"""judgement: keep failed judgements (nullable scores, `error`, `repaired`)

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NULLABLE = {
    "overall_score": sa.Integer(),
    "censorship": sa.String(),
    "refusal": sa.Boolean(),
    "balance_score": sa.Integer(),
    "depth_score": sa.Integer(),
    "rationale": sa.Text(),
}


def upgrade() -> None:
    with op.batch_alter_table("judgement", schema=None) as batch_op:
        for name, type_ in NULLABLE.items():
            batch_op.alter_column(name, existing_type=type_, nullable=True)
        batch_op.add_column(sa.Column("repaired", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM judgement WHERE error IS NOT NULL OR overall_score IS NULL")
    with op.batch_alter_table("judgement", schema=None) as batch_op:
        batch_op.drop_column("error")
        batch_op.drop_column("repaired")
        for name, type_ in NULLABLE.items():
            batch_op.alter_column(name, existing_type=type_, nullable=False)
