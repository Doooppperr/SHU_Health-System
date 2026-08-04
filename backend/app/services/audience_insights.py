from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.ai.service import AiConfigurationError, AiProviderError, get_ai_client
from app.extensions import db
from app.models import InstitutionAudienceInsightCache, InstitutionReport, Package


AGE_BUCKETS = (
    ("under_18", "<18"),
    ("18_29", "18–29"),
    ("30_39", "30–39"),
    ("40_49", "40–49"),
    ("50_59", "50–59"),
    ("60_plus", "60+"),
    ("unknown", "未知"),
)
GENDER_LABELS = {
    "female": "女性",
    "male": "男性",
    "other": "其他",
    "undisclosed": "未公开",
    "unknown": "未知",
}
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def _age_on(birth_date, on_date):
    if birth_date is None:
        return None
    return on_date.year - birth_date.year - (
        (on_date.month, on_date.day) < (birth_date.month, birth_date.day)
    )


def _age_bucket(age):
    if age is None or age < 0:
        return "unknown"
    if age < 18:
        return "under_18"
    if age < 30:
        return "18_29"
    if age < 40:
        return "30_39"
    if age < 50:
        return "40_49"
    if age < 60:
        return "50_59"
    return "60_plus"


def _ranked(counter, labels=None):
    total = sum(counter.values())
    return [
        {
            "key": key,
            "label": (labels or {}).get(key, key),
            "count": count,
            "percentage": round(count * 100 / total, 1) if total else 0.0,
        }
        for key, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))
    ]


def _aggregate(reports, *, scope, period_days, period_start, package_catalog):
    gender_counts = Counter()
    age_counts = Counter()
    package_counts = Counter()
    branch_counts = Counter()
    seen_people = set()

    for report in sorted(
        reports,
        key=lambda row: (row.exam_date or date.min, row.id),
        reverse=True,
    ):
        appointment = report.appointment
        owner = report.owner
        person_key = (
            ("user", report.matched_user_id)
            if report.matched_user_id
            else ("health", hashlib.sha256(report.subject_health_id.encode()).hexdigest())
        )
        if person_key not in seen_people:
            seen_people.add(person_key)
            gender = (
                appointment.user_gender_snapshot if appointment else None
            ) or (owner.gender if owner else None) or "unknown"
            if gender not in GENDER_LABELS:
                gender = "unknown"
            birth_date = (
                appointment.user_birth_date_snapshot if appointment else None
            ) or (owner.birth_date if owner else None)
            gender_counts[gender] += 1
            age_counts[_age_bucket(_age_on(birth_date, report.exam_date))] += 1

        package_name = (
            appointment.package_name_snapshot if appointment else None
        ) or (report.package.name if report.package else None) or "未标注套餐"
        package_counts[package_name] += 1
        if report.institution:
            branch_counts[report.institution.branch_name] += 1

    for key, _label in AGE_BUCKETS:
        age_counts.setdefault(key, 0)
    for key in GENDER_LABELS:
        gender_counts.setdefault(key, 0)

    return {
        "scope": scope,
        "period_days": period_days,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": datetime.now(BUSINESS_TZ).date().isoformat(),
        "report_count": len(reports),
        "unique_user_count": len(seen_people),
        "gender_distribution": _ranked(gender_counts, GENDER_LABELS),
        "age_distribution": _ranked(age_counts, dict(AGE_BUCKETS)),
        "package_ranking": _ranked(package_counts)[:10],
        "package_catalog": package_catalog,
        "branch_distribution": _ranked(branch_counts) if scope == "organization" else [],
    }


def _first_positive(rows):
    return next((row for row in rows if row["count"] > 0), None)


def _deterministic_analysis(aggregate):
    if aggregate["report_count"] == 0:
        return "当前统计周期内暂无已发布体检报告，建议先积累有效样本后再进行人群与套餐决策。"
    gender = _first_positive(aggregate["gender_distribution"])
    age = _first_positive(aggregate["age_distribution"])
    package = _first_positive(aggregate["package_ranking"])
    observations = [
        f"当前样本包含{aggregate['unique_user_count']}名用户、{aggregate['report_count']}份已发布报告。"
    ]
    if aggregate["unique_user_count"] < 10:
        observations.append("当前样本量较少，画像和套餐建议仅供初步参考。")
    if gender:
        observations.append(
            f"{gender['label']}用户占比最高，为{gender['percentage']}%。"
        )
    if age:
        observations.append(
            f"{age['label']}是主要年龄段，占比{age['percentage']}%。"
        )
    if package:
        observations.append(
            f"最受欢迎套餐为“{package['label']}”，共完成{package['count']}次。"
        )

    age_key = age["key"] if age else "unknown"
    gender_key = gender["key"] if gender else "unknown"
    if gender_key == "female":
        suggestion = "可评估增开女性专项、妇科筛查及相应复查组合套餐。"
    elif gender_key == "male":
        suggestion = "可评估增开男性专项、代谢与心血管风险筛查组合套餐。"
    elif age_key in {"40_49", "50_59", "60_plus"}:
        suggestion = "可评估增开慢病风险、心脑血管及肿瘤早筛类套餐。"
    else:
        suggestion = "可围绕主力年龄段设计基础体检与职业健康升级套餐。"
    observations.append(suggestion)
    observations.append("以上建议仅基于去标识化聚合数据，新增套餐前仍需结合医疗资源与合规要求评估。")
    return "".join(observations)


