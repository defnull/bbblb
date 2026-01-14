"""server stats

Revision ID: 80e8524b0d7c
Revises: 3e63cbead3b5
Create Date: 2026-01-14 08:57:54.664066

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "80e8524b0d7c"
down_revision: Union[str, Sequence[str], None] = "3e63cbead3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("servers", sa.Column("stats", sa.JSON(), nullable=True))
    op.execute("UPDATE servers SET stats = '{}'")
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.alter_column("stats", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_column("stats")
