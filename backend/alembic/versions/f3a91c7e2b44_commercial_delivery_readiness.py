"""commercial delivery readiness

Revision ID: f3a91c7e2b44
Revises: e8f4a6c2d901
Create Date: 2026-08-19 00:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a91c7e2b44"
down_revision: str | None = "e8f4a6c2d901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commercial_deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("edition", sa.String(length=40), nullable=False),
        sa.Column("installation_name", sa.String(length=120), nullable=False),
        sa.Column("customer_name", sa.String(length=160), nullable=True),
        sa.Column("customer_type", sa.String(length=40), nullable=False),
        sa.Column("plan", sa.String(length=30), nullable=False),
        sa.Column("deployment_mode", sa.String(length=60), nullable=False),
        sa.Column("account_owned_by_customer", sa.Boolean(), nullable=False),
        sa.Column("data_stays_customer_controlled", sa.Boolean(), nullable=False),
        sa.Column("no_credential_custody", sa.Boolean(), nullable=False),
        sa.Column("no_revenue_guarantee_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("prohibited_automation_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("regulatory_obligations_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("terms_version", sa.String(length=30), nullable=True),
        sa.Column("privacy_version", sa.String(length=30), nullable=True),
        sa.Column("compliance_version", sa.String(length=30), nullable=True),
        sa.Column("accepted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name=op.f("fk_commercial_deployments_accepted_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commercial_deployments")),
    )
    op.create_table(
        "commercial_evidence_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("qualified_leads", sa.Integer(), nullable=False),
        sa.Column("product_demos", sa.Integer(), nullable=False),
        sa.Column("paid_new_customers", sa.Integer(), nullable=False),
        sa.Column("active_customers", sa.Integer(), nullable=False),
        sa.Column("retained_30d_customers", sa.Integer(), nullable=False),
        sa.Column("refunded_customers", sa.Integer(), nullable=False),
        sa.Column("revenue_cny", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("delivery_hours", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("support_hours", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name=op.f("fk_commercial_evidence_periods_recorded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commercial_evidence_periods")),
    )
    op.create_index(
        op.f("ix_commercial_evidence_periods_period_start"),
        "commercial_evidence_periods",
        ["period_start"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_commercial_evidence_periods_period_start"),
        table_name="commercial_evidence_periods",
    )
    op.drop_table("commercial_evidence_periods")
    op.drop_table("commercial_deployments")
