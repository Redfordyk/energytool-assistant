"""Governance of immutable knowledge archives and privileged operations."""
import hashlib
import hmac
import os
import re
import tempfile
import zipfile
from pathlib import Path

from fastapi import Header, HTTPException
from sqlalchemy import delete, insert, select, update, func, text

from app.database import audit, knowledge, settings
from core.rag import load_knowledge


def require_role(authorization: str = Header(default='')):
    if not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Требуется авторизация')
    token = authorization[7:]
    if not token:
        raise HTTPException(401, 'Требуется авторизация')
    for role, variable in (('admin', 'ADMIN_TOKEN'), ('reviewer', 'REVIEWER_TOKEN')):
        expected = os.getenv(variable, '')
        if expected and hmac.compare_digest(token, expected):
            return role
    raise HTTPException(401, 'Недействительный токен')


def require_admin(role):
    if role != 'admin':
        raise HTTPException(403, 'Недостаточно прав')


def archive_root():
    return Path(os.getenv('KNOWLEDGE_DIR', 'knowledge_store'))


def archive_path(revision):
    if not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise HTTPException(404, 'Версия не найдена')
    return archive_root() / (revision + '.zip')


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def register(engine, actor):
    source = Path(os.getenv('DATA_DIR', 'data')) / 'knowledge-base.zip'
    if not source.is_file() or source.stat().st_size > 50_000_000:
        raise HTTPException(400, 'Исходный архив отсутствует или превышает лимит')
    root = archive_root()
    root.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        digest = hashlib.sha256()
        size = 0
        with source.open('rb') as src, tempfile.NamedTemporaryFile(dir=root, delete=False) as target:
            temp_name = target.name
            for block in iter(lambda: src.read(1024 * 1024), b''):
                size += len(block)
                if size > 50_000_000:
                    raise ValueError('Archive exceeds limit')
                digest.update(block)
                target.write(block)
        revision = digest.hexdigest()
        # Validate the exact bytes that will be activated, not the mutable input.
        chunks = load_knowledge(temp_name)
        destination = archive_path(revision)
        os.replace(temp_name, destination)
        temp_name = None
        with engine.begin() as db:
            if db.execute(select(knowledge.c.sha256).where(knowledge.c.sha256 == revision)).first() is None:
                db.execute(insert(knowledge).values(sha256=revision, status='pending', created_by=actor))
                db.execute(insert(audit).values(action='knowledge.register', actor=actor, subject=revision))
        return {'sha256': revision, 'chunks': len(chunks)}
    except HTTPException:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(400, 'Некорректный архив базы знаний') from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def lock_changes(db):
    if db.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(19533001)'))


def change_revision(engine, revision, actor, action):
    path = archive_path(revision)
    with engine.begin() as db:
        lock_changes(db)
        row = db.execute(select(knowledge.c.status).where(knowledge.c.sha256 == revision)).first()
        if row is None:
            raise HTTPException(404, 'Версия не найдена')
        if action in ('approve', 'activate'):
            try:
                valid = file_hash(path) == revision
            except OSError:
                valid = False
            if not valid:
                raise HTTPException(409, 'Архив отсутствует или изменён')
        if action == 'approve':
            if row.status != 'pending':
                raise HTTPException(409, 'Разрешено утверждать только ожидающую версию')
            db.execute(update(knowledge).where(knowledge.c.sha256 == revision).values(status='approved', approved_by=actor, approved_at=func.now()))
        elif action == 'activate':
            if row.status != 'approved':
                raise HTTPException(409, 'Версия не утверждена')
            db.execute(delete(settings).where(settings.c.key == 'active_knowledge'))
            db.execute(insert(settings).values(key='active_knowledge', value=revision))
        elif action == 'revoke':
            db.execute(update(knowledge).where(knowledge.c.sha256 == revision).values(status='revoked'))
            db.execute(delete(settings).where(settings.c.key == 'active_knowledge', settings.c.value == revision))
        else:
            raise ValueError('Unknown operation')
        db.execute(insert(audit).values(action='knowledge.' + action, actor=actor, subject=revision))


def active_knowledge(engine):
    revision, chunks = active_knowledge_snapshot(engine)
    return chunks if revision is not None and chunks else None


def active_knowledge_snapshot(engine):
    """Return the revision and chunks from the same immutable archive snapshot."""
    with engine.connect() as db:
        revision = db.execute(select(settings.c.value).join(knowledge, knowledge.c.sha256 == settings.c.value).where(
            settings.c.key == 'active_knowledge', knowledge.c.status == 'approved')).scalar_one_or_none()
    if revision is None:
        return None, ()
    path = archive_path(revision)
    try:
        if file_hash(path) != revision:
            return None, ()
        return revision, load_knowledge(path)
    except (OSError, ValueError, zipfile.BadZipFile):
        return None, ()
