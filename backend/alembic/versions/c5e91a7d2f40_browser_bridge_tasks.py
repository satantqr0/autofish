"""browser bridge tasks

Revision ID: c5e91a7d2f40
Revises: a4b7c9d2e615
Create Date: 2026-08-16 15:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e91a7d2f40"
down_revision: str | Sequence[str] | None = "a4b7c9d2e615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_action_records",
        sa.Column("lease_owner", sa.String(length=80)),
    )
    op.add_column(
        "platform_action_records",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_platform_action_records_lease_expires_at",
        "platform_action_records",
        ["lease_expires_at"],
    )
    op.create_table(
        "browser_bridge_agents",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("current_url", sa.String(length=1000)),
        sa.Column("last_error", sa.String(length=500)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_browser_bridge_agents")),
    )
    op.create_index(
        "ix_browser_bridge_agents_last_seen",
        "browser_bridge_agents",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_browser_bridge_agents_last_seen", table_name="browser_bridge_agents")
    op.drop_table("browser_bridge_agents")
    op.drop_index(
        "ix_platform_action_records_lease_expires_at",
        table_name="platform_action_records",
    )
    op.drop_column("platform_action_records", "lease_expires_at")
    op.drop_column("platform_action_records", "lease_owner")
