"""Store redacted diagnostics for completed chat turns.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

from app.database import traces

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    traces.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