def _ai_analysis(aggregate, fallback):
    if aggregate["report_count"] == 0:
        return fallback, "deterministic", None
    try:
        client = get_ai_client(current_app.config)
        completion = client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是体检机构运营分析助手。只能使用给定的去标识化聚合数据，"
                        "不得推断或索取任何个人身份、病历或单人健康信息。请输出 JSON，"
                        "字段 analysis_text 为简洁中文画像、套餐观察和可执行增开建议，"
                        "建议只能基于聚合统计与给定套餐目录，并明确需结合医疗资源与合规评估；"
                        "样本少于10人时必须使用审慎措辞。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(aggregate, ensure_ascii=False, sort_keys=True),
                },
            ],
            json_output=True,
            max_tokens=500,
        )
        decoded = json.loads(completion.content)
        analysis = str(
            decoded.get("analysis_text") or decoded.get("answer") or ""
        ).strip()
        if not analysis:
            raise ValueError("AI response did not include analysis text")
        return analysis, "ai", getattr(client, "model", None)
    except (AiConfigurationError, AiProviderError, ValueError, json.JSONDecodeError):
        current_app.logger.info(
            "Audience insight AI unavailable; using deterministic analysis",
            exc_info=True,
        )
        return fallback, "deterministic", None


def get_audience_insight(
    institution,
    *,
    scope,
    period_days,
    _retry_on_conflict=True,
):
    if scope == "organization":
        scope_id = institution.organization_id
        institution_ids = [
            branch.id
            for branch in institution.organization.branches
            if branch.is_active
        ]
    else:
        scope = "branch"
        scope_id = institution.id
        institution_ids = [institution.id]

    business_today = datetime.now(BUSINESS_TZ).date()
    period_start = (
        business_today - timedelta(days=period_days - 1)
        if period_days
        else None
    )
    query = InstitutionReport.query.filter(
        InstitutionReport.institution_id.in_(institution_ids),
        InstitutionReport.status == "published",
    )
    if period_start:
        query = query.filter(InstitutionReport.exam_date >= period_start)
    reports = query.all()
    package_catalog = []
    for row in Package.query.filter(
            Package.institution_id.in_(institution_ids),
            Package.is_active.is_(True),
            Package.current_version_id.is_not(None),
        ).order_by(Package.name, Package.id):
        current_version = next(
            (item for item in row.versions if item.id == row.current_version_id),
            None,
        )
        package_catalog.append(
            {
                "name": row.name,
                "current_price": str(row.price),
                "health_domains": sorted(
                    {
                        link.domain.name
                        for link in (current_version.domains if current_version else [])
                        if link.domain is not None
                    }
                ),
            }
        )
    aggregate = _aggregate(
        reports,
        scope=scope,
        period_days=period_days,
        period_start=period_start,
        package_catalog=package_catalog,
    )
    canonical = json.dumps(
        aggregate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    period_key = "all" if not period_days else f"days:{period_days}"
    now = datetime.now(timezone.utc)
    cached = InstitutionAudienceInsightCache.query.filter(
        InstitutionAudienceInsightCache.scope_type == scope,
        InstitutionAudienceInsightCache.scope_id == scope_id,
        InstitutionAudienceInsightCache.period_key == period_key,
        InstitutionAudienceInsightCache.data_digest == digest,
        InstitutionAudienceInsightCache.expires_at > now,
    ).first()
    if cached is not None:
        return cached, True

    fallback = _deterministic_analysis(aggregate)
    analysis, source, model_name = _ai_analysis(aggregate, fallback)
    cached = InstitutionAudienceInsightCache.query.filter_by(
        scope_type=scope,
        scope_id=scope_id,
        period_key=period_key,
    ).first()
    if cached is None:
        cached = InstitutionAudienceInsightCache(
            scope_type=scope,
            scope_id=scope_id,
            period_key=period_key,
        )
        db.session.add(cached)
    cached.data_digest = digest
    cached.aggregate_payload = aggregate
    cached.analysis_text = analysis
    cached.model_name = model_name
    cached.source = source
    cached.generated_at = now
    cached.expires_at = now + timedelta(hours=24)
    try:
        db.session.commit()
    except IntegrityError:
        # Two first-time requests for the same scope/period can both observe
        # an empty cache and race on the unique scope key. Keep the winner and
        # return it when it represents the same aggregate snapshot. If data
        # changed during generation, recompute once instead of overwriting a
        # newer digest with stale analysis.
        db.session.rollback()
        winner = InstitutionAudienceInsightCache.query.filter_by(
            scope_type=scope,
            scope_id=scope_id,
            period_key=period_key,
        ).filter(
            InstitutionAudienceInsightCache.expires_at > now,
        ).first()
        if (
            winner is not None
            and winner.data_digest == digest
        ):
            return winner, True
        if _retry_on_conflict:
            return get_audience_insight(
                institution,
                scope=scope,
                period_days=period_days,
                _retry_on_conflict=False,
            )
        raise
    return cached, False
