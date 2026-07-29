from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from flask import current_app, g, jsonify, redirect, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    User,
)
from app.agent.crypto import encrypt_json
from app.agent.tools import execute_tool
from app.models import AgentRun, AgentThread
from app.oauth import oauth_bp
from app.services.permissions import ROLE_ADMIN, roles_required


ALLOWED_SCOPES = {
    "knowledge.read",
    "catalog.read",
    "records.read",
    "booking.read",
    "booking.write",
    "support.write",
}


def _now():
    return datetime.now(timezone.utc)


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _token(size=32):
    return secrets.token_urlsafe(size)


def _oauth_error(error, description, status=400):
    response = jsonify({"error": error, "error_description": description})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _resource_url():
    return str(
        current_app.config.get("MCP_RESOURCE_URL")
        or request.url_root.rstrip("/") + "/mcp"
    )


def _issuer():
    return str(
        current_app.config.get("OAUTH_ISSUER")
        or request.url_root.rstrip("/")
    )


def _valid_redirect_uri(value):
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return False
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.fragment
        or "*" in str(value)
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _scopes(value):
    values = {item for item in str(value or "").split() if item}
    if not values or not values <= ALLOWED_SCOPES:
        raise ValueError("scope contains unsupported values")
    return values


def _approved_request(values):
    client = db.session.get(OAuthClient, str(values.get("client_id") or ""))
    if client is None or client.status != "approved":
        raise ValueError("client is not approved")
    redirect_uri = str(values.get("redirect_uri") or "")
    if redirect_uri not in (client.redirect_uris or []):
        raise ValueError("redirect_uri does not match registration")
    if values.get("response_type") != "code":
        raise ValueError("only response_type=code is supported")
    if values.get("code_challenge_method") != "S256":
        raise ValueError("PKCE S256 is required")
    challenge = str(values.get("code_challenge") or "")
    if not 43 <= len(challenge) <= 128:
        raise ValueError("invalid code_challenge")
    scopes = _scopes(values.get("scope"))
    if not scopes <= set(client.scopes or []):
        raise ValueError("scope exceeds approved client scopes")
    return client, redirect_uri, challenge, scopes


@oauth_bp.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    return {
        "resource": _resource_url(),
        "authorization_servers": [_issuer()],
        "scopes_supported": sorted(ALLOWED_SCOPES),
        "bearer_methods_supported": ["header"],
    }


@oauth_bp.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    issuer = _issuer()
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "registration_endpoint": f"{issuer}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(ALLOWED_SCOPES),
    }


@oauth_bp.post("/oauth/register")
def register_client():
    if not current_app.config.get("OAUTH_ENABLED"):
        return _oauth_error("temporarily_unavailable", "OAuth is disabled", 503)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _oauth_error("invalid_client_metadata", "JSON object required")
    name = str(payload.get("client_name") or "").strip()
    redirects = payload.get("redirect_uris")
    if not name or len(name) > 160:
        return _oauth_error("invalid_client_metadata", "invalid client_name")
    if (
        not isinstance(redirects, list)
        or not 1 <= len(redirects) <= 10
        or any(not _valid_redirect_uri(item) for item in redirects)
        or len(set(redirects)) != len(redirects)
    ):
        return _oauth_error("invalid_redirect_uri", "HTTPS or loopback redirect URI required")
    try:
        scopes = _scopes(payload.get("scope") or "knowledge.read catalog.read")
    except ValueError as exc:
        return _oauth_error("invalid_client_metadata", str(exc))
    client = OAuthClient(
        client_id=f"hdc_{secrets.token_urlsafe(24)}",
        client_name=name,
        redirect_uris=redirects,
        scopes=sorted(scopes),
        status="pending",
    )
    db.session.add(client)
    db.session.commit()
    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "scope": " ".join(client.scopes),
        "token_endpoint_auth_method": "none",
        "registration_status": "pending_admin_approval",
    }, 201


@oauth_bp.get("/oauth/authorize")
def authorize_redirect():
    if not current_app.config.get("OAUTH_ENABLED"):
        return _oauth_error("temporarily_unavailable", "OAuth is disabled", 503)
    try:
        _approved_request(request.args)
    except ValueError as exc:
        return _oauth_error("invalid_request", str(exc))
    query = urlencode({key: value for key, value in request.args.items()})
    return redirect(f"/oauth-consent?{query}", code=302)


