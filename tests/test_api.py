from fastapi.testclient import TestClient

from app import main
from sqlalchemy import select
from app.database import lead_consents, make_engine, metadata


def test_sessions_and_lead_confirmation(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    metadata.create_all(engine)
    monkeypatch.setattr(main, 'engine', engine)
    client = TestClient(main.app)
    session = client.post('/api/sessions').json()
    url = '/api/sessions/' + session['id']
    auth = {'Authorization': 'Bearer ' + session['token']}
    assert client.get(url + '/messages').status_code == 404
    payload = {'name': 'Иван Иванов', 'phone': '+7 999 123-45-67', 'company': 'Тест', 'consent': False, 'request_key': '12345678'}
    assert client.post(url + '/leads', headers=auth, json=payload).status_code == 422
    payload['consent'] = True
    first = client.post(url + '/leads', headers=auth, json=payload)
    assert first.status_code == 200
    assert client.post(url + '/leads', headers=auth, json=payload).json() == first.json()
    with engine.connect() as db:
        assert len(db.execute(select(lead_consents)).all()) == 1
    payload['company'] = 'Другая'
    assert client.post(url + '/leads', headers=auth, json=payload).status_code == 409
    assert client.post(url + '/messages', headers=auth, json={'text': 'Здравствуйте'}).status_code == 200
    assert len(client.get(url + '/messages', headers=auth).json()) == 2
