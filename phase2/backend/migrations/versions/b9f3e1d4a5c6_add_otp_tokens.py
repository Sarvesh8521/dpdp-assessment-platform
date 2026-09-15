"""add_otp_tokens

Revision ID: b9f3e1d4a5c6
Revises: 7c179f087532
Create Date: 2026-09-07 15:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9f3e1d4a5c6'
down_revision: Union[str, Sequence[str], None] = '7c179f087532'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'otp_tokens',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_otp', sa.String(length=255), nullable=False),
        sa.Column('purpose', sa.String(length=50), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_otp_tokens_email_purpose', 'otp_tokens', ['email', 'purpose'], unique=False)
    op.create_index('ix_otp_tokens_expires_at', 'otp_tokens', ['expires_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_otp_tokens_expires_at', table_name='otp_tokens')
    op.drop_index('ix_otp_tokens_email_purpose', table_name='otp_tokens')
    op.drop_table('otp_tokens')
