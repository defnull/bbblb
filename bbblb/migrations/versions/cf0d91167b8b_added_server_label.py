"""Added Server.label

Revision ID: cf0d91167b8b
Revises: 8fdf995890b2
Create Date: 2026-02-13 18:35:21.856254

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cf0d91167b8b"
down_revision: Union[str, Sequence[str], None] = "8fdf995890b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("label", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_column("label")