@oauth_bp.post("/oauth/authorize")
@jwt_required()
def authorize():
    if not current_app.config.get("OAUTH_ENABLED"):
        return _oauth_error("temporarily_unavailable", "OAuth is disabled", 503)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _oauth_error("invalid_request", "JSON object required")
    try:
        client, redirect_uri, challenge, scopes = _approved_request(payload)
    except ValueError as exc:
        return _oauth_error("invalid_request", str(exc))
    if payload.get("decision") != "approve":
        target = f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': payload.get('state', '')})}"
        return {"redirect_to": target}
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return _oauth_error("access_denied", "valid user required", 403)
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role != "user":
        return _oauth_error("access_denied", "active user account required", 403)
    raw_code = _token(32)
    row = OAuthAuthorizationCode(
        code_hash=_hash(raw_code),
        client_id=client.client_id,
        user_id=user.id,
        redirect_uri=redirect_uri,
        scope=" ".join(sorted(scopes)),
        code_challenge=challenge,
        expires_at=_now() + timedelta(minutes=2),
    )
    db.session.add(row)
    db.session.commit()
    query = {"code": raw_code}
    if payload.get("state") is not None:
        query["state"] = str(payload["state"])
    return {"redirect_to": f"{redirect_uri}?{urlencode(query)}"}


def _issue_tokens(*, client_id, user_id, scope, family_id=None):
    access = _token(32)
    refresh = _token(48)
    audience = _resource_url()
    now = _now()
    db.session.add(
        OAuthAccessToken(
            token_hash=_hash(access),
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            audience=audience,
            expires_at=now + timedelta(minutes=10),
        )
    )
    db.session.add(
        OAuthRefreshToken(
            token_hash=_hash(refresh),
            family_id=family_id or str(uuid.uuid4()),
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            audience=audience,
            expires_at=now + timedelta(days=30),
        )
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": 600,
        "refresh_token": refresh,
        "scope": scope,
        "resource": audience,
    }


@oauth_bp.post("/oauth/token")
def token():
    if not current_app.config.get("OAUTH_ENABLED"):
        return _oauth_error("temporarily_unavailable", "OAuth is disabled", 503)
    grant = request.form.get("grant_type")
    client_id = str(request.form.get("client_id") or "")
    client = db.session.get(OAuthClient, client_id)
    if client is None or client.status != "approved":
        return _oauth_error("invalid_client", "client is not approved", 401)
    now = _now()
    if grant == "authorization_code":
        row = OAuthAuthorizationCode.query.filter_by(
            code_hash=_hash(request.form.get("code") or ""),
            client_id=client_id,
        ).first()
        if (
            row is None
            or row.consumed_at is not None
            or _aware(row.expires_at) <= now
            or row.redirect_uri != request.form.get("redirect_uri")
        ):
            return _oauth_error("invalid_grant", "authorization code is invalid")
        verifier = str(request.form.get("code_verifier") or "")
        calculated = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii", errors="ignore")).digest()
        ).rstrip(b"=").decode("ascii")
        if not secrets.compare_digest(calculated, row.code_challenge):
            return _oauth_error("invalid_grant", "PKCE verification failed")
        row.consumed_at = now
        result = _issue_tokens(
            client_id=client_id, user_id=row.user_id, scope=row.scope
        )
        db.session.commit()
    elif grant == "refresh_token":
        row = OAuthRefreshToken.query.filter_by(
            token_hash=_hash(request.form.get("refresh_token") or ""),
            client_id=client_id,
        ).first()
        if row is None:
            return _oauth_error("invalid_grant", "refresh token is invalid")
        if row.used_at is not None or row.revoked_at is not None:
            OAuthRefreshToken.query.filter_by(family_id=row.family_id).update(
                {"revoked_at": now}
            )
            db.session.commit()
            return _oauth_error("invalid_grant", "refresh token replay detected")
        if _aware(row.expires_at) <= now:
            return _oauth_error("invalid_grant", "refresh token expired")
        row.used_at = now
        row.revoked_at = now
        result = _issue_tokens(
            client_id=client_id,
            user_id=row.user_id,
            scope=row.scope,
            family_id=row.family_id,
        )
        db.session.commit()
    else:
        return _oauth_error("unsupported_grant_type", "unsupported grant_type")
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@oauth_bp.post("/oauth/revoke")
def revoke():
    value = request.form.get("token") or ""
    token_hash = _hash(value)
    now = _now()
    access = OAuthAccessToken.query.filter_by(token_hash=token_hash).first()
    refresh = OAuthRefreshToken.query.filter_by(token_hash=token_hash).first()
    if access:
        access.revoked_at = now
    if refresh:
        OAuthRefreshToken.query.filter_by(family_id=refresh.family_id).update(
            {"revoked_at": now}
        )
    db.session.commit()
    return "", 200


