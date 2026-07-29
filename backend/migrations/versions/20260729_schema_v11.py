"""HealthDoc schema v11: persistent Agent runs, approvals and support handoff.

Revision ID: 20260729_schema_v11
Revises: 20260726_schema_v10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_schema_v11"
down_revision = "20260726_schema_v10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_threads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("encrypted_state", sa.Text(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cleared_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status in ('active','completed','cleared')", name="ck_agent_threads_status"),
    )
    op.create_index("ix_agent_threads_user_id", "agent_threads", ["user_id"])
    op.create_index("ix_agent_threads_last_activity_at", "agent_threads", ["last_activity_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("thread_id", sa.String(36), sa.ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("intent", sa.String(50)),
        sa.Column("model_name", sa.String(100)),
        sa.Column("prompt_version", sa.String(40), nullable=False, server_default="agent-v1"),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('running','waiting_approval','completed','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
    )
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_intent", "agent_runs", ["intent"])
    op.create_index("ix_agent_runs_started_at", "agent_runs", ["started_at"])

    op.create_table(
        "agent_tool_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("redacted_input", sa.JSON(), nullable=False),
        sa.Column("redacted_output", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status in ('started','completed','failed','denied')",
            name="ck_agent_tool_events_status",
        ),
    )
    op.create_index("ix_agent_tool_events_run_id", "agent_tool_events", ["run_id"])
    op.create_index("ix_agent_tool_events_tool_name", "agent_tool_events", ["tool_name"])
    op.create_index("ix_agent_tool_events_created_at", "agent_tool_events", ["created_at"])

    op.create_table(
        "agent_pending_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("thread_id", sa.String(36), sa.ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('pending','approved','rejected','expired','executed','failed')",
            name="ck_agent_pending_actions_status",
        ),
    )
    op.create_index("ix_agent_pending_actions_thread_id", "agent_pending_actions", ["thread_id"])
    op.create_index("ix_agent_pending_actions_run_id", "agent_pending_actions", ["run_id"])
    op.create_index("ix_agent_pending_actions_user_id", "agent_pending_actions", ["user_id"])
    op.create_index("ix_agent_pending_actions_action_type", "agent_pending_actions", ["action_type"])
    op.create_index("ix_agent_pending_actions_expires_at", "agent_pending_actions", ["expires_at"])

    op.create_table(
        "agent_action_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("agent_pending_actions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="started"),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("action_id", name="uq_agent_action_executions_action"),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_action_executions_key"),
        sa.CheckConstraint("status in ('started','completed','failed')", name="ck_agent_action_executions_status"),
    )
    op.create_index("ix_agent_action_executions_action_id", "agent_action_executions", ["action_id"])

    op.create_table(
        "support_handoffs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.String(36), sa.ForeignKey("agent_threads.id", ondelete="SET NULL")),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('open','in_progress','resolved','closed')",
            name="ck_support_handoffs_status",
        ),
        sa.CheckConstraint(
            "priority in ('normal','high','urgent')",
            name="ck_support_handoffs_priority",
        ),
    )
    op.create_index("ix_support_handoffs_user_id", "support_handoffs", ["user_id"])
    op.create_index("ix_support_handoffs_thread_id", "support_handoffs", ["thread_id"])
    op.create_index("ix_support_handoffs_category", "support_handoffs", ["category"])
    op.create_index("ix_support_handoffs_status", "support_handoffs", ["status"])
    op.create_index("ix_support_handoffs_assigned_to_user_id", "support_handoffs", ["assigned_to_user_id"])
    op.create_index("ix_support_handoffs_created_at", "support_handoffs", ["created_at"])

    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(80), primary_key=True),
        sa.Column("client_name", sa.String(160), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("token_endpoint_auth_method", sa.String(30), nullable=False, server_default="none"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('pending','approved','rejected','revoked')",
            name="ck_oauth_clients_status",
        ),
    )
    op.create_index("ix_oauth_clients_status", "oauth_clients", ["status"])
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(80), sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_oauth_authorization_codes_code_hash", "oauth_authorization_codes", ["code_hash"], unique=True)
    op.create_index("ix_oauth_authorization_codes_client_id", "oauth_authorization_codes", ["client_id"])
    op.create_index("ix_oauth_authorization_codes_user_id", "oauth_authorization_codes", ["user_id"])
    op.create_index("ix_oauth_authorization_codes_expires_at", "oauth_authorization_codes", ["expires_at"])
    op.create_table(
        "oauth_access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(80), sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False),
        sa.Column("audience", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_oauth_access_tokens_token_hash", "oauth_access_tokens", ["token_hash"], unique=True)
    op.create_index("ix_oauth_access_tokens_client_id", "oauth_access_tokens", ["client_id"])
    op.create_index("ix_oauth_access_tokens_user_id", "oauth_access_tokens", ["user_id"])
    op.create_index("ix_oauth_access_tokens_expires_at", "oauth_access_tokens", ["expires_at"])
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(80), sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False),
        sa.Column("audience", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_oauth_refresh_tokens_token_hash", "oauth_refresh_tokens", ["token_hash"], unique=True)
    op.create_index("ix_oauth_refresh_tokens_family_id", "oauth_refresh_tokens", ["family_id"])
    op.create_index("ix_oauth_refresh_tokens_client_id", "oauth_refresh_tokens", ["client_id"])
    op.create_index("ix_oauth_refresh_tokens_user_id", "oauth_refresh_tokens", ["user_id"])
    op.create_index("ix_oauth_refresh_tokens_expires_at", "oauth_refresh_tokens", ["expires_at"])


def downgrade():
    raise RuntimeError("schema v11 must be rolled back from a complete backup")
