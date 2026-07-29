"""Expire stale Agent drafts and cryptographically erase expired thread state."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("FLASK_ENV", "production")

from app import create_app  # noqa: E402
from app.agent.crypto import encrypt_json  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AgentPendingAction, AgentThread  # noqa: E402


def cleanup() -> tuple[int, int]:
    app = create_app(os.getenv("APP_CONFIG", "production"))
    with app.app_context():
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(
            hours=int(app.config.get("AGENT_THREAD_TTL_HOURS", 24))
        )
        expired_actions = AgentPendingAction.query.filter(
            AgentPendingAction.status == "pending",
            AgentPendingAction.expires_at <= now,
        ).update({"status": "expired"}, synchronize_session=False)
        stale_threads = AgentThread.query.filter(
            AgentThread.status == "active",
            AgentThread.last_activity_at <= cutoff,
        ).all()
        for row in stale_threads:
            row.status = "cleared"
            row.encrypted_state = encrypt_json(
                {"messages": [], "active_subject_id": row.user_id},
                purpose=f"agent-thread:{row.id}",
            )
            row.cleared_at = now
            AgentPendingAction.query.filter_by(
                thread_id=row.id, status="pending"
            ).update({"status": "expired"}, synchronize_session=False)
        db.session.commit()
        return expired_actions, len(stale_threads)


if __name__ == "__main__":
    actions, threads = cleanup()
    print(f"expired_actions={actions}")
    print(f"cleared_threads={threads}")
