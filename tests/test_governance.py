import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.database import audit, make_engine, metadata
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
    client.post(
        '/api/sessions/' + session['id'] + '/messages',
        headers={'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': 'message-key-1'},
        json={'text': 'Приватный текст', 'session_version': 0},
    )
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


def test_chat_trace_is_retrievable_by_reviewer_without_message_text(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'traces.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setenv('ADMIN_TOKEN', 'trace-admin-secret')
    monkeypatch.setenv('REVIEWER_TOKEN', 'trace-reviewer-secret')
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    chat = client.post(
        '/api/sessions/' + session['id'] + '/messages',
        headers={
            'Authorization': 'Bearer ' + session['token'],
            'Idempotency-Key': 'trace-message-1',
        },
        json={'text': 'Личный вопрос без настроенной базы', 'session_version': 0},
    )
    assert chat.status_code == 200
    trace_id = chat.json()['trace_id']
    assert trace_id == chat.headers['x-request-id']

    reviewer = client.get(
        '/api/admin/traces/' + trace_id, headers={'Authorization': 'Bearer trace-reviewer-secret'}
    )
    assert reviewer.status_code == 200
    trace = reviewer.json()
    assert trace['trace_id'] == trace_id
    assert trace['state_before'] == 0 and trace['state_after'] == 1
    assert trace['fallback_reason'] == 'no_active_knowledge'
    assert trace['knowledge_revision'] is None
    assert trace['evidence_ids'] == []
    assert trace['total_latency_ms'] >= 0
    assert 'idempotency_key' not in trace
    assert 'Личный вопрос без настроенной базы' not in reviewer.text
    assert client.get('/api/admin/traces/' + trace_id).status_code == 401

    retry = client.post(
        '/api/sessions/' + session['id'] + '/messages',
        headers={
            'Authorization': 'Bearer ' + session['token'],
            'Idempotency-Key': 'trace-message-1',
        },
        json={'text': 'Личный вопрос без настроенной базы', 'session_version': 0},
    )
    assert retry.json()['trace_id'] == trace_id
    assert retry.headers['x-request-id'] == trace_id
    with engine.connect() as db:
        assert (
            db.execute(select(audit.c.action).where(audit.c.action == 'trace.review')).scalar_one()
            == 'trace.review'
        )
