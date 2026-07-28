"""Validate the schema-v10 preset business dataset and attachment manifest."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import String, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.demo_indicator_values import DEMO_REALISTIC_SERIES  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    IndicatorDict, Institution, InstitutionReport, Package, ReportAsset,
    ReportIndicator, ReportTextResult, User,
)
from app.services.report_conclusions import (  # noqa: E402
    build_domain_conclusion,
    missing_conclusion_domains,
    represented_domains,
)


STORY_REPORT_KINDS = {"comprehensive_exam", "targeted_follow_up"}
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
    "INS": (1, 40),
    "RBC": (3, 7),
    "HGB": (90, 200),
    "HCT": (25, 60),
}

BANNED_PRODUCT_COPY = (
    "合成测试", "演示数据", "功能验收", "仅用于展示", "不作为诊断依据",
    "不对应系统用户", "开放授权样例", "隐私快照", "法律安全提示",
)


def validate_product_copy() -> int:
    paths = [
        PROJECT_DIR / "README.md",
        BACKEND_DIR / "README.md",
        PROJECT_DIR / "frontend" / "README.md",
        *(PROJECT_DIR / "项目文档").glob("*.md"),
        *(PROJECT_DIR / "frontend" / "src").rglob("*.vue"),
        *(
            path for path in (PROJECT_DIR / "frontend" / "src").rglob("*.js")
            if not path.name.endswith(".test.js")
        ),
    ]
    leaks = []
    for path in paths:
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for fragment in BANNED_PRODUCT_COPY:
            if fragment in content:
                leaks.append(f"{path.relative_to(PROJECT_DIR)}: {fragment}")
    if leaks:
        raise RuntimeError("product copy contains banned fragments: " + "; ".join(leaks))
    return len(paths)


def validate_report_package_alignment(reports) -> int:
    errors = []
    for report in reports:
        if report.package is None or report.package_version is None:
            errors.append(f"report {report.id}: missing package or package version")
            continue
        if report.package_version.package_id != report.package_id:
            errors.append(f"report {report.id}: package version belongs to another package")
        if report.package.institution_id != report.institution_id:
            errors.append(f"report {report.id}: package belongs to another institution")
        if report.appointment is not None:
            if report.appointment.institution_id != report.institution_id:
                errors.append(f"report {report.id}: appointment institution mismatch")
            if report.appointment.package_id != report.package_id:
                errors.append(f"report {report.id}: appointment package mismatch")
            if report.appointment.package_version_id != report.package_version_id:
                errors.append(f"report {report.id}: appointment package version mismatch")

        allowed = {row.health_domain_id for row in report.package_version.domains}
        indicator_domains = {
            item.display_domain_id
            for item in report.indicators
            if item.display_domain_id
        }
        asset_domains = {
            item.health_domain_id
            for item in report.assets
            if item.health_domain_id
        }
        data_domains = indicator_domains | asset_domains
        conclusion_domains = [item.health_domain_id for item in report.text_results]
        conclusion_domain_set = set(conclusion_domains)

        if not allowed:
            errors.append(f"report {report.id}: package version has no health domain")
        if not data_domains:
            errors.append(f"report {report.id}: report has no indicator or attachment domain")
        if data_domains - allowed:
            errors.append(
                f"report {report.id}: data domains outside package {sorted(data_domains - allowed)}"
            )
        if conclusion_domain_set != data_domains:
            errors.append(
                f"report {report.id}: conclusions {sorted(conclusion_domain_set)} "
                f"do not match data {sorted(data_domains)}"
            )
        if len(conclusion_domains) != len(conclusion_domain_set):
            errors.append(f"report {report.id}: duplicate domain conclusions")

        import_kind = (report.ocr_diagnostics or {}).get("import_kind")
        if import_kind in STORY_REPORT_KINDS and data_domains != allowed:
            errors.append(
                f"report {report.id}: story report covers {sorted(data_domains)}, "
                f"package requires {sorted(allowed)}"
            )

        for item in report.indicators:
            linked = {
                row.health_domain_id
                for row in item.indicator_dict.domain_links
            } if item.indicator_dict else set()
            if item.display_domain_id not in allowed:
                errors.append(
                    f"report {report.id}: indicator {item.id} outside package domain"
                )
            if item.display_domain_id not in linked:
                errors.append(
                    f"report {report.id}: indicator {item.id} display domain is not configured"
                )
        for asset in report.assets:
            if asset.health_domain_id not in allowed:
                errors.append(
                    f"report {report.id}: attachment {asset.id} outside package domain"
                )
            if asset.asset_type and asset.asset_type.health_domain_id != asset.health_domain_id:
                errors.append(
                    f"report {report.id}: attachment {asset.id} slot domain mismatch"
                )
    if errors:
        preview = "; ".join(errors[:20])
        suffix = f"; ... {len(errors) - 20} more" if len(errors) > 20 else ""
        raise RuntimeError(f"report/package alignment failed: {preview}{suffix}")
    return len(reports)


def validate_conclusion_facts(reports) -> int:
    history = defaultdict(dict)
    checked = 0
    for report in sorted(
        reports,
        key=lambda row: (row.matched_user_id or 0, row.exam_date, row.id),
    ):
        owner_history = history[report.matched_user_id or 0]
        conclusions = {
            row.health_domain_id: row
            for row in report.text_results
        }
        for domain in represented_domains(report):
            expected_title, expected_body = build_domain_conclusion(
                report, domain, owner_history,
            )
            actual = conclusions.get(domain.id)
            if actual is None:
                raise RuntimeError(
                    f"report {report.id} is missing conclusion for domain {domain.id}"
                )
            if actual.title != expected_title or actual.body != expected_body:
                raise RuntimeError(
                    f"report {report.id} conclusion facts do not match domain {domain.id}"
                )
            checked += 1
        for item in report.indicators:
            if item.indicator_dict:
                owner_history[item.indicator_dict.code] = item
    return checked


def validate_institution1_shared_archives():
    staff = User.query.filter_by(username="institution1_staff1").one()
    current = staff.managed_institution
    shared = (
        InstitutionReport.query
        .join(Institution)
        .filter(
            Institution.organization_id == current.organization_id,
            InstitutionReport.institution_id != current.id,
            InstitutionReport.status == "published",
        )
        .all()
    )
    if len(shared) < 10:
        raise RuntimeError(
            f"institution1 shared archive volume is too small: {len(shared)}"
        )
    sparse = {
        report.id: len(report.indicators)
        for report in shared
        if len(report.indicators) < 12
    }
    if sparse:
        raise RuntimeError(
            f"institution1 shared archives are too sparse: {sparse}"
        )
    return len(shared), min(len(report.indicators) for report in shared)


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
            "comprehensive_exam": 9,
            "targeted_follow_up": 20,
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
        positive_codes = Counter(
            row.indicator_dict.code
            for row in ReportIndicator.query.filter(
                ReportIndicator.report_id.in_(story_ids),
                ReportIndicator.result_status == "positive",
            ).all()
            if row.indicator_dict is not None
        )
        if positive_codes != {"U_PRO": 1, "FOBT": 1}:
            raise RuntimeError(
                f"qualitative abnormalities are not sparse and realistic: {dict(positive_codes)}"
            )
        hearing_rows = ReportIndicator.query.join(IndicatorDict).filter(
            ReportIndicator.report_id.in_(story_ids),
            IndicatorDict.code == "HEARING",
        ).all()
        if not hearing_rows or any(
            row.value != "未见明显异常" or row.result_status != "normal"
            for row in hearing_rows
        ):
            raise RuntimeError("hearing findings must use a clinical finding instead of polarity")

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
                raise RuntimeError(f"implausible indicator series for {code}")
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
            raise RuntimeError(f"{untyped_assets} report assets have no standard slot")
        reports = InstitutionReport.query.filter_by(status="published").all()
        aligned_reports = validate_report_package_alignment(reports)
        conclusion_facts = validate_conclusion_facts(reports)
        shared_count, minimum_shared_indicators = validate_institution1_shared_archives()
        invalid_package_scopes = sorted({
            package.gender_scope
            for package in Package.query.all()
            if package.gender_scope not in {"all", "male", "female"}
        })
        if invalid_package_scopes:
            raise RuntimeError(
                f"packages use redundant or invalid gender scopes: {invalid_package_scopes}"
            )
        missing_conclusions = {
            report.id: [domain.name for domain in missing_conclusion_domains(report)]
            for report in reports
            if missing_conclusion_domains(report)
        }
        if missing_conclusions:
            raise RuntimeError(f"reports missing domain conclusions: {missing_conclusions}")
        represented_pairs = sum(
            len({
                *(item.display_domain_id for item in report.indicators if item.display_domain_id),
                *(item.health_domain_id for item in report.assets if item.health_domain_id),
            })
            for report in reports
        )
        if ReportTextResult.query.count() != represented_pairs:
            raise RuntimeError(
                "report conclusion count does not match represented report-domain pairs"
            )
        inspector = inspect(db.engine)
        quote = db.engine.dialect.identifier_preparer.quote
        text_values = []
        for table_name in inspector.get_table_names():
            for column in inspector.get_columns(table_name):
                if not isinstance(column["type"], String):
                    continue
                rows = db.session.execute(text(
                    f"SELECT {quote(column['name'])} FROM {quote(table_name)} "
                    f"WHERE {quote(column['name'])} IS NOT NULL"
                ))
                text_values.extend(str(value) for (value,) in rows)
        leaks = sorted({
            fragment for fragment in BANNED_PRODUCT_COPY
            if any(fragment in value for value in text_values)
        })
        if leaks:
            raise RuntimeError(f"business copy contains banned fragments: {leaks}")
        upload_root = Path(app.config["UPLOAD_DIR"]).resolve()
        for asset in ReportAsset.query.all():
            path = (upload_root / asset.storage_key).resolve()
            if not path.is_file():
                raise RuntimeError(f"missing report asset: {asset.storage_key}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != asset.sha256:
                raise RuntimeError(f"report asset hash mismatch: {asset.storage_key}")
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
            "report_domain_conclusions": represented_pairs,
            "fact_aligned_conclusions": conclusion_facts,
            "package_aligned_reports": aligned_reports,
            "institution1_shared_reports": shared_count,
            "minimum_shared_report_indicators": minimum_shared_indicators,
            "package_gender_scopes": sorted({
                package.gender_scope for package in Package.query.all()
            }),
            "product_copy_files_scanned": validate_product_copy(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

