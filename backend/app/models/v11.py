from datetime import datetime, timezone

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class AgentThread(db.Model):
    __tablename__ = "agent_threads"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('active','completed','cleared')",
            name="ck_agent_threads_status",
        ),
    )

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(20), nullable=False, default="active", server_default="active")
    encrypted_state = db.Column(db.Text, nullable=False)
    state_version = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    last_activity_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )
    cleared_at = db.Column(db.DateTime(timezone=True), nullable=True)


class AgentRun(db.Model):
    __tablename__ = "agent_runs"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('running','waiting_approval','completed','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
    )

    id = db.Column(db.String(36), primary_key=True)
    thread_id = db.Column(
        db.String(36),
        db.ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(24), nullable=False, default="running", server_default="running")
    intent = db.Column(db.String(50), nullable=True, index=True)
    model_name = db.Column(db.String(100), nullable=True)
    prompt_version = db.Column(db.String(40), nullable=False, default="agent-v1", server_default="agent-v1")
    usage = db.Column(db.JSON, nullable=False, default=dict)
    error_code = db.Column(db.String(80), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class AgentToolEvent(db.Model):
    __tablename__ = "agent_tool_events"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('started','completed','failed','denied')",
            name="ck_agent_tool_events_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name = db.Column(db.String(80), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)
    redacted_input = db.Column(db.JSON, nullable=False, default=dict)
    redacted_output = db.Column(db.JSON, nullable=False, default=dict)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)


class AgentPendingAction(db.Model):
    __tablename__ = "agent_pending_actions"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('pending','approved','rejected','expired','executed','failed')",
            name="ck_agent_pending_actions_status",
        ),
    )

    id = db.Column(db.String(36), primary_key=True)
    thread_id = db.Column(
        db.String(36),
        db.ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = db.Column(
        db.String(36),
        db.ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type = db.Column(db.String(50), nullable=False, index=True)
    encrypted_payload = db.Column(db.Text, nullable=False)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(20), nullable=False, default="pending", server_default="pending")
    revision = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)


class AgentActionExecution(db.Model):
    __tablename__ = "agent_action_executions"
    __table_args__ = (
        db.UniqueConstraint("action_id", name="uq_agent_action_executions_action"),
        db.UniqueConstraint("idempotency_key", name="uq_agent_action_executions_key"),
        db.CheckConstraint(
            "status in ('started','completed','failed')",
            name="ck_agent_action_executions_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    action_id = db.Column(
        db.String(36),
        db.ForeignKey("agent_pending_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="started", server_default="started")
    result = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class SupportHandoff(db.Model):
    __tablename__ = "support_handoffs"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('open','in_progress','resolved','closed')",
            name="ck_support_handoffs_status",
        ),
        db.CheckConstraint(
            "priority in ('normal','high','urgent')",
            name="ck_support_handoffs_priority",
        ),
    )

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id = db.Column(
        db.String(36),
        db.ForeignKey("agent_threads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category = db.Column(db.String(50), nullable=False, index=True)
    priority = db.Column(db.String(20), nullable=False, default="normal", server_default="normal")
    status = db.Column(db.String(20), nullable=False, default="open", server_default="open", index=True)
    summary = db.Column(db.String(500), nullable=False)
    assigned_to_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)


class OAuthClient(db.Model):
    __tablename__ = "oauth_clients"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('pending','approved','rejected','revoked')",
            name="ck_oauth_clients_status",
        ),
    )

    client_id = db.Column(db.String(80), primary_key=True)
    client_name = db.Column(db.String(160), nullable=False)
    redirect_uris = db.Column(db.JSON, nullable=False)
    scopes = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", server_default="pending", index=True)
    token_endpoint_auth_method = db.Column(db.String(30), nullable=False, default="none", server_default="none")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)


class OAuthAuthorizationCode(db.Model):
    __tablename__ = "oauth_authorization_codes"

    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    client_id = db.Column(db.String(80), db.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    redirect_uri = db.Column(db.String(500), nullable=False)
    scope = db.Column(db.String(500), nullable=False)
    code_challenge = db.Column(db.String(128), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class OAuthAccessToken(db.Model):
    __tablename__ = "oauth_access_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    client_id = db.Column(db.String(80), db.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = db.Column(db.String(500), nullable=False)
    audience = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class OAuthRefreshToken(db.Model):
    __tablename__ = "oauth_refresh_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    family_id = db.Column(db.String(36), nullable=False, index=True)
    client_id = db.Column(db.String(80), db.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = db.Column(db.String(500), nullable=False)
    audience = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
