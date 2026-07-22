from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from flask import current_app, g, request, send_file

from app.extensions import db
from app.health_data_v7 import health_data_v7_bp
from app.models import (
    FriendRelation, HealthDomain, IndicatorDict, IndicatorDomainLink, Institution, InstitutionReport,
    ReportAsset, ReportIndicator, SelfMeasurement, User,
)
from app.services.permissions import ROLE_ADMIN, ROLE_INSTITUTION_ADMIN, ROLE_USER, roles_required
from app.services.dates import calendar_date_iso


BUSINESS_TZ = ZoneInfo("Asia/Shanghai")

REFERENCE_SOURCES = {
    "HR": {"context": "成人安静状态", "source_title": "American Heart Association 成人静息心率说明", "source_url": "https://www.heart.org/en/health-topics/high-blood-pressure/the-facts-about-high-blood-pressure/all-about-heart-rate-pulse"},
    "TEMP": {"context": "成人一般体温；测量部位、活动和时间会影响结果", "source_title": "MedlinePlus Body temperature norms", "source_url": "https://medlineplus.gov/ency/article/001982.htm"},
    "SPO2": {"context": "多数健康人；海拔、循环和设备条件会影响读数", "source_title": "FDA Pulse Oximeter Basics", "source_url": "https://www.fda.gov/consumers/consumer-updates/pulse-oximeters-and-oxygen-concentrators-what-know-about-home-oxygen-therapy"},
    "FBG": {"context": "成人空腹静脉血浆葡萄糖的一般参考", "source_title": "国家卫健委《成人糖尿病食养指南（2023年版）》", "source_url": "https://www.nhc.gov.cn/cms-search/downFiles/4fcbecd2c18e46baaf291bf46c2b79cd.pdf"},
    "BMI": {"context": "中国成人（18岁及以上）", "source_title": "国家卫健委《体重管理指导原则（2024年版）》", "source_url": "https://www.nhc.gov.cn/ylyjs/zcwj/202412/75cb79c171c94def9e768193e65484f7/files/1736390749000_59785.pdf"},
    "TC": {"context": "中国成人低危人群的一般筛查参考；个体目标值应结合心血管风险", "source_title": "《中国血脂管理指南（2023年）》", "source_url": "https://csc.cma.org.cn/attach/0/96b4e603cee74b7fb208b278aa92bd8b.pdf"},
    "TG": {"context": "中国成人低危人群的一般筛查参考；进食状态会影响结果", "source_title": "《中国血脂管理指南（2023年）》", "source_url": "https://csc.cma.org.cn/attach/0/96b4e603cee74b7fb208b278aa92bd8b.pdf"},
    "HDL": {"context": "中国成人低危人群的一般筛查参考；需结合其他血脂指标解释", "source_title": "《中国血脂管理指南（2023年）》", "source_url": "https://csc.cma.org.cn/attach/0/96b4e603cee74b7fb208b278aa92bd8b.pdf"},
    "LDL": {"context": "中国成人低危人群的一般筛查参考；高危人群目标值通常更严格", "source_title": "《中国血脂管理指南（2023年）》", "source_url": "https://csc.cma.org.cn/attach/0/96b4e603cee74b7fb208b278aa92bd8b.pdf"},
    "ALT": {"context": "成人血清检验的一般参考；检测方法与人群会影响区间", "source_title": "国家卫健委 WS/T 404.1—2012", "source_url": "https://www.nhc.gov.cn/wjw/s9492/201301/9b2e494990ce4825a48b4b476f6f928b.shtml"},
    "AST": {"context": "成人血清检验的一般参考；检测方法与人群会影响区间", "source_title": "国家卫健委 WS/T 404.1—2012", "source_url": "https://www.nhc.gov.cn/wjw/s9492/201301/9b2e494990ce4825a48b4b476f6f928b.shtml"},
    "UA": {"context": "成人血尿酸常见参考；性别和实验室方法会影响区间", "source_title": "MedlinePlus 血尿酸检验说明", "source_url": "https://medlineplus.gov/ency/article/003476.htm"},
    "CREA": {"context": "中国成人血清肌酐的一般参考；性别和检测系统会影响区间", "source_title": "国家卫健委 WS/T 404.5—2015 解读", "source_url": "https://www.nhc.gov.cn/zwgk/jdjd/201505/93ba76681b1b4b8a9d8b127159b3390c.shtml"},
}

