import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def test_dialogue_state_migration_upgrades_legacy_schema(tmp_path, monkeypatch):
    database = tmp_path / 'legacy.db'
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
        INSERT INTO alembic_version VALUES ('0006');
        CREATE TABLE session_states (
            session_id VARCHAR(64) PRIMARY KEY,
            version INTEGER NOT NULL,
            closed BOOLEAN NOT NULL
        );
        INSERT INTO session_states VALUES ('closed-session', 2, 1);
        INSERT INTO session_states VALUES ('active-session', 3, 0);
        CREATE TABLE lead_drafts (
            session_id VARCHAR(64) NOT NULL,
            request_key VARCHAR(100) NOT NULL,
            name VARCHAR(120) NOT NULL,
            phone VARCHAR(30) NOT NULL,
            company VARCHAR(160) NOT NULL,
            summary TEXT NOT NULL,
            knowledge_revision VARCHAR(64),
            confirmation_hash VARCHAR(64) NOT NULL,
            expires_at INTEGER NOT NULL,
            lead_id INTEGER,
            PRIMARY KEY (session_id, request_key)
        );
        CREATE TABLE retrieval_traces (trace_id VARCHAR(64) PRIMARY KEY);
        INSERT INTO retrieval_traces VALUES ('legacy-trace');
        """
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(database).replace('\\', '/'))
    config = Config('alembic.ini')
    command.upgrade(config, 'head')

    engine = create_engine('sqlite:///' + str(database).replace('\\', '/'))
    with engine.connect() as db:
        states = dict(db.execute(text('SELECT session_id, state FROM session_states')).all())
        draft_columns = {row[1] for row in db.execute(text('PRAGMA table_info(lead_drafts)')).all()}
        trace = db.execute(
            text(
                'SELECT dialogue_state_before, dialogue_state_after, escalation_reason '
                "FROM retrieval_traces WHERE trace_id = 'legacy-trace'"
            )
        ).one()
        trace_columns = {
            row[1] for row in db.execute(text('PRAGMA table_info(retrieval_traces)')).all()
        }
    engine.dispose()

    assert states == {'closed-session': 'CLOSED', 'active-session': 'CONSULTING'}
    assert 'return_state' in draft_columns
    assert tuple(trace) == ('NEW', 'CONSULTING', None)
    assert 'escalation_reason' in trace_columns
