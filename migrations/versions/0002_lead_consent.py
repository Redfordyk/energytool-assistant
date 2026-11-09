"""Record explicit consent to local demo storage.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

from app.database import lead_consents

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    lead_consents.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally not automated')
