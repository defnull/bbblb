"""Tenant overrides

Revision ID: 4aff87a582fa
Revises: 0650f8bdb0d2
Create Date: 2025-11-20 14:07:47.962438

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4aff87a582fa"
down_revision: Union[str, Sequence[str], None] = "0650f8bdb0d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tenants", sa.Column("overrides", sa.JSON(), nullable=True))
    op.execute("UPDATE tenants SET overrides = '{}'")
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.alter_column("overrides", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenants", "overrides")
