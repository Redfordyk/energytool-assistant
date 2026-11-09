import os
import secrets
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import make_engine
from app.lead_service import confirm
from app.security import consume


@pytest.mark.skipif(
    not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured'
)
def test_postgres_concurrent_lead_idempotency(monkeypatch):
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    headers = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': 'same-request-key'}
    draft = {'name': 'Тестовый Клиент', 'phone': '+79991234567', 'company': 'Пример'}
    prepared = client.post(url + '/lead-drafts', headers=headers, json=draft)
    assert prepared.status_code == 200
    lead = {'confirmation_token': prepared.json()['confirmation_token'], 'consent': True}
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(
                lambda _: confirm(
                    engine,
                    session['id'],
                    headers['Idempotency-Key'],
                    lead['confirmation_token'],
                    True,
                ),
                range(8),
            )
        )
    assert len({response['id'] for response in responses}) == 1
    assert client.post(url + '/leads', headers=headers, json=lead).json() == responses[0]
    engine.dispose()


@pytest.mark.skipif(
    not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured'
)
def test_postgres_knowledge_lifecycle(monkeypatch, tmp_path):
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setenv('ADMIN_TOKEN', 'test-postgres-admin')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('KNOWLEDGE_DIR', str(tmp_path / 'store'))
    with zipfile.ZipFile(tmp_path / 'knowledge-base.zip', 'w') as archive:
        archive.writestr(
            'product.md', '# Тест\nУникальные данные PostgreSQL ' + secrets.token_hex(8)
        )
    client = TestClient(main.app)
    headers = {'Authorization': 'Bearer test-postgres-admin'}
    root = '/api/admin/knowledge'
    response = client.post(root + '/register', headers=headers)
    assert response.status_code == 200
    revision = response.json()['sha256']
    assert client.post(f'{root}/{revision}/approve', headers=headers).status_code == 200
    assert client.post(f'{root}/{revision}/activate', headers=headers).status_code == 200
    assert client.post(f'{root}/{revision}/revoke', headers=headers).status_code == 200
    engine.dispose()


@pytest.mark.skipif(
    not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured'
)
def test_postgres_rate_limit_across_connections():
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    client = 'rate-test-' + secrets.token_hex(8)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(lambda _: consume(engine, client, 'messages', 5, now=1200), range(20))
        )
    assert sum(allowed for allowed, _ in results) == 5
    engine.dispose()


@pytest.mark.skipif(
    not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured'
)
def test_postgres_message_retries_across_workers(monkeypatch):
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'engine', engine)
    # Emulate separate workers: only PostgreSQL's advisory lock is shared.
    monkeypatch.setattr(main, 'session_lock', lambda _session: nullcontext())
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id'] + '/messages'
    headers = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': 'message-key-123'}
    payload = {'text': 'Здравствуйте', 'session_version': 0}
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda _: client.post(url, headers=headers, json=payload), range(8))
        )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()['answer'] for response in responses}) == 1
    assert len(client.get(url, headers=headers).json()) == 2
    engine.dispose()