ONE_SIDED_GUIDELINE_BOUNDS = {
    "TC": (None, 5.2),
    "TG": (None, 1.7),
    "HDL": (1.0, None),
    "LDL": (None, 3.4),
    "ALT": (None, 40.0),
    "AST": (None, 40.0),
}


def _measurement_day(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BUSINESS_TZ).date()


def _day_bounds(day):
    return (
        datetime.combine(day, time.min, tzinfo=BUSINESS_TZ).astimezone(timezone.utc),
        datetime.combine(day, time.max, tzinfo=BUSINESS_TZ).astimezone(timezone.utc),
    )


def _numeric_reference(value):
    text = str(value or "").strip()
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) == 1:
        bound = float(numbers[0])
        if re.search(r"(?:>=|>|≥|不低于|高于|大于)", text):
            return (bound, None)
        if re.search(r"(?:<=|<|≤|不超过|低于|小于)", text):
            return (None, bound)
        return None
    if len(numbers) < 2:
        return None
    low, high = float(numbers[0]), float(numbers[1])
    return (low, high) if low <= high else None


def _owner_age(owner):
    if not owner.birth_date:
        return None
    today = date.today()
    return today.year - owner.birth_date.year - (
        (today.month, today.day) < (owner.birth_date.month, owner.birth_date.day)
    )


def _guideline_bounds(owner, definition):
    if definition.code in ONE_SIDED_GUIDELINE_BOUNDS:
        return ONE_SIDED_GUIDELINE_BOUNDS[definition.code]
    if definition.code == "CREA" and owner.gender in {"male", "female"}:
        age = _owner_age(owner)
        if age is not None and 20 <= age <= 79:
            if owner.gender == "male":
                return (57.0, 97.0 if age <= 59 else 111.0)
            return (41.0, 73.0 if age <= 59 else 81.0)
    if definition.code == "UA" and owner.gender in {"male", "female"}:
        # MedlinePlus publishes sex-specific adult values in mg/dL. Convert
        # them to the catalog unit using 1 mg/dL = 59.48 μmol/L.
        return (238.0, 512.0) if owner.gender == "male" else (178.0, 422.0)
    return (
        float(definition.reference_low) if definition.reference_low is not None else None,
        float(definition.reference_high) if definition.reference_high is not None else None,
    )


def _track_reference(owner, definition, points):
    # Prefer the most recent usable range carried by an institution report.
    # Historical institutions can legitimately use different ranges, so the
    # latest range is drawn while every point keeps its original range in the
    # tooltip.  If no report range can be parsed, continue to the public
    # guideline fallback instead of returning an empty reference immediately.
    report_ranges = [
        _numeric_reference(point.get("reference"))
        for point in points
        if point.get("source", {}).get("type") == "institution" and point.get("reference")
    ]
    report_ranges = [item for item in report_ranges if item is not None]
    if report_ranges:
        low, high = report_ranges[-1]
        varies = len(set(report_ranges)) > 1
        return {
            "kind": "report", "low": low, "high": high,
            "label": "最近一次机构报告参考范围" if varies else "机构报告参考范围",
            "context": "历史报告范围不完全一致；图中采用最近一次，悬浮数据点可查看当次范围"
            if varies else "优先采用机构报告提供的参考范围",
            "varies": varies,
        }
    if definition.code == "WEIGHT":
        height = IndicatorDict.query.filter_by(code="HEIGHT").first()
        latest = None if height is None else SelfMeasurement.query.filter_by(
            user_id=owner.id, indicator_dict_id=height.id).order_by(SelfMeasurement.measured_at.desc()).first()
        age = _owner_age(owner)
        if latest and age is not None and age >= 18 and float(latest.value) > 0:
            metres = float(latest.value) / 100
            return {"kind": "derived", "low": round(18.5 * metres * metres, 1),
                    "high": round(23.9 * metres * metres, 1), "label": "按成人 BMI 换算的参考体重",
                    "context": f"根据最近身高 {float(latest.value):g} cm 换算，仅作健康管理参考", "varies": False,
                    **REFERENCE_SOURCES["BMI"]}
    meta = REFERENCE_SOURCES.get(definition.code)
    low, high = _guideline_bounds(owner, definition)
    if meta and (low is not None or high is not None):
        return {"kind": "guideline", "low": low, "high": high,
                "label": "一般参考范围", "varies": False, **meta}
    return {"kind": "none", "low": None, "high": None, "label": "暂无统一参考范围",
            "context": "该指标需结合个人情况或原始报告解释", "varies": False}


