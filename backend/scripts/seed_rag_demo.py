from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    FriendRelation,
    HealthIndicator,
    HealthRecord,
    IndicatorDict,
    Institution,
    Package,
    User,
)
from app.services.indicator_values import evaluate_is_abnormal  # noqa: E402


MANIFEST_PATH = BACKEND_DIR / "instance" / "rag" / "demo_seed.json"
USERNAMES = [f"rag_demo_{index:02d}" for index in range(1, 6)]
START_YEAR = 2021
START_MONTH = 9
RECORDS_PER_USER = 20


def _exam_date(index: int) -> date:
    month_index = START_MONTH - 1 + index * 3
    return date(START_YEAR + month_index // 12, month_index % 12 + 1, 15)


BASE = {
    "BMI": Decimal("21.0"),
    "FBG": Decimal("4.8"),
    "TC": Decimal("4.4"),
    "TG": Decimal("1.1"),
    "HDL": Decimal("1.35"),
    "LDL": Decimal("2.5"),
    "ALT": Decimal("24"),
    "AST": Decimal("23"),
    "UA": Decimal("330"),
    "CREA": Decimal("82"),
}


def _value(profile: int, code: str, index: int) -> Decimal:
    value = BASE[code]
    wave = Decimal((index % 5) - 2) / Decimal("20")
    if profile == 0:
        if code == "BMI":
            value += Decimal("0.22") * index
        elif code == "FBG":
            value += Decimal("0.09") * index
        elif code == "TG":
            value += Decimal("0.06") * index
        else:
            value += wave
    elif profile == 1:
        starts = {"TC": "6.2", "TG": "2.1", "HDL": "0.9", "LDL": "4.1"}
        slopes = {"TC": "-0.08", "TG": "-0.05", "HDL": "0.025", "LDL": "-0.07"}
        if code in starts:
            value = Decimal(starts[code]) + Decimal(slopes[code]) * index
        else:
            value += wave
    elif profile == 2:
        if code == "ALT":
            value = Decimal("58") if index in {4, 5, 11, 12, 18} else Decimal("25") + wave
        elif code == "AST":
            value = Decimal("47") if index in {4, 5, 11, 12, 18} else Decimal("23") + wave
        else:
            value += wave
    elif profile == 3:
        if code == "UA":
            value = Decimal("330") + Decimal("8") * index
        elif code == "CREA":
            value = Decimal("82") + Decimal("2.8") * index
        else:
            value += wave
    else:
        value += wave
    return value.quantize(Decimal("0.01"))


def _read_manifest():
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_manifest(value):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MANIFEST_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(MANIFEST_PATH)


def verify(manifest=None):
    manifest = manifest or _read_manifest()
    if not manifest:
        raise RuntimeError("demo seed manifest was not found")
    user_ids = manifest["user_ids"]
    record_ids = manifest["record_ids"]
    users = User.query.filter(User.id.in_(user_ids)).count()
    records = HealthRecord.query.filter(HealthRecord.id.in_(record_ids)).count()
    indicators = (
        HealthIndicator.query.filter(HealthIndicator.record_id.in_(record_ids)).count()
    )
    if (users, records, indicators) != (5, 100, 900):
        raise RuntimeError(
            f"demo seed verification failed: users={users}, records={records}, indicators={indicators}"
        )
    return {"users": users, "records": records, "indicators": indicators}


def apply_seed():
    existing_manifest = _read_manifest()
    if existing_manifest:
        return {"status": "already_applied", **verify(existing_manifest)}
    collisions = User.query.filter(User.username.in_(USERNAMES)).all()
    if collisions:
        raise RuntimeError("reserved rag_demo usernames already exist without a matching manifest")
    password = os.getenv("RAG_DEMO_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("RAG_DEMO_PASSWORD must contain at least 12 characters")

    definitions = {
        item.code: item for item in IndicatorDict.query.order_by(IndicatorDict.id).all()
    }
    if set(definitions) != set(BASE):
        raise RuntimeError("the demo generator requires the current ten indicator codes")
    institutions = Institution.query.order_by(Institution.id).all()
    packages_by_institution = {
        item.id: Package.query.filter_by(institution_id=item.id)
        .order_by(Package.id)
        .first()
        for item in institutions
    }
    if not institutions:
        raise RuntimeError("institution seed data is missing")

    user_ids = []
    record_ids = []
    try:
        users = []
        for username in USERNAMES:
            user = User(username=username, role="user")
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            users.append(user)
            user_ids.append(user.id)

        ordered_codes = list(BASE)
        for profile, user in enumerate(users):
            for index in range(RECORDS_PER_USER):
                institution = None if index % 5 == 0 else institutions[index % len(institutions)]
                package = packages_by_institution.get(institution.id) if institution else None
                record = HealthRecord(
                    owner_id=user.id,
                    uploader_id=user.id,
                    institution_id=institution.id if institution else None,
                    package_id=package.id if package else None,
                    exam_date=_exam_date(index),
                    status="confirmed",
                )
                db.session.add(record)
                db.session.flush()
                record_ids.append(record.id)
                omitted_code = ordered_codes[(index + profile * 2) % len(ordered_codes)]
                for code in ordered_codes:
                    if code == omitted_code:
                        continue
                    definition = definitions[code]
                    value = _value(profile, code, index)
                    rendered = format(value, "f")
                    if "." in rendered:
                        rendered = rendered.rstrip("0").rstrip(".")
                    db.session.add(
                        HealthIndicator(
                            record_id=record.id,
                            indicator_dict_id=definition.id,
                            value=rendered,
                            is_abnormal=evaluate_is_abnormal(definition, rendered),
                            source="manual",
                        )
                    )

        db.session.add(
            FriendRelation(
                user_id=users[0].id,
                friend_user_id=users[1].id,
                relation_name="已授权演示亲友",
                auth_status=True,
            )
        )
        db.session.add(
            FriendRelation(
                user_id=users[0].id,
                friend_user_id=users[2].id,
                relation_name="未授权演示亲友",
                auth_status=False,
            )
        )
        db.session.commit()
        manifest = {
            "schema_version": 1,
            "user_ids": user_ids,
            "record_ids": record_ids,
            "usernames": USERNAMES,
            "records_per_user": RECORDS_PER_USER,
        }
        _write_manifest(manifest)
        return {"status": "applied", **verify(manifest)}
    except Exception:
        db.session.rollback()
        raise


def cleanup():
    manifest = _read_manifest()
    if not manifest:
        return {"status": "not_applied", "users": 0, "records": 0, "indicators": 0}
    user_ids = manifest["user_ids"]
    record_ids = manifest["record_ids"]
    try:
        indicators = HealthIndicator.query.filter(
            HealthIndicator.record_id.in_(record_ids)
        ).delete(synchronize_session=False)
        records = HealthRecord.query.filter(HealthRecord.id.in_(record_ids)).delete(
            synchronize_session=False
        )
        FriendRelation.query.filter(
            or_(
                FriendRelation.user_id.in_(user_ids),
                FriendRelation.friend_user_id.in_(user_ids),
            )
        ).delete(synchronize_session=False)
        users = User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.session.commit()
        MANIFEST_PATH.unlink(missing_ok=True)
        return {
            "status": "cleaned",
            "users": users,
            "records": records,
            "indicators": indicators,
        }
    except Exception:
        db.session.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description="Create deterministic local RAG demo records")
    parser.add_argument("command", nargs="?", choices=["dry-run", "apply", "verify", "cleanup"], default="dry-run")
    args = parser.parse_args()
    if args.command == "dry-run":
        print(json.dumps({"status": "dry_run", "users": 5, "records": 100, "indicators": 900}))
        return
    app = create_app("development")
    with app.app_context():
        if args.command == "apply":
            result = apply_seed()
        elif args.command == "verify":
            result = {"status": "verified", **verify()}
        else:
            result = cleanup()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