@oauth_bp.get("/api/admin/oauth-clients")
@roles_required(ROLE_ADMIN)
def admin_clients():
    rows = OAuthClient.query.order_by(OAuthClient.created_at.desc()).all()
    return {
        "items": [
            {
                "client_id": row.client_id,
                "client_name": row.client_name,
                "redirect_uris": row.redirect_uris,
                "scopes": row.scopes,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@oauth_bp.post("/api/admin/oauth-clients/<string:client_id>/decision")
@roles_required(ROLE_ADMIN)
def admin_client_decision(client_id):
    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision")
    if decision not in {"approve", "reject", "revoke"}:
        return {"message": "decision 必须是 approve、reject 或 revoke"}, 400
    client = db.session.get(OAuthClient, client_id)
    if client is None:
        return {"message": "没有找到 OAuth 客户端"}, 404
    client.status = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[decision]
    client.approved_by_user_id = g.current_user.id
    client.approved_at = _now()
    if decision == "revoke":
        now = _now()
        OAuthAccessToken.query.filter_by(client_id=client_id).update({"revoked_at": now})
        OAuthRefreshToken.query.filter_by(client_id=client_id).update({"revoked_at": now})
    db.session.commit()
    return {"item": {"client_id": client.client_id, "status": client.status}}


def validate_bearer_token(value, required_scopes=()):
    row = OAuthAccessToken.query.filter_by(token_hash=_hash(value)).first()
    now = _now()
    if (
        row is None
        or row.revoked_at is not None
        or _aware(row.expires_at) <= now
        or row.audience != _resource_url()
    ):
        return None
    if not set(required_scopes) <= set(row.scope.split()):
        return None
    user = db.session.get(User, row.user_id)
    return (row, user) if user and user.is_active else None


_MCP_TOOL_SCOPES = {
    "list_reports": {"records.read"},
    "get_report_facts": {"records.read"},
    "compute_indicator_trend": {"records.read"},
    "search_institutions": {"catalog.read"},
    "compare_packages": {"catalog.read"},
    "check_availability": {"booking.read"},
    "get_appointment_status": {"booking.read"},
    "create_booking_draft": {"booking.write"},
    "create_cancellation_draft": {"booking.write"},
    "create_waitlist_draft": {"booking.write"},
    "create_support_handoff_draft": {"support.write"},
}


def _internal_mcp_allowed():
    if not current_app.config.get("MCP_ENABLED"):
        return False
    expected = str(current_app.config.get("MCP_INTERNAL_KEY") or "")
    supplied = str(request.headers.get("X-HealthDoc-Internal-Key") or "")
    return (
        request.remote_addr in {"127.0.0.1", "::1"}
        and bool(expected)
        and secrets.compare_digest(expected, supplied)
    )


@oauth_bp.post("/api/internal/mcp/verify")
def internal_mcp_verify():
    if not _internal_mcp_allowed():
        return "", 404
    payload = request.get_json(silent=True) or {}
    result = validate_bearer_token(payload.get("token") or "")
    if result is None:
        return {"active": False}
    row, user = result
    return {
        "active": True,
        "client_id": row.client_id,
        "user_id": user.id,
        "scopes": row.scope.split(),
        "expires_at": int(_aware(row.expires_at).timestamp()),
        "resource": row.audience,
    }


@oauth_bp.post("/api/internal/mcp/tool")
def internal_mcp_tool():
    if not _internal_mcp_allowed():
        return "", 404
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "")
    required = _MCP_TOOL_SCOPES.get(name)
    if required is None:
        return {"message": "tool is not allowed"}, 404
    verified = validate_bearer_token(payload.get("token") or "", required)
    if verified is None:
        return {"message": "invalid token or insufficient scope"}, 401
    _token_row, user = verified
    if name.startswith("create_") and not current_app.config.get("AGENT_WRITE_ENABLED"):
        return {"message": "Agent write tools are disabled"}, 503
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    thread = AgentThread(
        id=thread_id,
        user_id=user.id,
        encrypted_state=encrypt_json(
            {"messages": [], "active_subject_id": user.id, "source": "mcp"},
            purpose=f"agent-thread:{thread_id}",
        ),
    )
    run = AgentRun(
        id=run_id,
        thread_id=thread_id,
        user_id=user.id,
        status="running",
        intent=name,
        model_name="mcp-client",
        prompt_version="mcp-v1",
    )
    db.session.add_all([thread, run])
    db.session.flush()
    try:
        result = execute_tool(
            name,
            payload.get("arguments") or {},
            user=user,
            thread_id=thread_id,
            run_id=run_id,
        )
        run.status = "waiting_approval" if result.get("approval_required") else "completed"
        run.completed_at = _now()
        if result.get("approval_required"):
            public_base = (
                str(current_app.config.get("OAUTH_ISSUER") or "").rstrip("/")
                or request.url_root.rstrip("/")
            )
            result["approval_url"] = (
                public_base + f"/agent-actions/{result['action_id']}"
            )
        db.session.commit()
        return {"result": result}
    except (ValueError, LookupError, PermissionError) as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
