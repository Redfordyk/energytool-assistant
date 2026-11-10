import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack

import httpx
import pytest
from sqlalchemy import select

from app import main, security
from app.database import make_engine, messages


def test_hundred_unrelated_session_locks_do_not_block_each_other():
    session_count = 100
    rendezvous = threading.Barrier(session_count)

    def enter_session(index):
        with main.session_lock(f'independent-session-{index}'):
            rendezvous.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=session_count) as pool:
        list(pool.map(enter_session, range(session_count)))
    assert not main.SESSION_LOCKS._entries


@pytest.mark.skipif(
    not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured'
)
def test_hundred_simultaneous_chats_respect_capacity_without_partial_writes(monkeypatch):
    request_count = 100
    chat_capacity = 4
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(security, 'consume', lambda *_args: (True, 60))
    monkeypatch.setattr(main, 'CHAT_SLOTS', threading.BoundedSemaphore(chat_capacity))
    started = threading.Event()
    release = threading.Event()
    overloaded = threading.Event()

    def slow_answer(*_args):
        started.set()
        assert release.wait(timeout=20)
        return 'Синтетический ответ'

    monkeypatch.setattr(main, 'answer', slow_answer)

    async def run_burst():
        async with AsyncExitStack() as stack:
            clients = []
            for index in range(request_count):
                transport = httpx.ASGITransport(
                    app=main.app, client=(f'198.51.100.{index + 1}', 50000)
                )
                clients.append(
                    await stack.enter_async_context(
                        httpx.AsyncClient(transport=transport, base_url='http://test')
                    )
                )

            session_responses = await asyncio.gather(
                *(client.post('/api/sessions') for client in clients)
            )
            assert all(response.status_code == 200 for response in session_responses)
            sessions = [response.json() for response in session_responses]

            start_burst = asyncio.Event()
            ready = 0
            overloaded_count = 0
            counter_lock = asyncio.Lock()

            async def send(client, session):
                nonlocal ready, overloaded_count
                async with counter_lock:
                    ready += 1
                    if ready == request_count:
                        start_burst.set()
                await start_burst.wait()
                response = await client.post(
                    '/api/sessions/' + session['id'] + '/messages',
                    headers={
                        'Authorization': 'Bearer ' + session['token'],
                        'Idempotency-Key': 'burst-message-0001',
                    },
                    json={'text': 'Синтетический вопрос', 'session_version': 0},
                )
                if response.status_code == 503:
                    async with counter_lock:
                        overloaded_count += 1
                        if overloaded_count == request_count - chat_capacity:
                            overloaded.set()
                return session, response

            results = await asyncio.gather(
                *(send(client, session) for client, session in zip(clients, sessions, strict=True))
            )
            return results

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, run_burst())
        try:
            assert started.wait(timeout=10), 'No chat request reached the generation gateway'
            assert overloaded.wait(timeout=15), 'Over-capacity requests did not fail promptly'
        finally:
            release.set()
        results = future.result(timeout=25)

    statuses = [response.status_code for _session, response in results]
    assert statuses.count(200) == chat_capacity
    assert statuses.count(503) == request_count - chat_capacity
    accepted_ids = {session['id'] for session, response in results if response.status_code == 200}
    with engine.connect() as db:
        rows = db.execute(
            select(messages.c.session_id, messages.c.role).where(
                messages.c.session_id.in_(accepted_ids)
            )
        ).all()
    assert len(rows) == chat_capacity * 2
    assert {row.session_id for row in rows} == accepted_ids
    engine.dispose()
