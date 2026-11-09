from fastapi.testclient import TestClient

from app import main
from sqlalchemy import select
from app.database import lead_consents, lead_contexts, leads, make_engine, metadata


def test_sessions_and_lead_confirmation(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    auth = {'Authorization': 'Bearer ' + session['token'], 'Idempotency-Key': '12345678'}
    assert client.get(url + '/messages').status_code == 404
    assert client.post(url + '/messages', headers=auth, json={'text': 'Ищем снижение затрат'}).status_code == 200
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
    assert client.post(url + '/messages', headers=auth, json={'text': 'Здравствуйте'}).status_code == 200
    assert len(client.get(url + '/messages', headers=auth).json()) == 4


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
