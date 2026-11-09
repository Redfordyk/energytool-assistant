"""Track session versions and idempotent message requests.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

from app.database import message_requests, session_states

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    session_states.create(bind=connection, checkfirst=True)
    message_requests.create(bind=connection, checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
