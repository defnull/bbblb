"""
Change multiple columns to JSONB, but only for postgres because sqlite
does not support JSONB just yet. This change was necessary because postgres
fails to SELECT DISTINCT on a JSON column.

Revision ID: 8fdf995890b2
Revises: 80e8524b0d7c
Create Date: 2026-01-29 12:55:35.768277

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, JSON

# revision identifiers, used by Alembic.
revision: str = "8fdf995890b2"
down_revision: Union[str, Sequence[str], None] = "80e8524b0d7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_context().dialect.name == "postgresql":
        op.alter_column(
            "recordings", "meta", type_=JSONB, postgresql_using="meta::text::jsonb"
        )
        op.alter_column(
            "servers", "stats", type_=JSONB, postgresql_using="stats::text::jsonb"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_context().dialect.name == "postgresql":
        op.alter_column("recordings", "meta", type_=JSON, postgresql_using="meta::json")
        op.alter_column("servers", "stats", type_=JSON, postgresql_using="stats::json")
