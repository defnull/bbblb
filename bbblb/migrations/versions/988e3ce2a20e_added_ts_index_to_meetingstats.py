"""Added ts index to MeetingStats

Revision ID: 988e3ce2a20e
Revises: 91382557ef81
Create Date: 2026-03-27 14:13:48.678002

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "988e3ce2a20e"
down_revision: Union[str, Sequence[str], None] = "91382557ef81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_context().dialect.name == "postgresql":
        with op.batch_alter_table("meeting_stats", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_meeting_stats_ts"),
                ["ts"],
                unique=False,
                postgresql_using="brin",
            )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_context().dialect.name == "postgresql":
        with op.batch_alter_table("meeting_stats", schema=None) as batch_op:
            batch_op.drop_index(
                batch_op.f("ix_meeting_stats_ts"), postgresql_using="brin"
            )
