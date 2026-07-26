"""Validate the schema-v10 demonstration dataset and attachment manifest."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import IndicatorDict, InstitutionReport, ReportAsset, ReportIndicator, User  # noqa: E402


def main():
    app = create_app("development")
    with app.app_context():
        user = User.query.filter_by(username="test1", role="user").one()
        active_ids = {row.id for row in IndicatorDict.query.all()}
        counts = Counter(
            indicator_id
            for (indicator_id,) in ReportIndicator.query.join(InstitutionReport).filter(
                InstitutionReport.matched_user_id == user.id,
                InstitutionReport.status == "published",
            ).with_entities(ReportIndicator.indicator_dict_id).all()
        )
        missing = sorted(
            db.session.get(IndicatorDict, indicator_id).code
            for indicator_id in active_ids
            if counts[indicator_id] < 8
        )
        if missing:
            raise RuntimeError(f"test1 indicators below eight records: {missing}")
        statuses = {
            status
            for (status,) in ReportIndicator.query.join(InstitutionReport).filter(
                InstitutionReport.matched_user_id == user.id,
            ).with_entities(ReportIndicator.result_status).distinct().all()
        }
        if not {"high", "low", "normal", "positive", "negative"} <= statuses:
            raise RuntimeError(f"demo result directions are incomplete: {sorted(statuses)}")
        untyped_assets = ReportAsset.query.filter(ReportAsset.asset_type_id.is_(None)).count()
        if untyped_assets:
            raise RuntimeError(f"{untyped_assets} demo report assets have no standard slot")
        upload_root = Path(app.config["UPLOAD_DIR"])
        for asset in ReportAsset.query.all():
            path = (upload_root / asset.storage_key).resolve()
            if not path.is_file():
                raise RuntimeError(f"missing demo asset: {asset.storage_key}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != asset.sha256:
                raise RuntimeError(f"demo asset hash mismatch: {asset.storage_key}")
        summary = {
            "test1_active_indicators": len(active_ids),
            "minimum_records_per_indicator": min(counts[indicator_id] for indicator_id in active_ids),
            "result_statuses": sorted(statuses),
            "published_reports": InstitutionReport.query.filter_by(
                matched_user_id=user.id,
                status="published",
            ).count(),
            "typed_assets": ReportAsset.query.count(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
