"""Prepare and confirm locally stored demonstration leads."""

import hashlib
import re
import secrets
import time

import phonenumbers
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, text, update

from app.database import (
    audit,
    lead_consents,
    lead_contexts,
    lead_drafts,
    leads,
    messages,
    session_states,
    settings,
)
from app.dialogue import DialogueState

DRAFT_LIFETIME_SECONDS = 15 * 60
KEY_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,100}$')


def validate_key(key):
    if not KEY_PATTERN.fullmatch(key):
        raise HTTPException(422, 'Некорректный ключ идемпотентности')


def normalize_phone(value):
    if not re.fullmatch(r'[+\d ()-]{5,30}', value):
        raise HTTPException(422, 'Проверьте номер телефона')
    try:
        number = phonenumbers.parse(value, 'RU')
    except phonenumbers.NumberParseException as exc:
        raise HTTPException(422, 'Проверьте номер телефона') from exc
    if not phonenumbers.is_possible_number(number):
        raise HTTPException(422, 'Проверьте номер телефона')
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def lock_session(db, session_id):
    if db.dialect.name == 'postgresql':
        lock_id = int.from_bytes(
            hashlib.sha256(session_id.encode()).digest()[:8], 'big', signed=True
        )
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})


def require_open_session(db, session_id):
    state = db.execute(
        select(session_states).where(session_states.c.session_id == session_id)
    ).first()
    if state is None:
        version = db.execute(
            select(func.count())
            .select_from(messages)
            .where(messages.c.session_id == session_id, messages.c.role == 'assistant')
        ).scalar_one()
        initial_state = DialogueState.CONSULTING if version else DialogueState.NEW
        db.execute(
            insert(session_states).values(
                session_id=session_id,
                version=version,
                closed=False,
                state=initial_state.value,
            )
        )
        return initial_state.value
    if state.closed:
        raise HTTPException(409, 'Диалог закрыт')
    return state.state


def cleanup_expired_drafts(engine, now=None):
    cutoff = int(time.time()) if now is None else int(now)
    with engine.begin() as db:
        candidates = db.execute(
            select(lead_drafts.c.session_id, lead_drafts.c.request_key).where(
                lead_drafts.c.lead_id.is_(None), lead_drafts.c.expires_at < cutoff
            )
        ).all()
        removed = 0
        for candidate in candidates:
            lock_session(db, candidate.session_id)
            draft = db.execute(
                select(lead_drafts.c.return_state).where(
                    lead_drafts.c.session_id == candidate.session_id,
                    lead_drafts.c.request_key == candidate.request_key,
                    lead_drafts.c.lead_id.is_(None),
                    lead_drafts.c.expires_at < cutoff,
                )
            ).first()
            if draft is None:
                continue
            db.execute(
                update(session_states)
                .where(
                    session_states.c.session_id == candidate.session_id,
                    session_states.c.state == DialogueState.LEAD_REVIEW.value,
                    session_states.c.closed.is_(False),
                )
                .values(state=draft.return_state)
            )
            result = db.execute(
                delete(lead_drafts).where(
                    lead_drafts.c.session_id == candidate.session_id,
                    lead_drafts.c.request_key == candidate.request_key,
                    lead_drafts.c.lead_id.is_(None),
                    lead_drafts.c.expires_at < cutoff,
                )
            )
            removed += result.rowcount or 0
        return removed


