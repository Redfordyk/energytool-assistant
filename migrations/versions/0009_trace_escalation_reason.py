"""Store why a lead was offered for an unresolved answer.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {column['name'] for column in sa.inspect(connection).get_columns('retrieval_traces')}
    if 'escalation_reason' not in columns:
        op.add_column('retrieval_traces', sa.Column('escalation_reason', sa.String(40)))


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
