import hashlib
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import insert, select

from app.database import audit, knowledge, make_engine, messages, sessions
from app.governance import active_knowledge, change_revision, register, require_admin, require_role
from app.lead_service import cancel as cancel_lead, confirm as confirm_lead, lock_session, prepare as prepare_lead
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


class LeadDraft(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=5, max_length=30)
    company: str = Field(min_length=2, max_length=160)


class LeadConfirmation(BaseModel):
    confirmation_token: str = Field(min_length=32, max_length=128)
    consent: StrictBool


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
        lock_session(db, session_id)
        previous = read_history(db, session_id)
        chunks = active_knowledge(engine)
        result = answer(question, chunks, previous) if chunks else FALLBACK
        db.execute(insert(messages), [{'session_id': session_id, 'role': 'user', 'content': question},
                                      {'session_id': session_id, 'role': 'assistant', 'content': result}])
    return {'answer': result}


@app.post('/api/sessions/{session_id}/lead-drafts')
def draft_lead(session_id: str, draft: LeadDraft, authorization: str = Header(default=''),
               idempotency_key: str = Header(default='')):
    authorize(session_id, authorization)
    with session_lock(session_id):
        return prepare_lead(engine, session_id, idempotency_key, draft.name, draft.phone, draft.company)


@app.post('/api/sessions/{session_id}/leads')
def lead(session_id: str, confirmation: LeadConfirmation, authorization: str = Header(default=''),
         idempotency_key: str = Header(default='')):
    authorize(session_id, authorization)
    with session_lock(session_id):
        return confirm_lead(engine, session_id, idempotency_key, confirmation.confirmation_token, confirmation.consent)


@app.delete('/api/sessions/{session_id}/lead-drafts')
def delete_draft(session_id: str, authorization: str = Header(default=''), idempotency_key: str = Header(default='')):
    authorize(session_id, authorization)
    with session_lock(session_id):
        return cancel_lead(engine, session_id, idempotency_key)


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
