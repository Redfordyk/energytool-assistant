import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.database import (
    audit,
    knowledge,
    lead_consents,
    lead_contexts,
    lead_drafts,
    leads,
    make_engine,
    message_requests,
    messages,
    session_states,
    sessions,
    traces,
)
from app.dialogue import DialogueState, state_after_answer
from app.governance import (
    active_knowledge,
    active_knowledge_snapshot,
    change_revision,
    register,
    require_admin,
    require_role,
)
from app.lead_service import cancel as cancel_lead
from app.lead_service import cleanup_expired_drafts, lock_session, validate_key
from app.lead_service import confirm as confirm_lead
from app.lead_service import prepare as prepare_lead
from app.security import RequestGuard
from core.rag import answer

engine = make_engine()


def _expired_draft_cleanup_loop(stop):
    while not stop.is_set():
        try:
            cleanup_expired_drafts(engine)
        except SQLAlchemyError:
            logging.getLogger(__name__).warning('Expired lead draft cleanup failed')
        stop.wait(3600)


@asynccontextmanager
async def lifespan(_app):
    _app.state.cleanup_stop = threading.Event()
    worker = threading.Thread(
        target=_expired_draft_cleanup_loop,
        args=(_app.state.cleanup_stop,),
        name='expired-lead-draft-cleanup',
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        _app.state.cleanup_stop.set()
        worker.join(timeout=10)


app = FastAPI(title='Energytool consultant', lifespan=lifespan)
app.add_middleware(RequestGuard, engine_provider=lambda: engine)
app.mount('/assets', StaticFiles(directory=Path(__file__).with_name('static')), name='assets')
SESSION_LOCKS = tuple(threading.Lock() for _ in range(64))
CHAT_SLOTS = threading.BoundedSemaphore(4)


@app.exception_handler(SQLAlchemyError)
async def database_error(_request, _exc):
    return JSONResponse({'detail': 'Сервис временно недоступен'}, status_code=503)


def session_lock(session_id):
    index = int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:4], 'big')
    return SESSION_LOCKS[index % len(SESSION_LOCKS)]


def authorize(session_id, authorization):
    token = authorization[7:] if authorization.startswith('Bearer ') else ''
    digest = hashlib.sha256(token.encode()).hexdigest()
    with engine.connect() as db:
        stored = db.execute(
            select(sessions.c.token_hash).where(sessions.c.id == session_id)
        ).scalar_one_or_none()
    if not token or not stored or not secrets.compare_digest(stored, digest):
        raise HTTPException(404, 'Диалог не найден')


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    session_version: StrictInt = Field(ge=0)


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


@app.get('/health/live')
def liveness():
    return {'status': 'alive'}


