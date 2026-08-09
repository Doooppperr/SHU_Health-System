"""Safely add idempotent demo subjects for institution audience portraits."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.demo_v7 import (  # noqa: E402
    AUDIENCE_SAMPLE_SCENARIOS,
    ensure_demo_audience_samples,
)
from app.extensions import db  # noqa: E402
from app.models import Institution  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.yes:
        raise SystemExit("--apply requires --yes")

    app = create_app(os.getenv("APP_CONFIG", "development"))
    with app.app_context():
        if not args.apply:
            print(json.dumps({
                "action": "add inactive audience-profile demo subjects",
                "sample_users": len(AUDIENCE_SAMPLE_SCENARIOS),
                "active_branches": Institution.query.filter_by(is_active=True).count(),
                "apply": False,
            }, ensure_ascii=False, indent=2))
            return 0
        try:
            result = ensure_demo_audience_samples()
        except Exception:
            db.session.rollback()
            raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
