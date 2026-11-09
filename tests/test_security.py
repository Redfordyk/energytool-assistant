import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app import main, security
from app.database import make_engine, metadata
from app.security import consume


def test_rate_limit_is_atomic_and_resets(tmp_path):
    engine = make_engine('sqlite:///' + str(tmp_path / 'limit.db'))
    metadata.create_all(engine)
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(
            pool.map(lambda _: consume(engine, 'test-client', 'messages', 5, now=600), range(10))
        )
    assert sum(allowed for allowed, _ in responses) == 5
    assert consume(engine, 'test-client', 'messages', 5, now=660)[0]
    assert consume(engine, 'other-client', 'messages', 5, now=600)[0]


def test_http_security_headers_and_body_limit(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'http.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    page = client.get('/')
    assert page.status_code == 200
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert "'unsafe-inline'" not in page.headers['content-security-policy']
    assert page.headers['x-frame-options'] == 'DENY'
    assert page.headers['cache-control'] == 'no-store'
    assert client.get('/assets/index.js').status_code == 200
    assert client.get('/health/live').json() == {'status': 'alive'}
    assert client.post('/api/sessions', content=b'x' * 16385).status_code == 413
    assert client.post('/api/sessions', content=iter([b'x' * 9000, b'y' * 9000])).status_code == 413


def test_http_rate_limit_and_readiness(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'http.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert client.get('/health/ready').status_code == 503
    for _ in range(30):
        assert client.post('/api/sessions').status_code == 200
    response = client.post('/api/sessions', headers={'X-Forwarded-For': '1.2.3.4'})
    assert response.status_code == 429
    assert int(response.headers['retry-after']) > 0


def test_rate_limit_storage_failure_returns_generic_503(tmp_path, monkeypatch):
    from app import security

    engine = make_engine('sqlite:///' + str(tmp_path / 'http.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)

    def fail(*_args):
        raise SQLAlchemyError('private database detail')

    monkeypatch.setattr(security, 'consume', fail)
    response = TestClient(main.app).post('/api/sessions')
    assert response.status_code == 503
    assert 'private database detail' not in response.text


def test_overloaded_chat_rejects_without_recording_message(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'busy.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    # Keep this test focused on the in-process overload guard. SQLite serializes
    # the middleware's rate-limit writes, while PostgreSQL handles them concurrently.
    monkeypatch.setattr(security, 'consume', lambda *_args: (True, 60))
    monkeypatch.setattr(main, 'CHAT_SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(main, 'active_knowledge', lambda _engine: ())
    started, release = threading.Event(), threading.Event()

    def slow_answer(*_args):
        started.set()
        # SQLite may wait up to its configured 10-second lock timeout before
        # the second request reaches the process-level overload guard.
        assert release.wait(timeout=15)
        return 'Ответ'

    monkeypatch.setattr(main, 'answer', slow_answer)
    client = TestClient(main.app)
    first, second = client.post('/api/sessions').json(), client.post('/api/sessions').json()

    def request(session):
        return client.post(
            '/api/sessions/' + session['id'] + '/messages',
            headers={
                'Authorization': 'Bearer ' + session['token'],
                'Idempotency-Key': 'message-key-1',
            },
            json={'text': 'Вопрос', 'session_version': 0},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(request, first)
        assert started.wait(timeout=5)
        try:
            response = request(second)
            assert response.status_code == 503
            assert response.headers['retry-after'] == '5'
            history = client.get(
                '/api/sessions/' + second['id'] + '/messages',
                headers={'Authorization': 'Bearer ' + second['token']},
            )
            assert history.json() == []
        finally:
            release.set()
        assert future.result(timeout=20).status_code == 200
