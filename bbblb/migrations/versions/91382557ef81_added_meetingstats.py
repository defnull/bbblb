"""Added MeetingStats

Revision ID: 91382557ef81
Revises: 8fdf995890b2
Create Date: 2026-02-18 15:00:58.727844

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "91382557ef81"
down_revision: Union[str, Sequence[str], None] = "8fdf995890b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "meeting_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.String(), nullable=False),
        sa.Column("tenant_fk", sa.Integer(), nullable=True),
        sa.Column("users", sa.Integer(), nullable=False),
        sa.Column("voice", sa.Integer(), nullable=False),
        sa.Column("video", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_fk"],
            ["tenants.id"],
            name=op.f("fk_meeting_stats_tenant_fk_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting_stats")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("meeting_stats")
