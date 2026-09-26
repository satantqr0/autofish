"""add autonomous launch pipeline

Revision ID: b7d3e9f1042a
Revises: f1a2b3c4d5e6
Create Date: 2026-08-17 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d3e9f1042a"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_launches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("automation_job_id", sa.Integer(), nullable=True),
        sa.Column("sourcing_candidate_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("current_stage", sa.String(length=60), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("ai_copy", sa.JSON(), nullable=False),
        sa.Column("asset_manifest", sa.JSON(), nullable=False),
        sa.Column("vision_qa", sa.JSON(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
            ["automation_job_id"], ["automation_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["xianyu_drafts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sourcing_candidate_id"], ["sourcing_candidates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_autonomous_launches")),
        sa.UniqueConstraint("automation_job_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_autonomous_launches_status", "autonomous_launches", ["status"]
    )
    op.create_index(
        "ix_autonomous_launches_status_created",
        "autonomous_launches",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_autonomous_launches_correlation_id",
        "autonomous_launches",
        ["correlation_id"],
    )
    op.add_column("ai_decisions", sa.Column("sourcing_candidate_id", sa.Integer()))
    op.add_column("ai_decisions", sa.Column("autonomous_launch_id", sa.Integer()))
    op.create_foreign_key(
        op.f("fk_ai_decisions_sourcing_candidate_id_sourcing_candidates"),
        "ai_decisions",
        "sourcing_candidates",
        ["sourcing_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_ai_decisions_autonomous_launch_id_autonomous_launches"),
        "ai_decisions",
        "autonomous_launches",
        ["autonomous_launch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_ai_decisions_autonomous_launch_id_autonomous_launches"),
        "ai_decisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_ai_decisions_sourcing_candidate_id_sourcing_candidates"),
        "ai_decisions",
        type_="foreignkey",
    )
    op.drop_column("ai_decisions", "autonomous_launch_id")
    op.drop_column("ai_decisions", "sourcing_candidate_id")
    op.drop_index("ix_autonomous_launches_correlation_id", table_name="autonomous_launches")
    op.drop_index("ix_autonomous_launches_status_created", table_name="autonomous_launches")
    op.drop_index("ix_autonomous_launches_status", table_name="autonomous_launches")
    op.drop_table("autonomous_launches")
