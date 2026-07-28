"""Upgrade institution1 sibling-branch archives to complete package-aligned records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.demo_v7 import enrich_institution1_shared_archives  # noqa: E402
from app.extensions import db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.yes:
        raise SystemExit("--apply requires --yes")

    app = create_app("development")
    with app.app_context():
        if not args.apply:
            print(json.dumps({
                "database": str(Path(app.config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///"))),
                "action": "enrich institution1 sibling-branch published archives",
                "apply": False,
            }, ensure_ascii=False, indent=2))
            return 0
        try:
            result = enrich_institution1_shared_archives()
        except Exception:
            db.session.rollback()
            raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
