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
from app.demo_indicator_values import DEMO_REALISTIC_SERIES  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import IndicatorDict, InstitutionReport, ReportAsset, ReportIndicator, User  # noqa: E402


STORY_REPORT_KINDS = {"v10_comprehensive_exam", "v10_targeted_follow_up"}
CORE_TREND_CODES = {
    "BMI", "WEIGHT", "WAIST", "SBP", "DBP", "FBG", "HBA1C",
    "TC", "TG", "HDL", "LDL", "ALT", "AST", "GGT", "UA", "CREA",
}
PLAUSIBLE_SERIES_BOUNDS = {
    "HEIGHT": (80, 250),
    "WEIGHT": (20, 500),
    "BMI": (10, 60),
    "WAIST": (40, 180),
    "HIP": (50, 200),
    "WHR": (0.4, 1.5),
    "SBP": (60, 240),
    "DBP": (35, 150),
    "HR": (35, 220),
    "TEMP": (30, 43),
    "SPO2": (50, 100),
    "FBG": (2, 25),
}


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
        core_missing = sorted(
            code
            for code in CORE_TREND_CODES
            if counts[IndicatorDict.query.filter_by(code=code).one().id] < 12
        )
        if core_missing:
            raise RuntimeError(f"test1 core trends below twelve records: {core_missing}")

        story_reports = [
            report
            for report in InstitutionReport.query.filter_by(
                matched_user_id=user.id,
                status="published",
            ).all()
            if (report.ocr_diagnostics or {}).get("import_kind") in STORY_REPORT_KINDS
        ]
        story_kinds = Counter(
            (report.ocr_diagnostics or {}).get("import_kind")
            for report in story_reports
        )
        if story_kinds != {
            "v10_comprehensive_exam": 5,
            "v10_targeted_follow_up": 11,
        }:
            raise RuntimeError(f"unexpected test1 four-year story: {dict(story_kinds)}")
        story_dates = sorted(report.exam_date for report in story_reports)
        if not story_dates or (story_dates[-1] - story_dates[0]).days < 1400:
            raise RuntimeError("test1 story does not cover approximately four years")
        if len({report.institution_id for report in story_reports}) < 3:
            raise RuntimeError("test1 story must include at least three institutions")

        story_ids = [report.id for report in story_reports]
        story_sources = {
            source
            for (source,) in ReportIndicator.query.filter(
                ReportIndicator.report_id.in_(story_ids)
            ).with_entities(ReportIndicator.input_source).distinct().all()
        }
        if not {"manual", "ocr"} <= story_sources:
            raise RuntimeError(f"test1 story input sources are incomplete: {sorted(story_sources)}")

        descriptive_codes = {"HEIGHT", "WEIGHT", "HIP"}
        leaked_descriptive_statuses = ReportIndicator.query.join(IndicatorDict).filter(
            ReportIndicator.report_id.in_(story_ids),
            IndicatorDict.code.in_(descriptive_codes),
            ReportIndicator.result_status != "unknown",
        ).count()
        if leaked_descriptive_statuses:
            raise RuntimeError("descriptive measurements must not carry normal/abnormal labels")

        for code, (low, high) in PLAUSIBLE_SERIES_BOUNDS.items():
            values = [float(value) for value in DEMO_REALISTIC_SERIES[code]]
            if not all(low <= value <= high for value in values):
                raise RuntimeError(f"implausible synthetic series for {code}")
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
            "minimum_core_records": min(
                counts[IndicatorDict.query.filter_by(code=code).one().id]
                for code in CORE_TREND_CODES
            ),
            "result_statuses": sorted(statuses),
            "published_reports": InstitutionReport.query.filter_by(
                matched_user_id=user.id,
                status="published",
            ).count(),
            "story_reports": dict(story_kinds),
            "story_span_days": (story_dates[-1] - story_dates[0]).days,
            "story_institutions": len({report.institution_id for report in story_reports}),
            "story_input_sources": sorted(story_sources),
            "typed_assets": ReportAsset.query.count(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
