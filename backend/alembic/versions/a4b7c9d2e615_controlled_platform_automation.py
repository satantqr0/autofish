"""controlled platform automation

Revision ID: a4b7c9d2e615
Revises: d9a42f6e871b
Create Date: 2026-08-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4b7c9d2e615"
down_revision: str | Sequence[str] | None = "d9a42f6e871b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automation_controls",
        sa.Column("mode", sa.String(length=20), server_default="REVIEW", nullable=False),
    )
    op.add_column(
        "automation_controls",
        sa.Column("daily_limit", sa.Integer(), server_default="20", nullable=False),
    )
    op.add_column(
        "automation_controls",
        sa.Column("min_interval_seconds", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column(
        "automation_controls",
        sa.Column("failure_threshold", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column(
        "automation_controls",
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "automation_controls", sa.Column("cooldown_until", sa.DateTime(timezone=True))
    )
    op.add_column(
        "automation_controls", sa.Column("last_executed_at", sa.DateTime(timezone=True))
    )

    op.add_column(
        "platform_action_records",
        sa.Column("execution_result", sa.JSON(), server_default="{}", nullable=False),
    )
    op.add_column(
        "platform_action_records",
        sa.Column("automatic", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "platform_action_records", sa.Column("provider", sa.String(length=100))
    )
    op.add_column(
        "platform_action_records", sa.Column("correlation_id", sa.String(length=80))
    )
    op.add_column(
        "platform_action_records",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "platform_action_records", sa.Column("error_code", sa.String(length=100))
    )
    op.add_column("platform_action_records", sa.Column("error_message", sa.Text()))
    op.add_column(
        "platform_action_records", sa.Column("approved_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        op.f("ix_platform_action_records_correlation_id"),
        "platform_action_records",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_platform_action_records_correlation_id"),
        table_name="platform_action_records",
    )
    for column in (
        "approved_at",
        "error_message",
        "error_code",
        "attempts",
        "correlation_id",
        "provider",
        "automatic",
        "execution_result",
    ):
        op.drop_column("platform_action_records", column)
    for column in (
        "last_executed_at",
        "cooldown_until",
        "consecutive_failures",
        "failure_threshold",
        "min_interval_seconds",
        "daily_limit",
        "mode",
    ):
        op.drop_column("automation_controls", column)