def _owner():
    raw = request.args.get("owner_id")
    if raw in {None, ""}: return g.current_user, None
    try: owner_id = int(raw)
    except (TypeError, ValueError): return None, ({"message": "成员标识不正确"}, 400)
    if owner_id == g.current_user.id: return g.current_user, None
    relation = FriendRelation.query.filter_by(user_id=g.current_user.id, friend_user_id=owner_id, auth_status=True).first()
    owner = db.session.get(User, owner_id) if relation else None
    if not owner or owner.role != "user": return None, ({"message": "尚未获得该亲友的健康资料授权"}, 403)
    return owner, None


def _date_range():
    try:
        start = date.fromisoformat(request.args["start_date"]) if request.args.get("start_date") else None
        end = date.fromisoformat(request.args["end_date"]) if request.args.get("end_date") else None
    except ValueError:
        return None, None, ({"message": "日期范围格式不正确"}, 400)
    if start and end and start > end: return None, None, ({"message": "开始日期不能晚于结束日期"}, 400)
    return start, end, None


def _pagination():
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    requested = request.args.get("page_size", 15, type=int) or 15
    return page, 30 if requested == 30 else 15


def report_key(report): return f"hd-i-{report.id:x}"
def self_key(owner_id, day): return f"hd-s-{owner_id:x}-{day.isoformat()}"


def parse_key(value):
    try:
        if value.startswith("hd-i-"): return "institution", int(value[5:], 16)
        if value.startswith("hd-s-"):
            owner_hex, day = value[5:].split("-", 1)
            return "self", (int(owner_hex, 16), date.fromisoformat(day))
    except (TypeError, ValueError):
        pass
    return None, None


@health_data_v7_bp.get("/health-domains")
@roles_required(ROLE_USER, ROLE_INSTITUTION_ADMIN, ROLE_ADMIN)
def domains():
    rows = HealthDomain.query.filter_by(is_active=True).order_by(HealthDomain.sort_order, HealthDomain.id).all()
    return {"items": [row.to_dict() for row in rows]}, 200


