"""add xianyu product daily metrics

Revision ID: e8f4a6c2d901
Revises: b7d3e9f1042a
Create Date: 2026-08-18 23:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f4a6c2d901"
down_revision: str | Sequence[str] | None = "b7d3e9f1042a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "xianyu_product_daily_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("xianyu_product_id", sa.Integer(), nullable=True),
        sa.Column("external_product_id", sa.String(length=160), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("listing_title", sa.String(length=500), nullable=False),
        sa.Column("listing_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("listing_days", sa.Integer(), nullable=False),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("category_path", sa.JSON(), nullable=False),
        sa.Column("exposures", sa.Integer(), nullable=False),
        sa.Column("exposed_users", sa.Integer(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("viewers", sa.Integer(), nullable=False),
        sa.Column("inquiries", sa.Integer(), nullable=False),
        sa.Column("paid_users", sa.Integer(), nullable=False),
        sa.Column("paid_orders", sa.Integer(), nullable=False),
        sa.Column("paid_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("browse_pay_conversion", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("refund_requested_users", sa.Integer(), nullable=False),
        sa.Column("refund_requested_orders", sa.Integer(), nullable=False),
        sa.Column(
            "refund_requested_amount", sa.Numeric(precision=14, scale=2), nullable=False
        ),
        sa.Column("refund_success_users", sa.Integer(), nullable=False),
        sa.Column("refund_success_orders", sa.Integer(), nullable=False),
        sa.Column("refund_success_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_snapshot", sa.JSON(), nullable=False),
        sa.Column("imported_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_xianyu_product_daily_metrics_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["users.id"],
            name=op.f("fk_xianyu_product_daily_metrics_imported_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["xianyu_product_id"],
            ["xianyu_products.id"],
            name=op.f("fk_xianyu_product_daily_metrics_xianyu_product_id_xianyu_products"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xianyu_product_daily_metrics")),
        sa.UniqueConstraint(
            "account_id",
            "external_product_id",
            "metric_date",
            name="uq_xianyu_product_daily_metric",
        ),
    )
    op.create_index(
        "ix_xianyu_metrics_account_date",
        "xianyu_product_daily_metrics",
        ["account_id", "metric_date"],
        unique=False,
    )
    op.create_index(
        "ix_xianyu_metrics_product_date",
        "xianyu_product_daily_metrics",
        ["external_product_id", "metric_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_xianyu_product_daily_metrics_source_hash"),
        "xianyu_product_daily_metrics",
        ["source_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_xianyu_product_daily_metrics_source_hash"),
        table_name="xianyu_product_daily_metrics",
    )
    op.drop_index("ix_xianyu_metrics_product_date", table_name="xianyu_product_daily_metrics")
    op.drop_index("ix_xianyu_metrics_account_date", table_name="xianyu_product_daily_metrics")
    op.drop_table("xianyu_product_daily_metrics")
