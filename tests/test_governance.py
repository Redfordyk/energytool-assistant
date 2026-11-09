import zipfile

from fastapi.testclient import TestClient

from app import main
from app.database import make_engine, metadata
from app.governance import active_knowledge, archive_path


def test_knowledge_lifecycle_and_access(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'governance.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setenv('ADMIN_TOKEN', 'test-admin-secret')
    monkeypatch.setenv('REVIEWER_TOKEN', 'test-reviewer-secret')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('KNOWLEDGE_DIR', str(tmp_path / 'store'))
    with zipfile.ZipFile(tmp_path / 'knowledge-base.zip', 'w') as archive:
        archive.writestr('product.md', '# Тест\nПроверяемые сведения о продукте.')
    client = TestClient(main.app)
    admin = {'Authorization': 'Bearer test-admin-secret'}
    reviewer = {'Authorization': 'Bearer test-reviewer-secret'}
    root = '/api/admin/knowledge'
    assert client.get(root).status_code == 401
    session = client.post('/api/sessions').json()
    session_history = '/api/admin/sessions/' + session['id'] + '/messages'
    assert client.get(session_history).status_code == 401
    client.post('/api/sessions/' + session['id'] + '/messages', headers={'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': 'message-key-1'}, json={'text': 'Приватный текст', 'session_version': 0})
    assert 'Приватный текст' not in client.get(session_history, headers=reviewer).text
    assert 'Приватный текст' in client.get(session_history, headers=admin).text
    assert client.post(root + '/register', headers=reviewer).status_code == 403
    revision = client.post(root + '/register', headers=admin).json()['sha256']
    assert active_knowledge(engine) is None
    assert client.post(f'{root}/{revision}/activate', headers=admin).status_code == 409
    assert client.post(f'{root}/{revision}/approve', headers=admin).status_code == 200
    assert client.post(f'{root}/{revision}/activate', headers=admin).status_code == 200
    assert active_knowledge(engine)[0].source == 'product.md'
    assert client.get(root, headers=reviewer).json()[0]['status'] == 'approved'
    archive_path(revision).write_bytes(b'tampered')
    assert active_knowledge(engine) is None
    assert client.post(f'{root}/{revision}/activate', headers=admin).status_code == 409
    assert client.post(f'{root}/{revision}/revoke', headers=admin).status_code == 200
    assert active_knowledge(engine) is None
