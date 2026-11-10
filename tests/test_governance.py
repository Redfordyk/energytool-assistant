import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.database import (
    audit,
    lead_consents,
    lead_contexts,
    leads,
    make_engine,
    metadata,
    sessions,
)
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
    assert trace['escalation_reason'] == 'insufficient_knowledge'
    assert trace['knowledge_revision'] is None
    assert trace['evidence_ids'] == []
    assert trace['total_latency_ms'] >= 0
    assert (
        client.get(
            '/api/sessions/' + session['id'],
            headers={'Authorization': 'Bearer ' + session['token']},
        ).json()['state']
        == 'LEAD_OFFERED'
    )
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


def test_admin_lead_inbox_is_audited_and_hides_contacts_from_reviewer(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'lead-inbox.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setenv('ADMIN_TOKEN', 'lead-inbox-admin-secret')
    monkeypatch.setenv('REVIEWER_TOKEN', 'lead-inbox-reviewer-secret')
    with engine.begin() as db:
        db.execute(sessions.insert().values(id='lead-session', token_hash='session-token-hash'))
        lead_id = db.execute(
            leads.insert()
            .values(
                session_id='lead-session',
                request_key='lead-request-1',
                name='Анастасия Тест',
                phone='+79991234567',
                company='ООО Тестовая компания',
            )
            .returning(leads.c.id)
        ).scalar_one()
        db.execute(
            lead_contexts.insert().values(
                lead_id=lead_id,
                summary='Запрос на снижение затрат',
                knowledge_revision='a' * 64,
            )
        )
        db.execute(lead_consents.insert().values(lead_id=lead_id, purpose='demo_local_storage'))

    client = TestClient(main.app)
    endpoint = '/api/admin/leads'
    assert client.get(endpoint).status_code == 401
    assert (
        client.get(
            endpoint, headers={'Authorization': 'Bearer lead-inbox-reviewer-secret'}
        ).status_code
        == 403
    )

    response = client.get(
        endpoint + f'?limit=1&before_id={lead_id + 1}',
        headers={'Authorization': 'Bearer lead-inbox-admin-secret'},
    )
    assert response.status_code == 200
    row = response.json()[0]
    assert row['id'] == lead_id
    assert row['name'] == 'Анастасия Тест'
    assert row['phone'] == '+79991234567'
    assert row['company'] == 'ООО Тестовая компания'
    assert row['summary'] == 'Запрос на снижение затрат'
    assert row['consent_purpose'] == 'demo_local_storage'
    assert row['confirmed_at']
    assert (
        client.get(
            endpoint + '?limit=101', headers={'Authorization': 'Bearer lead-inbox-admin-secret'}
        ).status_code
        == 422
    )
    older_page = client.get(
        endpoint + f'?before_id={lead_id}',
        headers={'Authorization': 'Bearer lead-inbox-admin-secret'},
    )
    assert older_page.status_code == 200 and older_page.json() == []
    with engine.connect() as db:
        audit_events = (
            db.execute(audit.select().where(audit.c.action == 'lead.list')).mappings().all()
        )
        lead_review_event = (
            db.execute(
                audit.select().where(
                    audit.c.action == 'lead.review', audit.c.subject == str(lead_id)
                )
            )
            .mappings()
            .one()
        )
    assert len(audit_events) == 2
    assert {event['actor'] for event in audit_events} == {'admin'}
    assert lead_review_event['actor'] == 'admin'
