import hashlib
import re
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import audit, knowledge, lead_consents, leads, make_engine, messages, sessions
from app.governance import active_knowledge, change_revision, register, require_admin, require_role
from core.rag import FALLBACK, answer

app = FastAPI(title='Energytool consultant')
engine = make_engine()
SESSION_LOCKS = tuple(threading.Lock() for _ in range(64))


def session_lock(session_id):
    index = int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:4], 'big')
    return SESSION_LOCKS[index % len(SESSION_LOCKS)]


def authorize(session_id, authorization):
    token = authorization[7:] if authorization.startswith('Bearer ') else ''
    digest = hashlib.sha256(token.encode()).hexdigest()
    with engine.connect() as db:
        stored = db.execute(select(sessions.c.token_hash).where(sessions.c.id == session_id)).scalar_one_or_none()
    if not token or not stored or not secrets.compare_digest(stored, digest):
        raise HTTPException(404, 'Диалог не найден')


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Lead(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=5, max_length=30)
    company: str = Field(min_length=2, max_length=160)
    consent: bool
    request_key: str = Field(min_length=8, max_length=100)


@app.get('/')
def index():
    return FileResponse(Path(__file__).with_name('index.html'))


@app.get('/admin')
def admin_page():
    return FileResponse(Path(__file__).with_name('admin.html'))


@app.post('/api/sessions')
def create_session():
    session_id, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    with engine.begin() as db:
        db.execute(insert(sessions).values(id=session_id, token_hash=hashlib.sha256(token.encode()).hexdigest()))
    return {'id': session_id, 'token': token}


def read_history(db, session_id):
    rows = db.execute(select(messages.c.role, messages.c.content).where(messages.c.session_id == session_id).order_by(messages.c.id))
    return [{'role': row.role, 'content': row.content} for row in rows]


@app.get('/api/sessions/{session_id}/messages')
def history(session_id: str, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    with engine.connect() as db:
        return read_history(db, session_id)


@app.post('/api/sessions/{session_id}/messages')
def chat(session_id: str, message: Message, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    question = message.text.strip()
    if not question:
        raise HTTPException(422, 'Введите сообщение')
    with session_lock(session_id), engine.begin() as db:
        if db.dialect.name == 'postgresql':
            # The database lock serializes the session across application workers.
            db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], 'big', signed=True)})
        previous = read_history(db, session_id)
        chunks = active_knowledge(engine)
        result = answer(question, chunks, previous) if chunks else FALLBACK
        db.execute(insert(messages), [{'session_id': session_id, 'role': 'user', 'content': question},
                                      {'session_id': session_id, 'role': 'assistant', 'content': result}])
    return {'answer': result}


@app.post('/api/sessions/{session_id}/leads')
def lead(session_id: str, lead: Lead, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    if not lead.consent or not re.fullmatch(r'[+\d ()-]{5,30}', lead.phone) or not 10 <= len(re.sub(r'\D', '', lead.phone)) <= 15 or not lead.name.strip() or not lead.company.strip():
        raise HTTPException(422, 'Проверьте контакты и подтвердите согласие')
    values = dict(session_id=session_id, request_key=lead.request_key, name=lead.name.strip(), phone=lead.phone.strip(), company=lead.company.strip())
    with engine.begin() as db:
        dialect_insert = pg_insert if db.dialect.name == 'postgresql' else sqlite_insert
        inserted = db.execute(dialect_insert(leads).values(**values).on_conflict_do_nothing(index_elements=['session_id', 'request_key']))
        row = db.execute(select(leads).where(leads.c.session_id == session_id, leads.c.request_key == lead.request_key)).one()
        if (row.name, row.phone, row.company) != (values['name'], values['phone'], values['company']):
            raise HTTPException(409, 'Ключ уже использован для другой заявки')
        lead_id = row.id
        if inserted.rowcount == 1:
            db.execute(insert(lead_consents).values(lead_id=lead_id, purpose='demo_local_storage'))
    return {'id': lead_id, 'status': 'saved'}


@app.get('/api/admin/knowledge')
def knowledge_list(role: str = Depends(require_role)):
    with engine.connect() as db:
        rows = db.execute(select(knowledge).order_by(knowledge.c.created_at.desc())).mappings().all()
    return [dict(row) for row in rows]


@app.get('/api/admin/sessions/{session_id}/messages')
def review_history(session_id: str, role: str = Depends(require_role)):
    with engine.begin() as db:
        exists = db.execute(select(sessions.c.id).where(sessions.c.id == session_id)).first()
        if exists is None:
            raise HTTPException(404, 'Диалог не найден')
        result = read_history(db, session_id)
        db.execute(insert(audit).values(action='session.review', actor=role, subject=session_id))
    return result


@app.post('/api/admin/knowledge/register')
def knowledge_register(role: str = Depends(require_role)):
    require_admin(role)
    return register(engine, role)


@app.post('/api/admin/knowledge/{revision}/{action}')
def knowledge_action(revision: str, action: str, role: str = Depends(require_role)):
    require_admin(role)
    if action not in ('approve', 'activate', 'revoke'):
        raise HTTPException(404, 'Действие не найдено')
    change_revision(engine, revision, role, action)
    return {'sha256': revision, 'action': action}
