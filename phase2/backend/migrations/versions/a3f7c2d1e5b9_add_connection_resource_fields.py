"""Add resource and account fields to connections

Revision ID: a3f7c2d1e5b9
Revises: 6bcc021f8c72
Create Date: 2026-09-01 14:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f7c2d1e5b9'
down_revision: Union[str, Sequence[str], None] = ('0c4cc1c09131', '6bcc021f8c72')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add resource selection and account display fields to connections."""
    op.add_column('connections', sa.Column('resource_id', sa.String(length=255), nullable=True))
    op.add_column('connections', sa.Column('resource_name', sa.String(length=255), nullable=True))
    op.add_column('connections', sa.Column('account_email', sa.String(length=255), nullable=True))
    op.add_column('connections', sa.Column('account_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Remove resource and account fields from connections."""
    op.drop_column('connections', 'account_name')
    op.drop_column('connections', 'account_email')
    op.drop_column('connections', 'resource_name')
    op.drop_column('connections', 'resource_id')
