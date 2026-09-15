"""remove_phone_fields

Revision ID: 0b92d500cffa
Revises: b9f3e1d4a5c6
Create Date: 2026-09-09 13:32:15.099188

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0b92d500cffa'
down_revision: Union[str, Sequence[str], None] = 'b9f3e1d4a5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('ix_users_phone', table_name='users')
    op.drop_column('users', 'phone_verified')
    op.drop_column('users', 'phone')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('users', sa.Column('phone', sa.VARCHAR(length=50), autoincrement=False, nullable=True))
    op.add_column('users', sa.Column('phone_verified', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False))
    op.create_index('ix_users_phone', 'users', ['phone'], unique=True)
