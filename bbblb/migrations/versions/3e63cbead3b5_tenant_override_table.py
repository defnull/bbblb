"""tenant override table

Revision ID: 3e63cbead3b5
Revises: d5e8dccfd5e1
Create Date: 2025-12-16 13:45:58.316282

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "3e63cbead3b5"
down_revision: Union[str, Sequence[str], None] = "d5e8dccfd5e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    tenant_override_table = op.create_table(
        "tenant_override",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_fk", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("param", sa.String(), nullable=False),
        sa.Column("op", sa.String(1), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_fk"],
            ["tenants.id"],
            name=op.f("fk_tenant_override_tenant_fk_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_override")),
        sa.UniqueConstraint(
            "tenant_fk", "type", "param", name=op.f("uq_tenant_override_tenant_fk")
        ),
    )

    conn = op.get_bind()
    res = conn.execute(sa.text("SELECT id, overrides FROM tenants"))
    bulk = []
    for tenant_id, override_json in res.fetchall():
        for param, value in json.loads(override_json).items():
            flag, value = value[0], value[1:]
            if flag not in "?=<+":
                flag = "?"
            bulk.append(
                {
                    "tenant_fk": tenant_id,
                    "type": "create",
                    "param": param,
                    "op": flag,
                    "value": value,
                }
            )

    op.bulk_insert(tenant_override_table, bulk)

    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_column("overrides")


def downgrade() -> None:
    """Downgrade schema."""

    raise NotImplementedError("Downgrade would loose override values")
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.add_column(sa.Column("overrides", sa.JSON(), nullable=False))
    op.drop_table("tenant_override")
