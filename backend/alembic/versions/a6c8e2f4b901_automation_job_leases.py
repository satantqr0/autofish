"""add automation job leases

Revision ID: a6c8e2f4b901
Revises: f3a91c7e2b44
Create Date: 2026-08-26 00:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6c8e2f4b901"
down_revision: str | None = "f3a91c7e2b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automation_jobs",
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "automation_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_jobs_status_lease_expiry",
        "automation_jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_status_lease_expiry", table_name="automation_jobs")
    op.drop_column("automation_jobs", "lease_expires_at")
    op.drop_column("automation_jobs", "lease_owner")