@app.get('/health/ready')
def readiness():
    try:
        with engine.connect() as db:
            db.execute(text('SELECT 1'))
        configured = bool(
            (os.getenv('LLM_BASE_URL') or os.getenv('BASE_URL') or os.getenv('OPENAI_BASE_URL'))
            and (os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY'))
        )
        if configured and active_knowledge(engine):
            return {'status': 'ready'}
    except SQLAlchemyError:
        pass
    return JSONResponse({'status': 'not_ready'}, status_code=503)


@app.post('/api/sessions')
def create_session():
    session_id, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    with engine.begin() as db:
        db.execute(
            insert(sessions).values(
                id=session_id, token_hash=hashlib.sha256(token.encode()).hexdigest()
            )
        )
        db.execute(
            insert(session_states).values(
                session_id=session_id,
                version=0,
                closed=False,
                state=DialogueState.NEW.value,
            )
        )
    return {'id': session_id, 'token': token, 'version': 0, 'state': DialogueState.NEW.value}


def ensure_state(db, session_id):
    """Initialize versions for sessions created before the state migration."""
    state = db.execute(
        select(session_states).where(session_states.c.session_id == session_id)
    ).first()
    if state is not None:
        return state
    count = db.execute(
        select(func.count())
        .select_from(messages)
        .where(messages.c.session_id == session_id, messages.c.role == 'assistant')
    ).scalar_one()
    dialect_insert = pg_insert if db.dialect.name == 'postgresql' else sqlite_insert
    db.execute(
        dialect_insert(session_states)
        .values(
            session_id=session_id,
            version=count,
            closed=False,
            state=(DialogueState.CONSULTING if count else DialogueState.NEW).value,
        )
        .on_conflict_do_nothing(index_elements=['session_id'])
    )
    return db.execute(select(session_states).where(session_states.c.session_id == session_id)).one()


def read_history(db, session_id, after_id=0, limit=100):
    rows = db.execute(
        select(messages.c.id, messages.c.role, messages.c.content)
        .where(messages.c.session_id == session_id, messages.c.id > after_id)
        .order_by(messages.c.id)
        .limit(limit)
    )
    return [{'id': row.id, 'role': row.role, 'content': row.content} for row in rows]


def recent_history(db, session_id):
    rows = db.execute(
        select(messages.c.role, messages.c.content)
        .where(messages.c.session_id == session_id)
        .order_by(messages.c.id.desc())
        .limit(8)
    ).all()
    return [{'role': row.role, 'content': row.content} for row in reversed(rows)]


@app.get('/api/sessions/{session_id}')
def session_state(session_id: str, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    with session_lock(session_id), engine.begin() as db:
        lock_session(db, session_id)
        state = ensure_state(db, session_id)
        if state.state == DialogueState.LEAD_REVIEW.value:
            draft = db.execute(
                select(lead_drafts.c.return_state, lead_drafts.c.expires_at).where(
                    lead_drafts.c.session_id == session_id,
                    lead_drafts.c.lead_id.is_(None),
                )
            ).first()
            if draft is None or draft.expires_at < int(time.time()):
                next_state = (
                    draft.return_state if draft is not None else DialogueState.CONSULTING.value
                )
                db.execute(
                    update(session_states)
                    .where(session_states.c.session_id == session_id)
                    .values(state=next_state)
                )
                state = db.execute(
                    select(session_states).where(session_states.c.session_id == session_id)
                ).one()
        return {
            'id': session_id,
            'version': state.version,
            'closed': state.closed,
            'state': state.state,
        }


@app.post('/api/sessions/{session_id}/close')
def close_session(session_id: str, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    with session_lock(session_id), engine.begin() as db:
        lock_session(db, session_id)
        state = ensure_state(db, session_id)
        if not state.closed:
            db.execute(
                update(session_states)
                .where(session_states.c.session_id == session_id)
                .values(
                    closed=True,
                    version=state.version + 1,
                    state=DialogueState.CLOSED.value,
                )
            )
        return {'status': 'closed'}


@app.get('/api/sessions/{session_id}/messages')
def history(
    session_id: str,
    authorization: str = Header(default=''),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    authorize(session_id, authorization)
    with engine.connect() as db:
        return read_history(db, session_id, after_id, limit)


@app.post('/api/sessions/{session_id}/messages')
def chat(
    session_id: str,
    message: Message,
    request: Request,
    authorization: str = Header(default=''),
    idempotency_key: str = Header(default=''),
):
    started = time.perf_counter()
    authorize(session_id, authorization)
    validate_key(idempotency_key)
    request_id = request.scope.get('state', {}).get('request_id') or uuid.uuid4().hex
    question = message.text.strip()
    if not question:
        raise HTTPException(422, 'Введите сообщение')
    with session_lock(session_id), engine.begin() as db:
        lock_session(db, session_id)
        prior = db.execute(
            select(message_requests).where(
                message_requests.c.session_id == session_id,
                message_requests.c.request_key == idempotency_key,
            )
        ).first()
        if prior:
            if prior.question != question or prior.request_version != message.session_version:
                raise HTTPException(409, 'Ключ уже использован для другого сообщения')
            prior_trace = db.execute(
                select(traces.c.trace_id).where(
                    traces.c.session_id == session_id, traces.c.idempotency_key == idempotency_key
                )
            ).scalar_one_or_none()
            if prior_trace:
                request.scope.setdefault('state', {})['request_id'] = prior_trace
            return {
                'answer': prior.answer,
                'version': prior.result_version,
                'trace_id': prior_trace,
            }
        state = ensure_state(db, session_id)
        if state.closed:
            raise HTTPException(409, 'Диалог закрыт')
        if state.version != message.session_version:
            raise HTTPException(409, 'История изменилась; обновите диалог')
        if not CHAT_SLOTS.acquire(blocking=False):
            raise HTTPException(
                503, 'Сервис занят, повторите запрос позже', headers={'Retry-After': '5'}
            )
        try:
            previous = recent_history(db, session_id)
            knowledge_revision, chunks = active_knowledge_snapshot(engine)
            diagnostic: dict[str, object] = {}
            result = answer(question, chunks or (), previous, diagnostic)
            new_dialogue_state = state_after_answer(diagnostic)
            # Any new question invalidates a pending preview so consent always
            # applies to the latest dialogue summary.
            db.execute(
                delete(lead_drafts).where(
                    lead_drafts.c.session_id == session_id,
                    lead_drafts.c.lead_id.is_(None),
                )
            )
            db.execute(
                insert(messages),
                [
                    {'session_id': session_id, 'role': 'user', 'content': question},
                    {'session_id': session_id, 'role': 'assistant', 'content': result},
                ],
            )
            new_version = state.version + 1
            db.execute(
                insert(message_requests).values(
                    session_id=session_id,
                    request_key=idempotency_key,
                    question=question,
                    answer=result,
                    request_version=state.version,
                    result_version=new_version,
                )
            )
            db.execute(
                update(session_states)
                .where(session_states.c.session_id == session_id)
                .values(version=new_version, state=new_dialogue_state.value)
            )
            total_latency_ms = round((time.perf_counter() - started) * 1000)
            db.execute(
                insert(traces).values(
                    trace_id=request_id,
                    request_id=request_id,
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    intent=diagnostic.get('intent', 'knowledge_query'),
                    query_plan=diagnostic.get('query_plan', 'not_available'),
                    state_before=state.version,
                    state_after=new_version,
                    dialogue_state_before=state.state,
                    dialogue_state_after=new_dialogue_state.value,
                    retrieval_top_k=diagnostic.get('retrieval_top_k', 0),
                    reranker=diagnostic.get('reranker', 'not_run'),
                    evidence_ids=json.dumps(diagnostic.get('evidence_ids', []), ensure_ascii=False),
                    claim_verification=diagnostic.get('claim_verification'),
                    model_name=diagnostic.get('model_name'),
                    prompt_revision=diagnostic.get('prompt_revision', 'claims-json-v1'),
                    ttft_ms=diagnostic.get('ttft_ms'),
                    total_latency_ms=total_latency_ms,
                    prompt_token_count=diagnostic.get('prompt_token_count'),
                    completion_token_count=diagnostic.get('completion_token_count'),
                    fallback_reason=diagnostic.get('fallback_reason'),
                    knowledge_revision=knowledge_revision,
                )
            )
        finally:
            CHAT_SLOTS.release()
    return {'answer': result, 'version': new_version, 'trace_id': request_id}


@app.post('/api/sessions/{session_id}/lead-drafts')
def draft_lead(
    session_id: str,
    draft: LeadDraft,
    authorization: str = Header(default=''),
    idempotency_key: str = Header(default=''),
):
    authorize(session_id, authorization)
    with session_lock(session_id):
        return prepare_lead(
            engine, session_id, idempotency_key, draft.name, draft.phone, draft.company
        )


@app.post('/api/sessions/{session_id}/leads')
def lead(
    session_id: str,
    confirmation: LeadConfirmation,
    authorization: str = Header(default=''),
    idempotency_key: str = Header(default=''),
):
    authorize(session_id, authorization)
    with session_lock(session_id):
        return confirm_lead(
            engine,
            session_id,
            idempotency_key,
            confirmation.confirmation_token,
            confirmation.consent,
        )


@app.delete('/api/sessions/{session_id}/lead-drafts')
def delete_draft(
    session_id: str,
    authorization: str = Header(default=''),
    idempotency_key: str = Header(default=''),
):
    authorize(session_id, authorization)
    with session_lock(session_id):
        return cancel_lead(engine, session_id, idempotency_key)


@app.get('/api/admin/knowledge')
def knowledge_list(role: str = Depends(require_role)):
    with engine.connect() as db:
        rows = (
            db.execute(select(knowledge).order_by(knowledge.c.created_at.desc())).mappings().all()
        )
    return [dict(row) for row in rows]


@app.get('/api/admin/leads')
def admin_lead_list(
    role: str = Depends(require_role),
    before_id: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    require_admin(role)
    statement = (
        select(
            leads.c.id,
            leads.c.session_id,
            leads.c.name,
            leads.c.phone,
            leads.c.company,
            lead_contexts.c.summary,
            lead_contexts.c.knowledge_revision,
            lead_consents.c.purpose.label('consent_purpose'),
            lead_consents.c.confirmed_at,
        )
        .select_from(
            leads.join(lead_contexts, lead_contexts.c.lead_id == leads.c.id).join(
                lead_consents, lead_consents.c.lead_id == leads.c.id
            )
        )
        .order_by(leads.c.id.desc())
        .limit(limit)
    )
    if before_id:
        statement = statement.where(leads.c.id < before_id)
    with engine.begin() as db:
        result = db.execute(statement).mappings().all()
        db.execute(
            insert(audit).values(
                action='lead.list', actor=role, subject=f'before:{before_id};limit:{limit}'
            )
        )
        if result:
            db.execute(
                insert(audit),
                [
                    {'action': 'lead.review', 'actor': role, 'subject': str(row['id'])}
                    for row in result
                ],
            )
    return [dict(row) for row in result]


@app.get('/api/admin/traces/{trace_id}')
def debug_trace(trace_id: str, role: str = Depends(require_role)):
    with engine.begin() as db:
        row = db.execute(select(traces).where(traces.c.trace_id == trace_id)).mappings().first()
        if row is None:
            raise HTTPException(404, 'Трасса не найдена')
        db.execute(insert(audit).values(action='trace.review', actor=role, subject=trace_id))
    result = dict(row)
    result.pop('idempotency_key', None)
    result['evidence_ids'] = json.loads(result['evidence_ids'])
    return result


@app.get('/api/admin/sessions/{session_id}/messages')
def review_history(
    session_id: str,
    role: str = Depends(require_role),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    with engine.begin() as db:
        exists = db.execute(select(sessions.c.id).where(sessions.c.id == session_id)).first()
        if exists is None:
            raise HTTPException(404, 'Диалог не найден')
        result = read_history(db, session_id, after_id, limit)
        db.execute(insert(audit).values(action='session.review', actor=role, subject=session_id))
    if role == 'reviewer':
        return [
            {'id': item['id'], 'role': item['role'], 'characters': len(item['content'])}
            for item in result
        ]
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
