"""phase 3-4 read-only integrations

Revision ID: 8b4e3d2c1a90
Revises: fe07d1b0cd73
Create Date: 2026-08-15 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8b4e3d2c1a90"
down_revision: str | None = "fe07d1b0cd73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "accounts",
        sa.Column("raw_snapshot", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column("accounts", sa.Column("snapshot_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "accounts", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(op.f("ix_accounts_snapshot_hash"), "accounts", ["snapshot_hash"])

    op.alter_column("xianyu_products", "product_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "xianyu_products",
        sa.Column("raw_snapshot", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        "xianyu_products", sa.Column("snapshot_hash", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_xianyu_products_snapshot_hash"), "xianyu_products", ["snapshot_hash"]
    )

    op.add_column(
        "orders",
        sa.Column("raw_snapshot", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column("orders", sa.Column("snapshot_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "orders", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(op.f("ix_orders_snapshot_hash"), "orders", ["snapshot_hash"])
    op.create_unique_constraint(
        "uq_message_conversation_external",
        "messages",
        ["conversation_id", "external_message_id"],
    )

    op.create_table(
        "integration_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("adapter_name", sa.String(length=100), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.Column("items_written", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("safe_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_runs_platform_started",
        "integration_sync_runs",
        ["platform", "started_at"],
    )
    op.create_index(
        "ix_integration_runs_status_started",
        "integration_sync_runs",
        ["status", "started_at"],
    )
    op.create_index(
        op.f("ix_integration_sync_runs_correlation_id"),
        "integration_sync_runs",
        ["correlation_id"],
    )

    op.create_table(
        "external_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_run_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("object_type", sa.String(length=60), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("adapter_name", sa.String(length=100), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_data", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"], ["integration_sync_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform",
            "object_type",
            "external_id",
            "payload_hash",
            name="uq_external_snapshot_identity_hash",
        ),
    )
    op.create_index(
        "ix_external_snapshot_object_fetched",
        "external_snapshots",
        ["platform", "object_type", "fetched_at"],
    )

    op.create_table(
        "sourcing_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("adapter_name", sa.String(length=100), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("external_product_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("external_supplier_id", sa.String(length=200), nullable=True),
        sa.Column("supplier_name", sa.String(length=200), nullable=True),
        sa.Column("minimum_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("maximum_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("sku_count", sa.Integer(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("normalized_data", sa.JSON(), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=False),
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
            ["external_snapshot_id"], ["external_snapshots.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adapter_name", "external_product_id", name="uq_sourcing_candidate"),
    )
    op.create_index(
        "ix_sourcing_candidates_status_score", "sourcing_candidates", ["status", "score"]
    )
    op.create_index(
        op.f("ix_sourcing_candidates_category"), "sourcing_candidates", ["category"]
    )
    op.create_index(op.f("ix_sourcing_candidates_score"), "sourcing_candidates", ["score"])


def downgrade() -> None:
    op.drop_index(op.f("ix_sourcing_candidates_score"), table_name="sourcing_candidates")
    op.drop_index(op.f("ix_sourcing_candidates_category"), table_name="sourcing_candidates")
    op.drop_index("ix_sourcing_candidates_status_score", table_name="sourcing_candidates")
    op.drop_table("sourcing_candidates")
    op.drop_index("ix_external_snapshot_object_fetched", table_name="external_snapshots")
    op.drop_table("external_snapshots")
    op.drop_index(
        op.f("ix_integration_sync_runs_correlation_id"), table_name="integration_sync_runs"
    )
    op.drop_index("ix_integration_runs_status_started", table_name="integration_sync_runs")
    op.drop_index("ix_integration_runs_platform_started", table_name="integration_sync_runs")
    op.drop_table("integration_sync_runs")
    op.drop_constraint("uq_message_conversation_external", "messages", type_="unique")
    op.drop_index(op.f("ix_orders_snapshot_hash"), table_name="orders")
    op.drop_column("orders", "last_synced_at")
    op.drop_column("orders", "snapshot_hash")
    op.drop_column("orders", "raw_snapshot")
    op.drop_index(op.f("ix_xianyu_products_snapshot_hash"), table_name="xianyu_products")
    op.drop_column("xianyu_products", "snapshot_hash")
    op.drop_column("xianyu_products", "raw_snapshot")
    op.alter_column("xianyu_products", "product_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index(op.f("ix_accounts_snapshot_hash"), table_name="accounts")
    op.drop_column("accounts", "last_synced_at")
    op.drop_column("accounts", "snapshot_hash")
    op.drop_column("accounts", "raw_snapshot")
    op.drop_column("users", "session_version")