@health_data_v7_bp.get("/health-data")
@roles_required(ROLE_USER)
def health_data_list():
    owner, error = _owner()
    if error: return error
    start, end, error = _date_range()
    if error: return error
    institution_id = request.args.get("institution_id", type=int)
    domain_id = request.args.get("domain_id", type=int)
    records = []
    query = InstitutionReport.query.filter_by(matched_user_id=owner.id, status="published")
    if start: query = query.filter(InstitutionReport.exam_date >= start)
    if end: query = query.filter(InstitutionReport.exam_date <= end)
    if institution_id: query = query.filter(InstitutionReport.institution_id == institution_id)
    if domain_id:
        query = query.filter(db.or_(
            InstitutionReport.indicators.any(ReportIndicator.display_domain_id == domain_id),
            InstitutionReport.text_results.any(health_domain_id=domain_id),
            InstitutionReport.assets.any(health_domain_id=domain_id),
        ))
    for report in query.all():
        domain_ids = {row.display_domain_id for row in report.indicators if row.display_domain_id}
        domain_ids.update(row.health_domain_id for row in report.text_results)
        domain_ids.update(row.health_domain_id for row in report.assets)
        domain_rows = HealthDomain.query.filter(HealthDomain.id.in_(domain_ids)).order_by(HealthDomain.sort_order).all() if domain_ids else []
        records.append({"health_data_id": report_key(report), "source_type": "institution",
                        "business_date": calendar_date_iso(report.exam_date),
                        "source": {"id": report.institution_id, "name": report.institution.name,
                                   "branch_name": report.institution.branch_name} if report.institution else None,
                        "package": {"id": report.package_id, "name": report.package.name} if report.package else None,
                        "domains": [row.to_dict() for row in domain_rows], "indicator_count": len(report.indicators),
                        "text_result_count": len(report.text_results), "asset_count": len(report.assets)})
    records.sort(key=lambda row: (row["business_date"], row["health_data_id"]), reverse=True)
    page, size = _pagination(); total = len(records)
    return {"owner": owner.friend_identity_dict(), "items": records[(page - 1) * size:page * size],
            "pagination": {"page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size}}, 200


def _detail_for(owner, health_data_id):
    kind, value = parse_key(health_data_id)
    sections = defaultdict(lambda: {"indicators": [], "text_results": [], "assets": []})
    if kind == "institution":
        report = InstitutionReport.query.filter_by(id=value, matched_user_id=owner.id, status="published").first()
        if not report: return None
        for row in report.indicators:
            if row.display_domain_id: sections[row.display_domain_id]["indicators"].append(row.to_dict())
        for row in report.text_results: sections[row.health_domain_id]["text_results"].append(row.to_dict())
        for row in report.assets: sections[row.health_domain_id]["assets"].append(row.to_dict(health_data_id))
        source = {"id": report.institution_id, "name": report.institution.name,
                  "branch_name": report.institution.branch_name} if report.institution else None
        package = {"id": report.package_id, "name": report.package.name} if report.package else None
        business_date = calendar_date_iso(report.exam_date)
    elif kind == "self" and value[0] == owner.id:
        day = value[1]
        start_at, end_at = _day_bounds(day)
        rows = SelfMeasurement.query.filter_by(user_id=owner.id).filter(
            SelfMeasurement.measured_at >= start_at,
            SelfMeasurement.measured_at <= end_at,
        ).order_by(SelfMeasurement.measured_at, SelfMeasurement.id).all()
        if not rows: return None
        for row in rows:
            link = next((item for item in row.indicator_dict.domain_links if item.is_primary), None)
            if link: sections[link.health_domain_id]["indicators"].append(row.to_dict())
        source = {"id": None, "name": "个人自测", "branch_name": None}; package = None; business_date = day.isoformat()
    else:
        return None
    rendered = []
    for domain_id, values in sections.items():
        domain = db.session.get(HealthDomain, domain_id)
        rendered.append({"domain": domain.to_dict(), **values})
    rendered.sort(key=lambda row: (row["domain"]["sort_order"], row["domain"]["id"]))
    return {"health_data_id": health_data_id, "source_type": kind, "business_date": business_date,
            "source": source, "package": package, "sections": rendered}


@health_data_v7_bp.get("/health-data/<health_data_id>")
@roles_required(ROLE_USER)
def health_data_detail(health_data_id):
    owner, error = _owner()
    if error: return error
    item = _detail_for(owner, health_data_id)
    return ({"item": item, "owner": owner.friend_identity_dict()}, 200) if item else ({"message": "没有找到该体检数据"}, 404)


@health_data_v7_bp.get("/health-data/<health_data_id>/assets/<int:asset_id>/content")
@roles_required(ROLE_USER)
def asset_content(health_data_id, asset_id):
    owner, error = _owner()
    if error: return error
    kind, report_id = parse_key(health_data_id)
    if kind != "institution": return {"message": "没有找到该检查附件"}, 404
    asset = db.session.query(ReportAsset).join(InstitutionReport).filter(
        ReportAsset.id == asset_id, ReportAsset.report_id == report_id,
        InstitutionReport.matched_user_id == owner.id, InstitutionReport.status == "published").first()
    if not asset: return {"message": "没有找到该检查附件"}, 404
    path = Path(current_app.config["UPLOAD_DIR"]) / asset.storage_key
    if not path.is_file(): return {"message": "检查附件文件暂时不可用"}, 404
    return send_file(path, mimetype=asset.mime_type, download_name=asset.title, conditional=True)


@health_data_v7_bp.get("/health-trends/<int:domain_id>")
@roles_required(ROLE_USER)
def health_trends(domain_id):
    owner, error = _owner()
    if error: return error
    domain = db.session.get(HealthDomain, domain_id)
    if not domain or not domain.is_active: return {"message": "没有找到该健康方向"}, 404
    start, end, error = _date_range()
    if error: return error
    source_type = (request.args.get("source_type") or "all").strip()
    if source_type not in {"all", "self", "institution"}:
        return {"message": "趋势来源筛选不正确"}, 400
    institution_id = request.args.get("institution_id", type=int)
    items = []
    available_institutions = {}
    # Only select comparable scalar columns. Selecting the complete report with
    # DISTINCT also selects its JSON diagnostics column, which openGauss cannot compare.
    available_query = db.session.query(Institution.id, Institution.name, Institution.branch_name).join(
        InstitutionReport, InstitutionReport.institution_id == Institution.id
    ).join(ReportIndicator, ReportIndicator.report_id == InstitutionReport.id).filter(
        InstitutionReport.matched_user_id == owner.id,
        InstitutionReport.status == "published",
        ReportIndicator.display_domain_id == domain.id,
    )
    if start: available_query = available_query.filter(InstitutionReport.exam_date >= start)
    if end: available_query = available_query.filter(InstitutionReport.exam_date <= end)
    for available_institution_id, institution_name, branch_name in available_query.distinct().all():
        available_institutions[available_institution_id] = {
            "type": "institution", "id": available_institution_id,
            "name": institution_name, "branch_name": branch_name,
        }
    links = IndicatorDomainLink.query.filter_by(health_domain_id=domain.id).order_by(
        IndicatorDomainLink.sort_order, IndicatorDomainLink.indicator_dict_id).all()
    for link in links:
        definition = link.indicator
        daily_self = {}
        daily_reports = defaultdict(list)
        reports = db.session.query(ReportIndicator, InstitutionReport).join(InstitutionReport).filter(
            InstitutionReport.matched_user_id == owner.id, InstitutionReport.status == "published",
            ReportIndicator.indicator_dict_id == definition.id, ReportIndicator.display_domain_id == domain.id)
        if start: reports = reports.filter(InstitutionReport.exam_date >= start)
        if end: reports = reports.filter(InstitutionReport.exam_date <= end)
        if institution_id: reports = reports.filter(InstitutionReport.institution_id == institution_id)
        for result, report in reports.order_by(InstitutionReport.exam_date, InstitutionReport.published_at, InstitutionReport.id).all():
            try: numeric = float(result.value)
            except (TypeError, ValueError): continue
            source = {"type": "institution", "id": report.institution_id,
                      "name": report.institution.name, "branch_name": report.institution.branch_name}
            available_institutions[report.institution_id] = source
            day = calendar_date_iso(report.exam_date)
            daily_reports[day].append({"date": day, "value": numeric,
                "unit": result.normalized_unit or definition.unit, "reference": result.reference_text,
                "is_abnormal": result.is_abnormal, "health_data_id": report_key(report), "source": source,
                "published_at": report.published_at.isoformat() if report.published_at else None})
        if not institution_id and source_type in {"all", "self"}:
            measurements = SelfMeasurement.query.filter_by(user_id=owner.id, indicator_dict_id=definition.id)
            if start: measurements = measurements.filter(SelfMeasurement.measured_at >= _day_bounds(start)[0])
            if end: measurements = measurements.filter(SelfMeasurement.measured_at <= _day_bounds(end)[1])
            for row in measurements.order_by(SelfMeasurement.measured_at, SelfMeasurement.id).all():
                day = _measurement_day(row.measured_at)
                daily_self[day.isoformat()] = {"date": day.isoformat(), "value": float(row.value),
                    "unit": definition.unit, "measured_at": row.measured_at.isoformat(),
                    "health_data_id": self_key(owner.id, day),
                    "source": {"type": "self", "id": None, "name": "个人日常测量"}}
        points = []
        all_days = sorted(set(daily_self) | set(daily_reports))
        for day in all_days:
            report_points = daily_reports.get(day, [])
            if source_type == "self":
                chosen = daily_self.get(day)
            elif source_type == "institution" or institution_id:
                chosen = report_points[-1] if report_points else None
            else:
                chosen = report_points[-1] if report_points else daily_self.get(day)
            if chosen:
                chosen = dict(chosen)
                chosen["same_day_other_count"] = max(len(report_points) - 1, 0)
                points.append(chosen)
        if points:
            previous = points[-2]["value"] if len(points) > 1 else None
            items.append({"indicator": definition.to_dict(), "points": points,
                          "reference": _track_reference(owner, definition, points),
                          "summary": {"latest": points[-1]["value"],
                                      "change": points[-1]["value"] - previous if previous is not None else None,
                                      "count": len(points)}})
    source_options = [
        {"value": "all", "label": "全部来源"},
        {"value": "self", "label": "个人日常测量"},
        {"value": "institution", "label": "全部机构体检"},
        *[{"value": f"institution:{item['id']}", "label": f"{item['name']} · {item['branch_name']}"}
          for item in sorted(available_institutions.values(), key=lambda row: (row["name"], row["branch_name"]))],
    ]
    return {"owner": owner.friend_identity_dict(), "domain": domain.to_dict(),
            "series_by_indicator": items, "source_options": source_options,
            "abnormal_count": sum(1 for item in items for point in item["points"] if point.get("is_abnormal"))}, 200
