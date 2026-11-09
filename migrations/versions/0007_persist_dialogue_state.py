"""Persist the consultation lifecycle and lead-draft return state.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if 'state' not in {column['name'] for column in inspector.get_columns('session_states')}:
        op.add_column(
            'session_states',
            sa.Column('state', sa.String(length=32), server_default='NEW', nullable=False),
        )
    if 'return_state' not in {column['name'] for column in inspector.get_columns('lead_drafts')}:
        op.add_column(
            'lead_drafts',
            sa.Column(
                'return_state',
                sa.String(length=32),
                server_default='CONSULTING',
                nullable=False,
            ),
        )
    connection.execute(sa.text("UPDATE session_states SET state = 'CLOSED' WHERE closed = true"))
    connection.execute(
        sa.text(
            "UPDATE session_states SET state = 'CONSULTING' WHERE closed = false AND version > 0"
        )
    )


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
