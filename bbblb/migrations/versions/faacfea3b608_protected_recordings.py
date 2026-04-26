"""Protected recordings

Revision ID: faacfea3b608
Revises: 988e3ce2a20e
Create Date: 2026-04-10 14:55:08.788193

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "faacfea3b608"
down_revision: Union[str, Sequence[str], None] = "988e3ce2a20e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "view_tickets",
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("recording_fk", sa.Integer(), nullable=False),
        sa.Column("expire", sa.DateTime(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recording_fk"],
            ["recordings.id"],
            name=op.f("fk_view_tickets_recording_fk_recordings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("uuid", name=op.f("pk_view_tickets")),
    )
    op.add_column(
        "recordings",
        sa.Column("protected", sa.Boolean(), server_default=sa.sql.expression.false()),
    )
    with op.batch_alter_table("recordings") as batch_op:
        batch_op.alter_column("protected", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("recordings") as batch_op:
        batch_op.drop_column("protected")

    op.drop_table("view_tickets")
