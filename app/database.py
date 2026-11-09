"""Database schema and connections shared by the web application and migrations."""

import os

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
)

metadata = MetaData()
sessions = Table(
    'sessions',
    metadata,
    Column('id', String(64), primary_key=True),
    Column('token_hash', String(64), nullable=False),
)
session_states = Table(
    'session_states',
    metadata,
    Column('session_id', String(64), ForeignKey('sessions.id'), primary_key=True),
    Column('version', Integer, nullable=False, default=0),
    Column('closed', Boolean, nullable=False, default=False),
    Column('state', String(32), nullable=False, default='NEW', server_default='NEW'),
)
message_requests = Table(
    'message_requests',
    metadata,
    Column('session_id', String(64), ForeignKey('sessions.id'), primary_key=True),
    Column('request_key', String(100), primary_key=True),
    Column('question', Text, nullable=False),
    Column('answer', Text, nullable=False),
    Column('request_version', Integer, nullable=False),
    Column('result_version', Integer, nullable=False),
)
messages = Table(
    'messages',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('session_id', String(64), ForeignKey('sessions.id'), nullable=False),
    Column('role', String(16), nullable=False),
    Column('content', Text, nullable=False),
)
leads = Table(
    'leads',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('session_id', String(64), ForeignKey('sessions.id'), nullable=False),
    Column('request_key', String(100), nullable=False),
    Column('name', String(120), nullable=False),
    Column('phone', String(30), nullable=False),
    Column('company', String(160), nullable=False),
    UniqueConstraint('session_id', 'request_key', name='uq_leads_session_request'),
)
lead_consents = Table(
    'lead_consents',
    metadata,
    Column('lead_id', Integer, ForeignKey('leads.id'), primary_key=True),
    Column('purpose', String(64), nullable=False),
    Column('confirmed_at', DateTime(timezone=True), server_default=func.now(), nullable=False),
)
lead_drafts = Table(
    'lead_drafts',
    metadata,
    Column('session_id', String(64), ForeignKey('sessions.id'), primary_key=True),
    Column('request_key', String(100), primary_key=True),
    Column('name', String(120), nullable=False),
    Column('phone', String(30), nullable=False),
    Column('company', String(160), nullable=False),
    Column('summary', Text, nullable=False),
    Column('knowledge_revision', String(64)),
    Column('confirmation_hash', String(64), nullable=False),
    Column('expires_at', Integer, nullable=False),
    Column(
        'return_state',
        String(32),
        nullable=False,
        default='CONSULTING',
        server_default='CONSULTING',
    ),
    Column('lead_id', Integer, ForeignKey('leads.id')),
)
lead_contexts = Table(
    'lead_contexts',
    metadata,
    Column('lead_id', Integer, ForeignKey('leads.id'), primary_key=True),
    Column('summary', Text, nullable=False),
    Column('knowledge_revision', String(64)),
)
knowledge = Table(
    'knowledge_revisions',
    metadata,
    Column('sha256', String(64), primary_key=True),
    Column('status', String(16), nullable=False),
    Column('created_at', DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column('approved_at', DateTime(timezone=True)),
    Column('created_by', String(16), nullable=False),
    Column('approved_by', String(16)),
)
settings = Table(
    'application_settings',
    metadata,
    Column('key', String(64), primary_key=True),
    Column('value', String(128), nullable=False),
)
audit = Table(
    'audit_events',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('action', String(40), nullable=False),
    Column('actor', String(16), nullable=False),
    Column('subject', String(64), nullable=False),
    Column('created_at', DateTime(timezone=True), server_default=func.now(), nullable=False),
)
traces = Table(
    'retrieval_traces',
    metadata,
    Column('trace_id', String(64), primary_key=True),
    Column('request_id', String(64), nullable=False),
    Column('session_id', String(64), ForeignKey('sessions.id'), nullable=False),
    Column('idempotency_key', String(100), nullable=False),
    Column('intent', String(40), nullable=False),
    Column('query_plan', String(80), nullable=False),
    Column('state_before', Integer, nullable=False),
    Column('state_after', Integer, nullable=False),
    Column('dialogue_state_before', String(32), nullable=False, default='NEW'),
    Column('dialogue_state_after', String(32), nullable=False, default='CONSULTING'),
    Column('retrieval_top_k', Integer, nullable=False),
    Column('reranker', String(16), nullable=False),
    Column('evidence_ids', Text, nullable=False),
    Column('claim_verification', Boolean),
    Column('model_name', String(160)),
    Column('prompt_revision', String(64), nullable=False),
    Column('ttft_ms', Integer),
    Column('total_latency_ms', Integer, nullable=False),
    Column('prompt_token_count', Integer),
    Column('completion_token_count', Integer),
    Column('fallback_reason', String(64)),
    Column('knowledge_revision', String(64)),
    Column('created_at', DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint('session_id', 'idempotency_key', name='uq_retrieval_trace_session_request'),
)
rate_limits = Table(
    'rate_limits',
    metadata,
    Column('key', String(64), primary_key=True),
    Column('window', Integer, nullable=False),
    Column('count', Integer, nullable=False),
)


def make_engine(url=None):
    url = url or os.getenv('DATABASE_URL', 'sqlite:///energytool.db')
    options: dict[str, object] = {'pool_pre_ping': True}
    if url.startswith('sqlite:'):
        options['connect_args'] = {'check_same_thread': False, 'timeout': 10}
    engine = create_engine(url, **options)
    if url.startswith('sqlite:'):

        @event.listens_for(engine, 'connect')
        def enable_foreign_keys(connection, _record):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')

    return engine
