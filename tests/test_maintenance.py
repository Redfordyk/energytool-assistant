from sqlalchemy import insert, select

from app.database import lead_drafts, leads, make_engine, metadata, sessions
from app.lead_service import cleanup_expired_drafts


def test_expired_draft_cleanup_removes_only_unconfirmed_expired_records(tmp_path):
    engine = make_engine('sqlite:///' + str(tmp_path / 'cleanup.db'))
    metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(
            insert(sessions),
            [
                {'id': session_id, 'token_hash': (str(index + 1) * 64)}
                for index, session_id in enumerate(('expired', 'future', 'confirmed'))
            ],
        )
        lead_id = db.execute(
            insert(leads)
            .values(
                session_id='confirmed',
                request_key='saved-lead',
                name='Test User',
                phone='+79991234567',
                company='Test Co',
            )
            .returning(leads.c.id)
        ).scalar_one()
        db.execute(
            insert(lead_drafts),
            [
                {
                    'session_id': 'expired',
                    'request_key': 'expired-draft',
                    'name': 'Test User',
                    'phone': '+79991234567',
                    'company': 'Test Co',
                    'summary': 'test',
                    'confirmation_hash': 'a' * 64,
                    'expires_at': 99,
                    'lead_id': None,
                },
                {
                    'session_id': 'future',
                    'request_key': 'future-draft',
                    'name': 'Test User',
                    'phone': '+79991234567',
                    'company': 'Test Co',
                    'summary': 'test',
                    'confirmation_hash': 'b' * 64,
                    'expires_at': 101,
                    'lead_id': None,
                },
                {
                    'session_id': 'confirmed',
                    'request_key': 'saved-lead',
                    'name': 'Test User',
                    'phone': '+79991234567',
                    'company': 'Test Co',
                    'summary': 'test',
                    'confirmation_hash': 'c' * 64,
                    'expires_at': 99,
                    'lead_id': lead_id,
                },
            ],
        )

    assert cleanup_expired_drafts(engine, now=100) == 1
    with engine.connect() as db:
        remaining = set(db.execute(select(lead_drafts.c.request_key)).scalars())
    assert remaining == {'future-draft', 'saved-lead'}
    assert cleanup_expired_drafts(engine, now=100) == 0
