import os
import secrets
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import make_engine


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured')
def test_postgres_concurrent_lead_idempotency(monkeypatch):
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id'] + '/leads'
    headers = {'Authorization': 'Bearer ' + session['token']}
    lead = {'name': 'Тестовый Клиент', 'phone': '+79991234567', 'company': 'Пример', 'consent': True, 'request_key': 'same-request-key'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post(url, headers=headers, json=lead), range(8)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()['id'] for response in responses}) == 1
    engine.dispose()


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL test database not configured')
def test_postgres_knowledge_lifecycle(monkeypatch, tmp_path):
    engine = make_engine(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setenv('ADMIN_TOKEN', 'test-postgres-admin')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('KNOWLEDGE_DIR', str(tmp_path / 'store'))
    with zipfile.ZipFile(tmp_path / 'knowledge-base.zip', 'w') as archive:
        archive.writestr('product.md', '# Тест\nУникальные данные PostgreSQL ' + secrets.token_hex(8))
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
