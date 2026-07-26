"""HealthDoc schema v10: fifth-round platform optimization.

Revision ID: 20260726_schema_v10
Revises: 20260722_schema_v9
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_schema_v10"
down_revision = "20260722_schema_v9"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_appointments_status", "appointments", type_="check")
    op.add_column("appointments", sa.Column("height_cm_snapshot", sa.Numeric(6, 2)))
    op.add_column("appointments", sa.Column("weight_kg_snapshot", sa.Numeric(6, 2)))
    op.add_column("appointments", sa.Column("bmi_snapshot", sa.Numeric(5, 2)))
    op.add_column("appointments", sa.Column("allergy_history_snapshot", sa.Text()))
    op.add_column("appointments", sa.Column("medical_history_snapshot", sa.Text()))
    op.add_column("appointments", sa.Column("intake_captured_at", sa.DateTime(timezone=True)))
    op.add_column("appointments", sa.Column("termination_party", sa.String(20)))
    op.add_column("appointments", sa.Column("termination_reason_code", sa.String(40)))
    op.add_column("appointments", sa.Column("termination_reason_text", sa.String(500)))
    op.execute("UPDATE appointments SET status='no_show', termination_party='subject', termination_reason_code='legacy_no_show' WHERE status='invalidated'")
    op.create_check_constraint(
        "ck_appointments_status",
        "appointments",
        "status in ('unfulfilled','awaiting_report','fulfilled','cancelled','no_show','institution_cancelled')",
    )
    op.create_check_constraint(
        "ck_appointments_termination_party",
        "appointments",
        "termination_party is null or termination_party in ('user','institution','subject')",
    )
    op.add_column(
        "report_indicators",
        sa.Column("result_status", sa.String(20), nullable=False, server_default="unknown"),
    )
    op.execute("UPDATE report_indicators SET result_status=CASE WHEN is_abnormal THEN 'abnormal' ELSE 'normal' END")
    op.create_check_constraint(
        "ck_report_indicators_result_status",
        "report_indicators",
        "result_status in ('normal','high','low','positive','negative','abnormal','unknown')",
    )

    op.create_table(
        "indicator_reference_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("indicator_dict_id", sa.Integer(), sa.ForeignKey("indicator_dicts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("gender_scope", sa.String(20), nullable=False, server_default="all"),
        sa.Column("min_age", sa.Integer()),
        sa.Column("max_age", sa.Integer()),
        sa.Column("reference_low", sa.Numeric(12, 4)),
        sa.Column("reference_high", sa.Numeric(12, 4)),
        sa.Column("reference_text", sa.String(255)),
        sa.Column("source_title", sa.String(200)),
        sa.Column("source_url", sa.String(500)),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("gender_scope in ('all', 'male', 'female', 'other')", name="ck_indicator_reference_rules_gender"),
        sa.CheckConstraint("reference_low is null or reference_high is null or reference_low <= reference_high", name="ck_indicator_reference_rules_range"),
    )
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("action_url", sa.String(500)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_user_notifications_user_key"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_user_notifications_title"),
        sa.CheckConstraint("length(trim(body)) > 0", name="ck_user_notifications_body"),
    )
    op.create_table(
        "report_asset_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, index=True),
        sa.Column("health_domain_id", sa.Integer(), sa.ForeignKey("health_domains.id"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("modality", sa.String(40), nullable=False, server_default="image"),
        sa.Column("max_files", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("code", name="uq_report_asset_types_code"),
        sa.CheckConstraint("max_files between 1 and 2", name="ck_report_asset_types_max_files"),
    )
    op.add_column("report_assets", sa.Column("asset_type_id", sa.Integer(), sa.ForeignKey("report_asset_types.id"), index=True))
    op.create_table(
        "package_version_asset_requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("package_version_id", sa.Integer(), sa.ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("asset_type_id", sa.Integer(), sa.ForeignKey("report_asset_types.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("package_version_id", "asset_type_id", name="uq_package_version_asset_requirement"),
    )


def downgrade():
    raise RuntimeError("schema v10 must be rolled back from a complete backup")
