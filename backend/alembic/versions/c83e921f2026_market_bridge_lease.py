"""Durable market browser leases, isolated from publishing tasks."""

import sqlalchemy as sa

from alembic import op

revision = "c83e921f2026"
down_revision = "b72d910e2026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "market_price_runs",
        sa.Column("bridge_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade():
    op.drop_column("market_price_runs", "bridge_state")
