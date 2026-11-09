"""Add reviewed lead drafts and context snapshots.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

from app.database import lead_contexts, lead_drafts

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    lead_drafts.create(bind=op.get_bind(), checkfirst=True)
    lead_contexts.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
