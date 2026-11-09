"""Record dialogue lifecycle transitions in request traces.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {column['name'] for column in sa.inspect(connection).get_columns('retrieval_traces')}
    if 'dialogue_state_before' not in columns:
        op.add_column(
            'retrieval_traces',
            sa.Column(
                'dialogue_state_before',
                sa.String(length=32),
                server_default='NEW',
                nullable=False,
            ),
        )
    if 'dialogue_state_after' not in columns:
        op.add_column(
            'retrieval_traces',
            sa.Column(
                'dialogue_state_after',
                sa.String(length=32),
                server_default='CONSULTING',
                nullable=False,
            ),
        )


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
