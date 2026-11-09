from fastapi.testclient import TestClient

from app import main
from sqlalchemy import insert, select
from app.database import lead_consents, lead_contexts, leads, make_engine, messages, metadata, sessions


def test_sessions_and_lead_confirmation(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    auth = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': '12345678'}
    assert client.get(url + '/messages').status_code == 404
    chat_auth = {**auth, 'Idempotency-Key': 'message-key-0001'}
    assert client.post(url + '/messages', headers=chat_auth, json={'text': 'Ищем снижение затрат', 'session_version': 0}).status_code == 200
    payload = {'name': 'Иван Иванов', 'phone': '8 999 123-45-67', 'company': 'Тест'}
    fake_confirmation = {'confirmation_token': 'x' * 32, 'consent': True}
    assert client.post(url + '/leads', headers=auth, json=fake_confirmation).status_code == 409
    prepared = client.post(url + '/lead-drafts', headers=auth, json=payload).json()
    assert prepared['phone'] == '+79991234567'
    assert 'Ищем снижение затрат' in prepared['summary']
    confirmation = {'confirmation_token': prepared['confirmation_token'], 'consent': False}
    assert client.post(url + '/leads', headers=auth, json=confirmation).status_code == 422
    assert client.post(url + '/leads', headers=auth, json={**confirmation, 'consent': 'true'}).status_code == 422
    with engine.connect() as db:
        assert db.execute(select(leads)).first() is None
    confirmation['consent'] = True
    first = client.post(url + '/leads', headers=auth, json=confirmation)
    assert first.status_code == 200
    assert client.post(url + '/leads', headers=auth, json=confirmation).json() == first.json()
    with engine.connect() as db:
        assert len(db.execute(select(lead_consents)).all()) == 1
        assert 'Ищем снижение затрат' in db.execute(select(lead_contexts.c.summary)).scalar_one()
    assert client.post(url + '/lead-drafts', headers=auth, json=payload).status_code == 409
    chat_auth['Idempotency-Key'] = 'message-key-0002'
    assert client.post(url + '/messages', headers=chat_auth, json={'text': 'Здравствуйте', 'session_version': 1}).status_code == 200
    assert len(client.get(url + '/messages', headers=auth).json()) == 4


def test_message_retries_version_conflicts_and_close(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'messages.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    headers = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': 'message-key-1'}
    payload = {'text': 'Здравствуйте', 'session_version': 0}
    first = client.post(url + '/messages', headers=headers, json=payload)
    assert first.status_code == 200 and first.json()['version'] == 1
    assert client.post(url + '/messages', headers=headers, json=payload).json() == first.json()
    assert client.post(url + '/messages', headers=headers, json={**payload, 'text': 'Другое'}).status_code == 409
    other_headers = {**headers, 'Idempotency-Key': 'message-key-2'}
    assert client.post(url + '/messages', headers=other_headers, json=payload).status_code == 409
    assert client.get(url, headers=headers).json()['version'] == 1
    first_page = client.get(url + '/messages?limit=1', headers=headers).json()
    assert len(first_page) == 1
    second_page = client.get(url + '/messages?limit=1&after_id=' + str(first_page[0]['id']), headers=headers).json()
    assert len(second_page) == 1 and second_page[0]['role'] == 'assistant'
    assert client.post(url + '/close', headers=headers).status_code == 200
    assert client.post(url + '/close', headers=headers).status_code == 200
    assert client.post(url + '/messages', headers=other_headers, json={'text': 'Вопрос', 'session_version': 2}).status_code == 409
    assert client.post(url + '/messages', headers=headers, json=payload).json() == first.json()
    draft = {'name': 'Тест Тест', 'phone': '+79991234567', 'company': 'Пример'}
    assert client.post(url + '/lead-drafts', headers=other_headers, json=draft).status_code == 409


def test_legacy_session_gets_version_from_existing_history(tmp_path, monkeypatch):
    import hashlib

    engine = make_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    with engine.begin() as db:
        db.execute(insert(sessions).values(id='legacy-session', token_hash=hashlib.sha256(b'legacy-token').hexdigest()))
        db.execute(insert(messages), [{'session_id': 'legacy-session', 'role': 'user', 'content': 'Привет'},
                                      {'session_id': 'legacy-session', 'role': 'assistant', 'content': 'Здравствуйте'}])
    client = TestClient(main.app)
    headers = {'Authorization': 'Bearer legacy-token', 'Idempotency-Key': 'legacy-message-1'}
    assert client.get('/api/sessions/legacy-session', headers=headers).json()['version'] == 1
    response = client.post('/api/sessions/legacy-session/messages', headers=headers,
                           json={'text': 'Привет', 'session_version': 1})
    assert response.status_code == 200 and response.json()['version'] == 2


def test_edited_draft_invalidates_old_confirmation(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    headers = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': '12345678'}
    fields = {'name': 'Иван Иванов', 'phone': '+79991234567', 'company': 'Первая'}
    old = client.post(url + '/lead-drafts', headers=headers, json=fields).json()
    fields['company'] = 'Другая'
    new = client.post(url + '/lead-drafts', headers=headers, json=fields).json()
    assert client.post(url + '/leads', headers=headers, json={'confirmation_token': old['confirmation_token'], 'consent': True}).status_code == 409
    assert client.post(url + '/leads', headers=headers, json={'confirmation_token': new['confirmation_token'], 'consent': True}).status_code == 200


def test_cancel_draft_prevents_confirmation(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    headers = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': '12345678'}
    prepared = client.post(url + '/lead-drafts', headers=headers, json={'name': 'Тест Тест', 'phone': '+79991234567', 'company': 'Пример'}).json()
    assert client.delete(url + '/lead-drafts', headers=headers).status_code == 200
    assert client.post(url + '/leads', headers=headers, json={'confirmation_token': prepared['confirmation_token'], 'consent': True}).status_code == 409


def test_draft_expiry_and_session_isolation(tmp_path, monkeypatch):
    from app import lead_service

    engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(lead_service.time, 'time', lambda: 1000)
    client = TestClient(main.app)
    first, second = client.post('/api/sessions').json(), client.post('/api/sessions').json()
    auth = lambda session: {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': '12345678'}
    url = lambda session: '/api/sessions/' + session['id']
    prepared = client.post(url(first) + '/lead-drafts', headers=auth(first), json={'name': 'Тест Тест', 'phone': '+79991234567', 'company': 'Пример'}).json()
    confirm = {'confirmation_token': prepared['confirmation_token'], 'consent': True}
    assert client.post(url(first) + '/leads', headers=auth(second), json=confirm).status_code == 404
    assert client.post(url(second) + '/leads', headers=auth(second), json=confirm).status_code == 409
    monkeypatch.setattr(lead_service.time, 'time', lambda: 2000)
    assert client.post(url(first) + '/leads', headers=auth(first), json=confirm).status_code == 409
    with engine.connect() as db:
        assert db.execute(select(leads)).first() is None
