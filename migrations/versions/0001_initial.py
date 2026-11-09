"""Initialize schema; preserve the prototype's existing SQLite tables.

Revision ID: 0001
Revises:
"""
from alembic import op
from app.database import audit, knowledge, leads, messages, sessions, settings

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for table in (sessions, messages, leads, knowledge, settings, audit):
        table.create(bind=connection, checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
