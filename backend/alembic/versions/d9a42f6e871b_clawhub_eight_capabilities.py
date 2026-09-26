"""clawhub eight capabilities

Revision ID: d9a42f6e871b
Revises: 8b4e3d2c1a90
Create Date: 2026-08-16 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9a42f6e871b"
down_revision: str | None = "8b4e3d2c1a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps():
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "supplier_discovery_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("sourcing_candidate_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("safe_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sourcing_candidate_id"], ["sourcing_candidates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_supplier_discovery_status_created",
        "supplier_discovery_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        op.f("ix_supplier_discovery_jobs_correlation_id"),
        "supplier_discovery_jobs",
        ["correlation_id"],
    )

    op.create_table(
        "supplier_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_supplier_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=80), nullable=False),
        sa.Column("region", sa.String(length=160), nullable=True),
        sa.Column("industry", sa.String(length=200), nullable=True),
        sa.Column("factory_tags", sa.JSON(), nullable=False),
        sa.Column("source_product_count", sa.Integer(), nullable=False),
        sa.Column("suitability_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("response_speed_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("delivery_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("raw_snapshot", sa.JSON(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "external_supplier_id", name="uq_supplier_candidate_source"
        ),
    )
    op.create_index(
        "ix_supplier_candidates_status_score",
        "supplier_candidates",
        ["status", "suitability_score"],
    )

    op.create_table(
        "supplier_product_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sourcing_candidate_id", sa.Integer(), nullable=False),
        sa.Column("supplier_candidate_id", sa.Integer(), nullable=False),
        sa.Column("external_product_id", sa.String(length=200), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("minimum_order_quantity", sa.Integer(), nullable=True),
        sa.Column("shipping_terms", sa.JSON(), nullable=False),
        sa.Column("match_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["sourcing_candidate_id"], ["sourcing_candidates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supplier_candidate_id"], ["supplier_candidates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sourcing_candidate_id",
            "supplier_candidate_id",
            "external_product_id",
            name="uq_supplier_product_match",
        ),
    )
    op.create_index(
        "ix_supplier_matches_candidate_priority",
        "supplier_product_matches",
        ["sourcing_candidate_id", "priority"],
    )

    op.create_table(
        "supplier_reliability_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_candidate_id", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("engine_version", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["supplier_candidate_id"], ["supplier_candidates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_supplier_reliability_created",
        "supplier_reliability_scores",
        ["supplier_candidate_id", "created_at"],
    )

    op.create_table(
        "platform_action_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_platform_action_records_action_type"),
        "platform_action_records",
        ["action_type"],
    )
    op.create_index(
        "ix_platform_actions_status_created",
        "platform_action_records",
        ["status", "created_at"],
    )

    op.create_table(
        "product_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("sourcing_candidate_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("grade", sa.String(length=10), nullable=False),
        sa.Column("total_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["sourcing_candidate_id"], ["sourcing_candidates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_evaluations_candidate_created",
        "product_evaluations",
        ["sourcing_candidate_id", "created_at"],
    )
    op.create_index(
        "ix_product_evaluations_product_created",
        "product_evaluations",
        ["product_id", "created_at"],
    )
    op.create_index(
        op.f("ix_product_evaluations_grade"), "product_evaluations", ["grade"]
    )
    op.create_index(
        op.f("ix_product_evaluations_input_hash"),
        "product_evaluations",
        ["input_hash"],
    )

    op.create_table(
        "product_diagnoses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("sourcing_candidate_id", sa.Integer(), nullable=True),
        sa.Column("diagnosis", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recommended_actions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("engine_version", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["sourcing_candidate_id"], ["sourcing_candidates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_product_diagnoses_diagnosis"), "product_diagnoses", ["diagnosis"]
    )
    op.create_index(
        op.f("ix_product_diagnoses_severity"), "product_diagnoses", ["severity"]
    )
    op.create_index(
        "ix_product_diagnoses_severity_created",
        "product_diagnoses",
        ["severity", "created_at"],
    )

    op.create_table(
        "xianyu_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("images", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("pipeline_trace", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "input_hash", name="uq_xianyu_draft_product_hash"),
    )
    op.create_index(
        "ix_xianyu_drafts_status_created", "xianyu_drafts", ["status", "created_at"]
    )

    op.create_table(
        "supplier_inquiries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_candidate_id", sa.Integer(), nullable=True),
        sa.Column("sourcing_candidate_id", sa.Integer(), nullable=True),
        sa.Column("topic", sa.String(length=300), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("upstream_task_id", sa.String(length=200), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sourcing_candidate_id"], ["sourcing_candidates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["supplier_candidate_id"], ["supplier_candidates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_supplier_inquiries_correlation_id"),
        "supplier_inquiries",
        ["correlation_id"],
    )
    op.create_index(
        "ix_supplier_inquiries_status_created",
        "supplier_inquiries",
        ["status", "created_at"],
    )

    op.create_table(
        "supplier_inquiry_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inquiry_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("author_type", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["inquiry_id"], ["supplier_inquiries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inquiry_messages_inquiry_created",
        "supplier_inquiry_messages",
        ["inquiry_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inquiry_messages_inquiry_created", table_name="supplier_inquiry_messages"
    )
    op.drop_table("supplier_inquiry_messages")
    op.drop_index("ix_supplier_inquiries_status_created", table_name="supplier_inquiries")
    op.drop_index(
        op.f("ix_supplier_inquiries_correlation_id"), table_name="supplier_inquiries"
    )
    op.drop_table("supplier_inquiries")
    op.drop_index("ix_xianyu_drafts_status_created", table_name="xianyu_drafts")
    op.drop_table("xianyu_drafts")
    op.drop_index(
        "ix_product_diagnoses_severity_created", table_name="product_diagnoses"
    )
    op.drop_index(op.f("ix_product_diagnoses_severity"), table_name="product_diagnoses")
    op.drop_index(
        op.f("ix_product_diagnoses_diagnosis"), table_name="product_diagnoses"
    )
    op.drop_table("product_diagnoses")
    op.drop_index(
        op.f("ix_product_evaluations_input_hash"), table_name="product_evaluations"
    )
    op.drop_index(op.f("ix_product_evaluations_grade"), table_name="product_evaluations")
    op.drop_index(
        "ix_product_evaluations_product_created", table_name="product_evaluations"
    )
    op.drop_index(
        "ix_product_evaluations_candidate_created", table_name="product_evaluations"
    )
    op.drop_table("product_evaluations")
    op.drop_index(
        "ix_platform_actions_status_created", table_name="platform_action_records"
    )
    op.drop_index(
        op.f("ix_platform_action_records_action_type"),
        table_name="platform_action_records",
    )
    op.drop_table("platform_action_records")
    op.drop_index(
        "ix_supplier_reliability_created", table_name="supplier_reliability_scores"
    )
    op.drop_table("supplier_reliability_scores")
    op.drop_index(
        "ix_supplier_matches_candidate_priority", table_name="supplier_product_matches"
    )
    op.drop_table("supplier_product_matches")
    op.drop_index(
        "ix_supplier_candidates_status_score", table_name="supplier_candidates"
    )
    op.drop_table("supplier_candidates")
    op.drop_index(
        op.f("ix_supplier_discovery_jobs_correlation_id"),
        table_name="supplier_discovery_jobs",
    )
    op.drop_index(
        "ix_supplier_discovery_status_created", table_name="supplier_discovery_jobs"
    )
    op.drop_table("supplier_discovery_jobs")
