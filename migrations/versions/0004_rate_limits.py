"""Add shared request counters.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
from app.database import rate_limits

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    rate_limits.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
