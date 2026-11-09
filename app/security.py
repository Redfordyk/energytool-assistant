"""Bound request sizes, shared rate limits, and browser security headers."""
import hashlib
import hmac
import os
import secrets
import time
import uuid

from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.database import rate_limits

MAX_BODY = 16 * 1024
WINDOW_SECONDS = 60


def policy(path, method):
    if path.startswith('/api/admin/'):
        return 'admin', 30
    if path == '/api/sessions' and method == 'POST':
        return 'sessions', 30
    if path.startswith('/api/sessions/') and path.endswith('/messages') and method == 'POST':
        return 'messages', 30
    if path.startswith('/api/'):
        return 'api', 120
    return None


def consume(engine, client, category, limit, now=None):
    """Atomically count one request in a fixed window across database clients."""
    now = int(time.time()) if now is None else now
    window = now // WINDOW_SECONDS
    secret = os.getenv('RATE_LIMIT_SALT', '')
    key = hmac.new(secret.encode(), (category + '\0' + client).encode(), hashlib.sha256).hexdigest()
    with engine.begin() as db:
        dialect_insert = pg_insert if db.dialect.name == 'postgresql' else sqlite_insert
        statement = dialect_insert(rate_limits).values(key=key, window=window, count=1)
        statement = statement.on_conflict_do_update(index_elements=['key'], set_={
            'window': window,
            'count': case((rate_limits.c.window == window, rate_limits.c.count + 1), else_=1),
        }).returning(rate_limits.c.count)
        count = db.execute(statement).scalar_one()
        if secrets.randbelow(512) == 0:
            db.execute(delete(rate_limits).where(rate_limits.c.window < window - 2))
    return count <= limit, WINDOW_SECONDS - (now % WINDOW_SECONDS)


class RequestGuard:
    def __init__(self, app, engine_provider):
        self.app = app
        self.engine_provider = engine_provider

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id

        async def secure_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                response_request_id = scope.get('state', {}).get('request_id') or request_id
                headers.extend([
                    (b'x-request-id', response_request_id.encode()),
                    (b'x-content-type-options', b'nosniff'),
                    (b'x-frame-options', b'DENY'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'cache-control', b'no-store'),
                    (b'content-security-policy', b"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ])
                message['headers'] = headers
            await send(message)

        route_policy = policy(scope['path'], scope['method'])
        if route_policy:
            client = scope.get('client')
            host = client[0] if client else 'unknown'
            try:
                allowed, retry = await run_in_threadpool(consume, self.engine_provider(), host, *route_policy)
            except SQLAlchemyError:
                response = JSONResponse({'detail': 'Сервис временно недоступен'}, status_code=503)
                await response(scope, receive, secure_send)
                return
            if not allowed:
                response = JSONResponse({'detail': 'Слишком много запросов'}, status_code=429,
                                        headers={'Retry-After': str(retry)})
                await response(scope, receive, secure_send)
                return

        content_lengths = [value for name, value in scope.get('headers', []) if name.lower() == b'content-length']
        if content_lengths:
            try:
                if len(content_lengths) != 1 or not content_lengths[0].isdigit():
                    raise ValueError('Invalid content length')
                if int(content_lengths[0]) > MAX_BODY:
                    response = JSONResponse({'detail': 'Слишком большой запрос'}, status_code=413)
                    await response(scope, receive, secure_send)
                    return
            except ValueError:
                response = JSONResponse({'detail': 'Некорректный запрос'}, status_code=400)
                await response(scope, receive, secure_send)
                return

        chunks = []
        size = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            if message['type'] != 'http.request':
                continue
            body = message.get('body', b'')
            size += len(body)
            if size > MAX_BODY:
                response = JSONResponse({'detail': 'Слишком большой запрос'}, status_code=413)
                await response(scope, receive, secure_send)
                return
            chunks.append(body)
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)
        first = True

        async def replay():
            nonlocal first
            if first:
                first = False
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, secure_send)
