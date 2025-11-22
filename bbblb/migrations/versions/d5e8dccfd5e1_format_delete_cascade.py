"""format_delete_cascade

Revision ID: d5e8dccfd5e1
Revises: 4aff87a582fa
Create Date: 2025-11-22 12:50:36.436611

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d5e8dccfd5e1"
down_revision: Union[str, Sequence[str], None] = "4aff87a582fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("playback", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_playback_recording_fk_recordings"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_playback_recording_fk_recordings"),
            "recordings",
            ["recording_fk"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("playback", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_playback_recording_fk_recordings"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_playback_recording_fk_recordings"),
            "recordings",
            ["recording_fk"],
            ["id"],
        )
