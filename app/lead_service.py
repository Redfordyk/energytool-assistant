"""Prepare and confirm locally stored demonstration leads."""
import hashlib
import re
import secrets
import time

import phonenumbers
from fastapi import HTTPException
from sqlalchemy import delete, insert, select, text, update

from app.database import audit, lead_consents, lead_contexts, lead_drafts, leads, messages, session_states, settings

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
        lock_id = int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})


def require_open_session(db, session_id):
    closed = db.execute(select(session_states.c.closed).where(session_states.c.session_id == session_id)).scalar_one_or_none()
    if closed:
        raise HTTPException(409, 'Диалог закрыт')


def prepare(engine, session_id, key, name, phone, company):
    validate_key(key)
    name, company = name.strip(), company.strip()
    if len(name) < 2 or len(company) < 2:
        raise HTTPException(422, 'Проверьте имя и компанию')
    phone = normalize_phone(phone)
    confirmation = secrets.token_urlsafe(32)
    with engine.begin() as db:
        lock_session(db, session_id)
        require_open_session(db, session_id)
        db.execute(delete(lead_drafts).where(lead_drafts.c.session_id == session_id,
                   lead_drafts.c.lead_id.is_(None), lead_drafts.c.expires_at < int(time.time())))
        previous = db.execute(select(lead_drafts.c.lead_id).where(
            lead_drafts.c.session_id == session_id, lead_drafts.c.request_key == key)).first()
        if previous and previous.lead_id is not None:
            raise HTTPException(409, 'Заявка с этим ключом уже подтверждена')
        entries = db.execute(select(messages.c.content).where(
            messages.c.session_id == session_id, messages.c.role == 'user').order_by(messages.c.id.desc()).limit(4)).scalars().all()
        summary = '\n'.join(' '.join(entry.split())[:300] for entry in reversed(entries)) or 'Задача в диалоге не описана.'
        revision = db.execute(select(settings.c.value).where(settings.c.key == 'active_knowledge')).scalar_one_or_none()
        values = dict(session_id=session_id, request_key=key, name=name, phone=phone, company=company,
                      summary=summary, knowledge_revision=revision,
                      confirmation_hash=hashlib.sha256(confirmation.encode()).hexdigest(),
                      expires_at=int(time.time()) + DRAFT_LIFETIME_SECONDS)
        if previous:
            db.execute(update(lead_drafts).where(lead_drafts.c.session_id == session_id,
                                                 lead_drafts.c.request_key == key).values(**values))
        else:
            db.execute(insert(lead_drafts).values(**values))
    return {'name': name, 'phone': phone, 'company': company, 'summary': summary,
            'confirmation_token': confirmation, 'expires_in_seconds': DRAFT_LIFETIME_SECONDS}


def confirm(engine, session_id, key, token, consent):
    validate_key(key)
    if not consent:
        raise HTTPException(422, 'Необходимо подтвердить сохранение заявки')
    supplied = hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as db:
        lock_session(db, session_id)
        draft = db.execute(select(lead_drafts).where(
            lead_drafts.c.session_id == session_id, lead_drafts.c.request_key == key)).first()
        if draft is None or not secrets.compare_digest(draft.confirmation_hash, supplied):
            raise HTTPException(409, 'Подготовьте и проверьте заявку заново')
        if draft.lead_id is not None:
            return {'id': draft.lead_id, 'status': 'saved'}
        require_open_session(db, session_id)
        if draft.expires_at < int(time.time()):
            raise HTTPException(409, 'Время подтверждения истекло; проверьте заявку заново')
        lead_id = db.execute(insert(leads).values(session_id=session_id, request_key=key,
                             name=draft.name, phone=draft.phone, company=draft.company).returning(leads.c.id)).scalar_one()
        db.execute(insert(lead_consents).values(lead_id=lead_id, purpose='demo_local_storage'))
        db.execute(insert(lead_contexts).values(lead_id=lead_id, summary=draft.summary, knowledge_revision=draft.knowledge_revision))
        db.execute(update(lead_drafts).where(lead_drafts.c.session_id == session_id,
                   lead_drafts.c.request_key == key).values(lead_id=lead_id))
        db.execute(insert(audit).values(action='lead.create', actor='visitor', subject=str(lead_id)))
    return {'id': lead_id, 'status': 'saved'}


def cancel(engine, session_id, key):
    validate_key(key)
    with engine.begin() as db:
        lock_session(db, session_id)
        db.execute(delete(lead_drafts).where(lead_drafts.c.session_id == session_id,
                   lead_drafts.c.request_key == key, lead_drafts.c.lead_id.is_(None)))
    return {'status': 'cancelled'}