def prepare(engine, session_id, key, name, phone, company):
    validate_key(key)
    name, company = name.strip(), company.strip()
    if len(name) < 2 or len(company) < 2:
        raise HTTPException(422, 'Проверьте имя и компанию')
    phone = normalize_phone(phone)
    confirmation = secrets.token_urlsafe(32)
    with engine.begin() as db:
        lock_session(db, session_id)
        current_state = require_open_session(db, session_id)
        db.execute(
            delete(lead_drafts).where(
                lead_drafts.c.lead_id.is_(None), lead_drafts.c.expires_at < int(time.time())
            )
        )
        previous = db.execute(
            select(lead_drafts.c.lead_id, lead_drafts.c.return_state).where(
                lead_drafts.c.session_id == session_id, lead_drafts.c.request_key == key
            )
        ).first()
        if previous and previous.lead_id is not None:
            raise HTTPException(409, 'Заявка с этим ключом уже подтверждена')
        entries = (
            db.execute(
                select(messages.c.content)
                .where(messages.c.session_id == session_id, messages.c.role == 'user')
                .order_by(messages.c.id.desc())
                .limit(4)
            )
            .scalars()
            .all()
        )
        summary = (
            '\n'.join(' '.join(entry.split())[:300] for entry in reversed(entries))
            or 'Задача в диалоге не описана.'
        )
        revision = db.execute(
            select(settings.c.value).where(settings.c.key == 'active_knowledge')
        ).scalar_one_or_none()
        return_state = (
            previous.return_state
            if previous and current_state == DialogueState.LEAD_REVIEW.value
            else current_state
        )
        values = {
            'session_id': session_id,
            'request_key': key,
            'name': name,
            'phone': phone,
            'company': company,
            'summary': summary,
            'knowledge_revision': revision,
            'confirmation_hash': hashlib.sha256(confirmation.encode()).hexdigest(),
            'expires_at': int(time.time()) + DRAFT_LIFETIME_SECONDS,
            'return_state': return_state,
        }
        if previous:
            db.execute(
                update(lead_drafts)
                .where(lead_drafts.c.session_id == session_id, lead_drafts.c.request_key == key)
                .values(**values)
            )
        else:
            db.execute(insert(lead_drafts).values(**values))
        db.execute(
            update(session_states)
            .where(session_states.c.session_id == session_id)
            .values(state=DialogueState.LEAD_REVIEW.value)
        )
    return {
        'name': name,
        'phone': phone,
        'company': company,
        'summary': summary,
        'confirmation_token': confirmation,
        'expires_in_seconds': DRAFT_LIFETIME_SECONDS,
    }


def confirm(engine, session_id, key, token, consent):
    validate_key(key)
    if not consent:
        raise HTTPException(422, 'Необходимо подтвердить сохранение заявки')
    supplied = hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as db:
        lock_session(db, session_id)
        draft = db.execute(
            select(lead_drafts).where(
                lead_drafts.c.session_id == session_id, lead_drafts.c.request_key == key
            )
        ).first()
        if draft is None or not secrets.compare_digest(draft.confirmation_hash, supplied):
            raise HTTPException(409, 'Подготовьте и проверьте заявку заново')
        if draft.lead_id is not None:
            return {'id': draft.lead_id, 'status': 'saved'}
        require_open_session(db, session_id)
        if draft.expires_at < int(time.time()):
            raise HTTPException(409, 'Время подтверждения истекло; проверьте заявку заново')
        lead_id = db.execute(
            insert(leads)
            .values(
                session_id=session_id,
                request_key=key,
                name=draft.name,
                phone=draft.phone,
                company=draft.company,
            )
            .returning(leads.c.id)
        ).scalar_one()
        db.execute(insert(lead_consents).values(lead_id=lead_id, purpose='demo_local_storage'))
        db.execute(
            insert(lead_contexts).values(
                lead_id=lead_id, summary=draft.summary, knowledge_revision=draft.knowledge_revision
            )
        )
        db.execute(
            update(lead_drafts)
            .where(lead_drafts.c.session_id == session_id, lead_drafts.c.request_key == key)
            .values(lead_id=lead_id)
        )
        db.execute(
            update(session_states)
            .where(session_states.c.session_id == session_id)
            .values(state=DialogueState.LEAD_CREATED.value)
        )
        db.execute(
            insert(audit).values(action='lead.create', actor='visitor', subject=str(lead_id))
        )
    return {'id': lead_id, 'status': 'saved'}


def cancel(engine, session_id, key):
    validate_key(key)
    with engine.begin() as db:
        lock_session(db, session_id)
        draft = db.execute(
            select(lead_drafts.c.return_state).where(
                lead_drafts.c.session_id == session_id,
                lead_drafts.c.request_key == key,
                lead_drafts.c.lead_id.is_(None),
            )
        ).first()
        db.execute(
            delete(lead_drafts).where(
                lead_drafts.c.session_id == session_id,
                lead_drafts.c.request_key == key,
                lead_drafts.c.lead_id.is_(None),
            )
        )
        if draft:
            db.execute(
                update(session_states)
                .where(
                    session_states.c.session_id == session_id,
                    session_states.c.closed.is_(False),
                )
                .values(state=draft.return_state)
            )
    return {'status': 'cancelled'}
