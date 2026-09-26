"""Twice-daily market price samples and collection audit."""

import sqlalchemy as sa

from alembic import op

revision = "b72d910e2026"
down_revision = "a6c8e2f4b901"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_price_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slot", sa.String(32), nullable=False, unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("message", sa.String(300)),
    )


def downgrade():
    op.drop_table("market_price_runs")
