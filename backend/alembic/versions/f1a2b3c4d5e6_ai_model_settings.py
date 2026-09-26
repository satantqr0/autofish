"""add encrypted AI provider and runtime settings

Revision ID: f1a2b3c4d5e6
Revises: c5e91a7d2f40
Create Date: 2026-08-17 04:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "c5e91a7d2f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("text_model", sa.String(length=160), nullable=False),
        sa.Column("vision_model", sa.String(length=160), nullable=True),
        sa.Column("image_model", sa.String(length=160), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_hint", sa.String(length=40), nullable=True),
        sa.Column("is_configured", sa.Boolean(), nullable=False),
        sa.Column("last_test_status", sa.String(length=30), nullable=False),
        sa.Column("last_test_message", sa.String(length=500), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
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
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_provider_configs_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_provider_configs")),
    )
    op.create_index(
        op.f("ix_ai_provider_configs_provider"),
        "ai_provider_configs",
        ["provider"],
        unique=True,
    )
    op.create_table(
        "ai_runtime_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("primary_provider", sa.String(length=40), nullable=False),
        sa.Column("fallback_provider", sa.String(length=40), nullable=True),
        sa.Column("monthly_budget_cny", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("temperature", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("task_routing", sa.JSON(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
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
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_runtime_settings_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runtime_settings")),
    )


def downgrade() -> None:
    op.drop_table("ai_runtime_settings")
    op.drop_index(op.f("ix_ai_provider_configs_provider"), table_name="ai_provider_configs")
    op.drop_table("ai_provider_configs")
