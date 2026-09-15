"""Seed obligations

Revision ID: ff5b713a83aa
Revises: 6bcc021f8c72
Create Date: 2026-08-19 16:37:29.956783

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ff5b713a83aa'
down_revision: Union[str, Sequence[str], None] = '6bcc021f8c72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO obligations (id, code, title, category, severity, active, dpdp_section) VALUES
        (gen_random_uuid(), 'S6.consentRecord', 'Verifiable Consent Record', 'CONSENT', 'HIGH', true, 'Section 6(1)'),
        (gen_random_uuid(), 'S6.consentGating', 'Consent Gating', 'CONSENT', 'HIGH', true, 'Section 6(4)'),
        (gen_random_uuid(), 'S8.dataMinimization', 'Data Minimization', 'DATA_COLLECTION', 'MEDIUM', true, 'Section 8(1)'),
        (gen_random_uuid(), 'S8.retention', 'Data Retention Limits', 'DATA_RETENTION', 'HIGH', true, 'Section 8(4)'),
        (gen_random_uuid(), 'S9.sharing', 'Processor Sharing Contracts', 'DATA_SHARING', 'HIGH', true, 'Section 9(1)')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM obligations WHERE code IN ('S6.consentRecord', 'S6.consentGating', 'S8.dataMinimization', 'S8.retention', 'S9.sharing')")
