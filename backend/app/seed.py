import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import current_app

from app.extensions import db
from app.models import (
    Appointment, AppointmentEvent, BookingGroup, FriendRelation, HealthDomain,
    IndicatorCategory, IndicatorDict, IndicatorDomainLink, IndicatorReferenceRule, Institution,
    InstitutionReport, Package, PackageChangeRequest, PackageVersion,
    PackageVersionDomain, ReportAssetType, ReportIndicator, SelfMeasurement, User,
)
from app.catalog_v10 import ASSET_TYPE_SEEDS, V10_INDICATOR_CATEGORIES, V10_INDICATOR_SEEDS
from app.services.password_challenges import (
    increment_user_security_epochs,
    revoke_account_security_artifacts,
)
import uuid


INSTITUTION_SEEDS = [
    {
        "name": "美年大健康",
        "branch_name": "小木桥分院(美年门诊部)",
        "district": "徐汇区",
        "address": "小木桥路251号天亿大厦1-3楼",
        "metro_info": "4号线大木桥路站3号口，12号线嘉善路站4号口",
        "consult_phone": "64031188",
        "closed_day": "周一休",
        "description": "综合体检服务中心",
    },
    {
        "name": "美年大健康",
        "branch_name": "苏河分院(美恒门诊部)",
        "district": "静安区",
        "address": "恒丰路638号苏河一号3楼",
        "metro_info": "1/3/4号线上海火车站站3号口",
        "consult_phone": "63810221",
        "closed_day": "周四休",
        "description": "靠近交通枢纽，支持快速预约",
    },
    {
        "name": "美年大健康",
        "branch_name": "五角场分院(美阳门诊部)",
        "district": "杨浦区",
        "address": "淞沪路388号创智天地7号楼5楼",
        "metro_info": "10号线江湾体育场站11号口",
        "consult_phone": "35360351",
        "closed_day": "周五休",
        "description": "常规筛查与专项体检并行",
    },
]


PACKAGE_TEMPLATE = [
    {
        "name": "A套餐-综合套餐",
        "focus_area": "全身基础筛查",
        "gender_scope": "all",
        "price": Decimal("699.00"),
        "description": "覆盖常规体格检查、血常规、尿常规、肝肾功能与基础影像。",
    },
    {
        "name": "B套餐-心脑血管",
        "focus_area": "心脑血管风险",
        "gender_scope": "all",
        "price": Decimal("999.00"),
        "description": "强化血脂、炎症指标、超声与心电检查。",
    },
    {
        "name": "C套餐-内分泌",
        "focus_area": "糖脂代谢与甲状腺",
        "gender_scope": "all",
        "price": Decimal("1199.00"),
        "description": "覆盖血糖、胰岛素、糖化血红蛋白及甲状腺功能。",
    },
    {
        "name": "D套餐-消化及呼吸",
        "focus_area": "消化系统与肺功能",
        "gender_scope": "all",
        "price": Decimal("1099.00"),
        "description": "包含幽门螺旋杆菌、消化功能及肺功能筛查。",
    },
    {
        "name": "E套餐-女性专项",
        "focus_area": "女性健康",
        "gender_scope": "female",
        "price": Decimal("1299.00"),
        "description": "增加妇科专项、乳腺与甲状腺针对性评估。",
    },
]


INDICATOR_CATEGORY_SEEDS = [
    {"name": "一般检查", "sort_order": 1},
    {"name": "血糖", "sort_order": 2},
    {"name": "血脂", "sort_order": 3},
    {"name": "肝功能", "sort_order": 4},
    {"name": "肾功能", "sort_order": 5},
]


INDICATOR_DICT_SEEDS = [
    {
        "category": "一般检查", "code": "HEIGHT", "name": "身高",
        "aliases": ["身高", "HEIGHT"], "unit": "cm",
        "reference_low": None, "reference_high": None,
        "clinical_significance": "用于观察生长发育与体型变化。", "value_type": "numeric",
        "allow_self_measurement": True,
    },
    {
        "category": "一般检查", "code": "WEIGHT", "name": "体重",
        "aliases": ["体重", "WEIGHT"], "unit": "kg",
        "reference_low": None, "reference_high": None,
        "clinical_significance": "体重变化可辅助评估营养和代谢状态。", "value_type": "numeric",
        "allow_self_measurement": True,
    },
    {
        "category": "一般检查", "code": "HR", "name": "心率",
        "aliases": ["心率", "脉搏", "HR"], "unit": "次/分",
        "reference_low": Decimal("60"), "reference_high": Decimal("100"),
        "clinical_significance": "静息心率异常时建议结合症状咨询专业人员。", "value_type": "numeric",
        "allow_self_measurement": True,
    },
    {
        "category": "一般检查", "code": "TEMP", "name": "体温",
        "aliases": ["体温", "TEMP"], "unit": "℃",
        "reference_low": Decimal("36.1"), "reference_high": Decimal("37.2"),
        "clinical_significance": "体温异常可提示感染或其他生理变化。", "value_type": "numeric",
        "allow_self_measurement": True,
    },
    {
        "category": "一般检查", "code": "SPO2", "name": "血氧",
        "aliases": ["血氧", "血氧饱和度", "SpO2"], "unit": "%",
        "reference_low": Decimal("95"), "reference_high": Decimal("100"),
        "clinical_significance": "持续偏低时应及时寻求专业医疗帮助。", "value_type": "numeric",
        "allow_self_measurement": True,
    },
    {
        "category": "一般检查",
        "code": "BMI",
        "name": "体重指数",
        "aliases": ["BMI", "体重指数"],
        "unit": "kg/m²",
        "reference_low": Decimal("18.50"),
        "reference_high": Decimal("23.90"),
        "clinical_significance": "过高提示超重或肥胖风险。",
        "value_type": "numeric",
    },
    {
        "category": "血糖",
        "code": "FBG",
        "name": "空腹血糖",
        "aliases": ["空腹血糖", "FBG", "GLU"],
        "unit": "mmol/L",
        "reference_low": Decimal("3.90"),
        "reference_high": Decimal("6.10"),
        "clinical_significance": "升高提示糖代谢异常风险。",
        "value_type": "numeric",
        "allow_self_measurement": True,
    },
    {
        "category": "血脂",
        "code": "TC",
        "name": "总胆固醇",
        "aliases": ["总胆固醇", "TC"],
        "unit": "mmol/L",
        "reference_low": Decimal("0.00"),
        "reference_high": Decimal("5.20"),
        "clinical_significance": "升高提示动脉粥样硬化风险上升。",
        "value_type": "numeric",
    },
    {
        "category": "血脂",
        "code": "TG",
        "name": "甘油三酯",
        "aliases": ["甘油三酯", "TG"],
        "unit": "mmol/L",
        "reference_low": Decimal("0.00"),
        "reference_high": Decimal("1.70"),
        "clinical_significance": "升高与代谢综合征风险相关。",
        "value_type": "numeric",
    },
    {
        "category": "血脂",
        "code": "HDL",
        "name": "高密度脂蛋白",
        "aliases": ["高密度脂蛋白", "HDL"],
        "unit": "mmol/L",
        "reference_low": Decimal("1.00"),
        "reference_high": Decimal("2.30"),
        "clinical_significance": "偏低提示心血管保护作用下降。",
        "value_type": "numeric",
    },
    {
        "category": "血脂",
        "code": "LDL",
        "name": "低密度脂蛋白",
        "aliases": ["低密度脂蛋白", "LDL"],
        "unit": "mmol/L",
        "reference_low": Decimal("0.00"),
        "reference_high": Decimal("3.40"),
        "clinical_significance": "偏高提示心脑血管风险增加。",
        "value_type": "numeric",
    },
    {
        "category": "肝功能",
        "code": "ALT",
        "name": "丙氨酸氨基转移酶",
        "aliases": ["ALT", "谷丙转氨酶", "丙氨酸氨基转移酶"],
        "unit": "U/L",
        "reference_low": Decimal("0.00"),
        "reference_high": Decimal("40.00"),
        "clinical_significance": "升高提示肝细胞损伤可能。",
        "value_type": "numeric",
    },
    {
        "category": "肝功能",
        "code": "AST",
        "name": "天门冬氨酸氨基转移酶",
        "aliases": ["AST", "谷草转氨酶", "天门冬氨酸氨基转移酶"],
        "unit": "U/L",
        "reference_low": Decimal("0.00"),
        "reference_high": Decimal("40.00"),
        "clinical_significance": "升高需结合ALT等指标评估。",
        "value_type": "numeric",
    },
    {
        "category": "肾功能",
        "code": "UA",
        "name": "尿酸",
        "aliases": ["尿酸", "UA"],
        "unit": "μmol/L",
        "reference_low": Decimal("155.00"),
        "reference_high": Decimal("428.00"),
        "clinical_significance": "升高提示高尿酸血症风险。",
        "value_type": "numeric",
    },
    {
        "category": "肾功能",
        "code": "CREA",
        "name": "肌酐",
        "aliases": ["肌酐", "CREA"],
        "unit": "μmol/L",
        "reference_low": Decimal("44.00"),
        "reference_high": Decimal("133.00"),
        "clinical_significance": "异常提示肾功能变化风险。",
        "value_type": "numeric",
    },
]


def seed_institutions_and_packages():
    if Institution.query.first() is not None:
        return

    for institution_payload in INSTITUTION_SEEDS:
        institution = Institution(**institution_payload)
        db.session.add(institution)
        db.session.flush()

        for package_payload in PACKAGE_TEMPLATE:
            package = Package(institution_id=institution.id, **package_payload)
            db.session.add(package)

    db.session.commit()


def seed_indicator_dicts(*, commit: bool = True):
    category_map = {row.name: row for row in IndicatorCategory.query.all()}
    for category_payload in [*INDICATOR_CATEGORY_SEEDS, *V10_INDICATOR_CATEGORIES]:
        category = category_map.get(category_payload["name"])
        if category is None:
            category = IndicatorCategory(
                name=category_payload["name"],
                sort_order=category_payload["sort_order"],
            )
            db.session.add(category)
            db.session.flush()
            category_map[category.name] = category

    existing_codes = {row.code for row in IndicatorDict.query.all()}
    for item in [*INDICATOR_DICT_SEEDS, *V10_INDICATOR_SEEDS]:
        if item["code"] in existing_codes:
            continue
        category = category_map[item["category"]]
        indicator = IndicatorDict(
            category_id=category.id,
            code=item["code"],
            name=item["name"],
            aliases=item["aliases"],
            unit=item["unit"],
            reference_low=item["reference_low"],
            reference_high=item["reference_high"],
            clinical_significance=item["clinical_significance"],
            value_type=item["value_type"],
            allow_self_measurement=item.get("allow_self_measurement", False),
        )
        db.session.add(indicator)
        existing_codes.add(item["code"])

    db.session.commit() if commit else db.session.flush()


def seed_core_data():
    seed_admin_user()
    seed_indicator_dicts()
    # The v7 snapshot is deliberately built as a coherent platform story
    # instead of backfilling the old A-E package/status-matrix fixtures.
    seed_health_domains_and_versions()
    seed_v10_reference_rules()
    seed_v10_asset_types()
    from app.demo_v7 import (
        ensure_v7_demo_accounts,
        ensure_v7_demo_catalog,
        seed_v7_demo_experience,
    )

    ensure_v7_demo_catalog()
    ensure_v7_demo_accounts()
    seed_v7_demo_experience()


HEALTH_DOMAIN_SEEDS = (
    ("basic", "基础体征与体格", 1),
    ("cardio", "心脑血管与循环", 2),
    ("metabolic", "内分泌与代谢", 3),
    ("digestive", "消化与肝胆胰", 4),
    ("respiratory", "呼吸系统", 5),
    ("renal", "肾脏与泌尿", 6),
    ("hematology", "血液与免疫", 7),
    ("other", "其他", 8),
)

INDICATOR_DOMAIN_CODES = {
    "HEIGHT": ("basic",), "WEIGHT": ("basic", "metabolic"), "BMI": ("basic", "metabolic"),
    "HR": ("cardio",), "TEMP": ("basic",), "SPO2": ("respiratory",),
    "FBG": ("metabolic",), "TC": ("metabolic", "cardio"),
    "TG": ("metabolic", "cardio"), "HDL": ("metabolic", "cardio"),
    "LDL": ("metabolic", "cardio"), "ALT": ("digestive",),
    "AST": ("digestive",), "UA": ("renal", "metabolic"), "CREA": ("renal",),
}
INDICATOR_DOMAIN_CODES.update({
    item["code"]: tuple(item["domains"])
    for item in V10_INDICATOR_SEEDS
})


def seed_v10_asset_types(*, commit: bool = True):
    domains = {row.code: row for row in HealthDomain.query.all()}
    existing = {row.code: row for row in ReportAssetType.query.all()}
    for order, (code, domain_code, name, modality, max_files) in enumerate(ASSET_TYPE_SEEDS, start=1):
        if domain_code not in domains:
            continue
        row = existing.get(code)
        if row is None:
            row = ReportAssetType(code=code)
            db.session.add(row)
        row.health_domain_id = domains[domain_code].id
        row.name = name
        row.modality = modality
        row.max_files = max_files
        row.sort_order = order
    db.session.commit() if commit else db.session.flush()


def seed_v10_reference_rules(*, commit: bool = True):
    if IndicatorReferenceRule.query.first() is not None:
        return
    definitions = {row.code: row for row in IndicatorDict.query.all()}
    source_title = "国家卫生健康委临床常用检验项目参考区间"
    source_url = "https://www.nhc.gov.cn/zwgkzt/pyzgl1/201303/906048b809e04849b0f14daeea92ff2c.shtml"
    rules = [
        ("RBC", "male", 4.3, 5.8, "4.3–5.8 ×10^12/L"),
        ("RBC", "female", 3.8, 5.1, "3.8–5.1 ×10^12/L"),
        ("HGB", "male", 130, 175, "130–175 g/L"),
        ("HGB", "female", 115, 150, "115–150 g/L"),
        ("HCT", "male", 40, 50, "40–50%"),
        ("HCT", "female", 35, 45, "35–45%"),
        ("CREA", "male", 57, 111, "57–111 μmol/L"),
        ("CREA", "female", 41, 81, "41–81 μmol/L"),
        ("UA", "male", 208, 428, "208–428 μmol/L"),
        ("UA", "female", 155, 357, "155–357 μmol/L"),
    ]
    for code, gender, low, high, text in rules:
        definition = definitions.get(code)
        if definition is None:
            continue
        db.session.add(IndicatorReferenceRule(
            indicator_dict_id=definition.id,
            gender_scope=gender,
            min_age=18,
            reference_low=Decimal(str(low)),
            reference_high=Decimal(str(high)),
            reference_text=text,
            source_title=source_title,
            source_url=source_url,
            priority=100,
        ))
    db.session.commit() if commit else db.session.flush()


def _package_domain_codes(package):
    text = f"{package.name} {package.focus_area}"
    if "心脑血管" in text:
        return ("cardio", "metabolic")
    if "内分泌" in text or "甲状腺" in text:
        return ("metabolic",)
    if "消化" in text or "呼吸" in text:
        return ("digestive", "respiratory")
    if "女性" in text:
        return ("metabolic", "other")
    return ("basic", "cardio", "metabolic", "digestive", "respiratory", "renal", "hematology")


def seed_health_domains_and_versions(*, commit: bool = True):
    domains = {row.code: row for row in HealthDomain.query.all()}
    for code, name, order in HEALTH_DOMAIN_SEEDS:
        if code not in domains:
            row = HealthDomain(code=code, name=name, sort_order=order)
            db.session.add(row); db.session.flush(); domains[code] = row

    for indicator in IndicatorDict.query.all():
        if IndicatorDomainLink.query.filter_by(indicator_dict_id=indicator.id).first():
            continue
        codes = INDICATOR_DOMAIN_CODES.get(indicator.code, ("other",))
        for order, code in enumerate(codes):
            db.session.add(IndicatorDomainLink(
                indicator_dict_id=indicator.id, health_domain_id=domains[code].id,
                is_primary=order == 0, sort_order=order,
            ))

    for package in Package.query.all():
        version = PackageVersion.query.filter_by(package_id=package.id).order_by(PackageVersion.version_number.desc()).first()
        if version is None:
            codes = _package_domain_codes(package)
            package.package_type = "special" if len(codes) == 1 else "combined"
            version = PackageVersion(
                package_id=package.id, version_number=1, package_type=package.package_type,
                name_snapshot=package.name, price_snapshot=package.price,
                audience_snapshot=package.audience or package.gender_scope,
                description_snapshot=package.description,
                booking_notice_snapshot=package.booking_notice or "具体检查结果以机构实际完成项目为准。",
            )
            db.session.add(version); db.session.flush()
            for order, code in enumerate(codes):
                db.session.add(PackageVersionDomain(package_version_id=version.id, health_domain_id=domains[code].id, sort_order=order))
        package.current_version_id = version.id

    db.session.flush()
    for report in InstitutionReport.query.all():
        if report.package_version_id is None and report.package is not None:
            report.package_version_id = report.package.current_version_id
        allowed = {row.health_domain_id for row in PackageVersionDomain.query.filter_by(package_version_id=report.package_version_id).all()}
        for indicator in report.indicators:
            if indicator.display_domain_id is None:
                links = IndicatorDomainLink.query.filter_by(indicator_dict_id=indicator.indicator_dict_id).order_by(IndicatorDomainLink.is_primary.desc(), IndicatorDomainLink.sort_order).all()
                match = next((link for link in links if link.health_domain_id in allowed), links[0] if links else None)
                if match:
                    indicator.display_domain_id = match.health_domain_id
                    indicator.original_name = indicator.original_name or indicator.indicator_dict.name
                    indicator.original_value = indicator.original_value or indicator.value
                    indicator.original_unit = indicator.original_unit or indicator.indicator_dict.unit
                    indicator.normalized_unit = indicator.normalized_unit or indicator.indicator_dict.unit
                    indicator.mapping_status = "legacy_backfill"
    db.session.commit() if commit else db.session.flush()


def seed_v7_workflow_snapshots():
    """Backfill v6 appointments as one-person groups and immutable event histories."""
    for user in User.query.filter(User.email.isnot(None), User.email_verified_at.is_(None)).all():
        if user.username.startswith("test") or user.username.startswith("institution") or user.username == "demo_admin":
            user.email_verified_at = user.created_at
    for appointment in Appointment.query.order_by(Appointment.id).all():
        package = appointment.package
        version = (
            db.session.get(PackageVersion, package.current_version_id)
            if package and package.current_version_id else None
        )
        if appointment.package_version_id is None and version:
            appointment.package_version_id = version.id
        if appointment.booked_by_user_id is None:
            appointment.booked_by_user_id = appointment.user_id
        if appointment.booking_group_id is None:
            domains = [row.domain.to_dict() for row in version.domains if row.domain] if version else []
            group = BookingGroup(
                group_code=f"BG-LEGACY-{appointment.id:08d}", booked_by_user_id=appointment.user_id,
                institution_id=appointment.institution_id, package_id=appointment.package_id,
                package_version_id=version.id if version else None,
                appointment_date=appointment.appointment_date, party_size=1,
                package_name_snapshot=appointment.package_name_snapshot,
                package_price_snapshot=appointment.package_price_snapshot,
                domain_snapshot=domains,
                booking_notice_snapshot=version.booking_notice_snapshot if version else None,
                notice_version_snapshot=version.version_number if version else None,
                notice_confirmed_at=appointment.created_at,
                created_at=appointment.created_at,
            )
            db.session.add(group); db.session.flush(); appointment.booking_group_id = group.id
        if not appointment.events:
            db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="booked",
                status_snapshot="unfulfilled", message="预约成功", occurred_at=appointment.created_at))
            if appointment.attended_at:
                db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="attended",
                    status_snapshot="awaiting_report", message="机构确认到检", occurred_at=appointment.attended_at))
            if appointment.fulfilled_at:
                db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="archived",
                    status_snapshot="fulfilled", message="健康数据已归档", occurred_at=appointment.fulfilled_at))
            if appointment.cancelled_at:
                db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="cancelled",
                    status_snapshot="cancelled", message="预约已取消", occurred_at=appointment.cancelled_at))
            if appointment.invalidated_at:
                db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="invalidated",
                    status_snapshot="invalidated", message="预约已失效", occurred_at=appointment.invalidated_at))
    for report in InstitutionReport.query.all():
        if report.package_version_id is None and report.appointment:
            report.package_version_id = report.appointment.package_version_id
    db.session.commit()


def seed_demo_workflows():
    """Seed synthetic appointment and package-review states for every demo role."""
    people = User.query.filter(User.username.in_([f"test{i}" for i in range(1, 6)])).order_by(User.username).all()
    institutions = Institution.query.order_by(Institution.id).limit(3).all()
    if len(people) != 5 or len(institutions) != 3:
        return
    staff = [
        User.query.filter_by(role="institution_admin", managed_institution_id=item.id).order_by(User.id).first()
        for item in institutions
    ]
    reviewer = User.query.filter_by(username="demo_admin", role="admin").first()
    now = datetime.now(timezone.utc)
    today = date.today()

    if Appointment.query.first() is None:
        institutions[0].daily_appointment_limit = 3
        institutions[1].daily_appointment_limit = 5
        institutions[2].daily_appointment_limit = None
        for index, person in enumerate(people, start=1):
            institution = institutions[(index - 1) % len(institutions)]
            package = next(item for item in institution.packages if item.is_active)
            common = {
                "user_id": person.id,
                "institution_id": institution.id,
                "package_id": package.id,
                "user_name_snapshot": person.real_name,
                "user_health_id_snapshot": person.health_id,
                "package_name_snapshot": package.name,
                "package_price_snapshot": package.price,
            }
            future_day = today + timedelta(days=index)
            db.session.add(Appointment(**common, appointment_date=future_day, active_date_key=future_day, status="unfulfilled", created_at=now - timedelta(days=2)))
            awaiting_day = today + timedelta(days=10 + index)
            db.session.add(Appointment(**common, appointment_date=awaiting_day, active_date_key=awaiting_day, status="awaiting_report", attended_at=now - timedelta(hours=index), created_at=now - timedelta(days=3)))
            invalid_day = today + timedelta(days=20 + index)
            db.session.add(Appointment(**common, appointment_date=invalid_day, active_date_key=None, status="invalidated", invalidated_at=now - timedelta(hours=index), created_at=now - timedelta(days=4)))
            cancelled_day = today + timedelta(days=25 + index)
            db.session.add(Appointment(**common, appointment_date=cancelled_day, active_date_key=None, status="cancelled", cancelled_at=now - timedelta(hours=index), created_at=now - timedelta(days=5)))

            report = InstitutionReport.query.filter_by(matched_user_id=person.id, status="published").filter(InstitutionReport.package_id.isnot(None)).order_by(InstitutionReport.exam_date.asc()).first()
            if report is not None:
                report_package = report.package
                fulfilled = Appointment(
                    user_id=person.id,
                    institution_id=report.institution_id,
                    package_id=report.package_id,
                    appointment_date=report.exam_date,
                    active_date_key=report.exam_date,
                    status="fulfilled",
                    user_name_snapshot=person.real_name,
                    user_health_id_snapshot=person.health_id,
                    package_name_snapshot=report_package.name,
                    package_price_snapshot=report_package.price,
                    attended_at=report.locked_at,
                    fulfilled_at=report.published_at,
                    created_at=report.created_at,
                )
                db.session.add(fulfilled)
                db.session.flush()
                report.appointment_id = fulfilled.id
        db.session.commit()

    if PackageChangeRequest.query.first() is None and reviewer is not None:
        first_package = institutions[0].packages[0]
        second_package = institutions[1].packages[0]
        third_package = institutions[2].packages[0]
        db.session.add_all([
            PackageChangeRequest(
                institution_id=institutions[0].id,
                action="create",
                status="pending",
                proposed_data={"name": "职场轻体检套餐", "focus_area": "预约流程", "gender_scope": "all", "price": 288.0, "description": "基础体征与常规生化检查", "is_active": True},
                requested_by_user_id=staff[0].id,
                requested_at=now - timedelta(hours=2),
            ),
            PackageChangeRequest(
                institution_id=institutions[1].id,
                package_id=second_package.id,
                action="update",
                status="approved",
                before_data=second_package.to_dict(),
                proposed_data=second_package.to_dict(),
                requested_by_user_id=staff[1].id,
                reviewed_by_user_id=reviewer.id,
                requested_at=now - timedelta(days=3),
                reviewed_at=now - timedelta(days=2),
                review_note="资料完整，审核通过",
            ),
            PackageChangeRequest(
                institution_id=institutions[2].id,
                package_id=third_package.id,
                action="deactivate",
                status="rejected",
                before_data=third_package.to_dict(),
                proposed_data={**third_package.to_dict(), "is_active": False},
                requested_by_user_id=staff[2].id,
                reviewed_by_user_id=reviewer.id,
                requested_at=now - timedelta(days=2),
                reviewed_at=now - timedelta(days=1),
                review_note="套餐说明不完整，请补充后重新提交",
            ),
            PackageChangeRequest(
                institution_id=institutions[0].id,
                package_id=first_package.id,
                action="update",
                status="withdrawn",
                before_data=first_package.to_dict(),
                proposed_data=first_package.to_dict(),
                requested_by_user_id=staff[0].id,
                requested_at=now - timedelta(days=1),
                withdrawn_at=now - timedelta(hours=12),
            ),
        ])
        db.session.commit()


def _seed_self_measurements(user, index, indicators, now):
    """Create a dense, deterministic timeline for one synthetic user."""
    base_weight = Decimal("58.0") + Decimal(index * 4)
    height = Decimal("158.0") + Decimal(index * 3)
    db.session.add(SelfMeasurement(
        user_id=user.id,
        indicator_dict_id=indicators["HEIGHT"].id,
        value=height,
        measured_at=now - timedelta(days=120 - index),
    ))

    measurement_series = {
        "WEIGHT": [base_weight + Decimal(step) / Decimal("10") for step in (5, 4, 3, 5, 2, 1, 0, -1)],
        "HR": [68 + index, 72 + index, 70 + index, 75 + index, 69 + index, 71 + index],
        "FBG": [Decimal("4.7") + Decimal(index) / 10, Decimal("4.9") + Decimal(index) / 10,
                Decimal("5.0") + Decimal(index) / 10, Decimal("5.1") + Decimal(index) / 10],
        "TEMP": [Decimal("36.4"), Decimal("36.6"), Decimal("36.5"), Decimal("36.7")],
        "SPO2": [99 - index % 2, 98, 99, 97 + index % 3],
    }
    day_steps = {
        "WEIGHT": (56, 49, 42, 35, 28, 21, 14, 7),
        "HR": (45, 36, 27, 18, 9, 2),
        "FBG": (48, 32, 16, 3),
        "TEMP": (30, 20, 10, 1),
        "SPO2": (24, 16, 8, 1),
    }
    for code, values in measurement_series.items():
        for sequence, (days_ago, value) in enumerate(zip(day_steps[code], values)):
            db.session.add(SelfMeasurement(
                user_id=user.id,
                indicator_dict_id=indicators[code].id,
                value=value,
                measured_at=(now - timedelta(days=days_ago)).replace(
                    hour=7 + sequence % 3,
                    minute=(index * 7 + sequence * 11) % 60,
                    second=0,
                    microsecond=0,
                ),
            ))


def _seed_published_report(
    *, user, institution, staff_user, package, exam_day, indicators, now, index,
    weight_value=None,
):
    report = InstitutionReport(
        institution_id=institution.id,
        created_by_user_id=staff_user.id,
        created_by_username_snapshot=staff_user.username,
        subject_name_snapshot=user.real_name,
        subject_health_id=user.health_id,
        exam_date=exam_day,
        package_id=package.id,
        matched_user_id=user.id,
        status="published",
        locked_at=datetime.combine(exam_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=11),
        submitted_at=datetime.combine(exam_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12),
        published_at=datetime.combine(exam_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12),
        created_at=min(now, datetime.combine(exam_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)),
    )
    db.session.add(report)
    db.session.flush()

    values = {
        "WEIGHT": weight_value or f"{62 + index * 4 + (exam_day.toordinal() % 3) / 10:.1f}",
        "BMI": f"{20.2 + index * 0.6:.1f}",
        "HR": str(67 + index * 3),
        "FBG": "5.2" if weight_value == "71.9" else f"{4.7 + index * 0.25:.1f}",
        "TC": f"{4.1 + index * 0.25:.1f}",
        "ALT": str(18 + index * 5),
        "UA": str(250 + index * 28),
    }
    abnormal_codes = {"FBG", "TC"} if index == 5 else set()
    report.indicators.extend([
        ReportIndicator(
            indicator_dict_id=indicators[code].id,
            value=value,
            is_abnormal=code in abnormal_codes,
            input_source="ocr" if position % 3 == 1 else "manual",
        )
        for position, (code, value) in enumerate(values.items())
    ])
    return report


def seed_demo_data():
    if User.query.filter_by(username="test1").first() is not None:
        return
    institutions = Institution.query.order_by(Institution.id).limit(3).all()
    if len(institutions) < 3:
        return

    password = "Shuhealthdoc！"
    demo_admin = User(username="demo_admin", role="admin", email="demo-admin@example.test")
    demo_admin.set_password(password)
    db.session.add(demo_admin)

    profile_seeds = (
        ("test1", "林晓晨", "male", "无已知过敏", "久坐办公，关注体重和年度健康变化", "HID-8K3M2Q7A"),
        ("test2", "陈雨桐", "female", "无已知过敏", "有糖代谢家族史，持续记录空腹血糖", "HID-5R9T4W2C"),
        ("test3", "林国安", "male", "青霉素过敏", "轻度血脂异常，家人协助安排年度体检", "HID-7N2P6X8D"),
        ("test4", "周婧", "female", "海鲜过敏", "饮食不规律，关注肝胆与代谢健康", "HID-4V8J3L5F"),
        ("test5", "顾远", "male", "无已知过敏", "有吸烟史，关注肺功能和血氧变化", "HID-9C6H2M7K"),
    )
    people = []
    for index, (username, real_name, gender, allergy, history, health_id) in enumerate(profile_seeds, start=1):
        user = User(
            username=username,
            role="user",
            health_id=health_id,
            real_name=real_name,
            birth_date=date(1983 + index * 5, index, min(index * 4, 28)),
            gender=gender,
            identity_completed_at=datetime.now(timezone.utc),
            allergy_history=allergy,
            medical_history=history,
            email=f"{username}@example.test",
            phone=f"138000000{index:02d}",
        )
        user.set_password(password)
        db.session.add(user)
        people.append(user)

    staff_by_institution = []
    for institution_index, institution in enumerate(institutions, start=1):
        institution_staff = []
        user = User(
            username=f"institution{institution_index}_staff1",
            role="institution_admin",
            managed_institution_id=institution.id,
            email=f"institution{institution_index}-staff1@example.test",
        )
        user.set_password(password)
        db.session.add(user)
        institution_staff.append(user)
        staff_by_institution.append(institution_staff)
    db.session.flush()

    now = datetime.now(timezone.utc)
    for viewer, owner, relation_name, authorized in (
        (people[0], people[1], "家人", True),
        (people[0], people[2], "父母", True),
        (people[1], people[3], "家人", True),
        (people[3], people[4], "朋友", False),
    ):
        db.session.add(FriendRelation(
            user_id=viewer.id,
            friend_user_id=owner.id,
            pair_key=FriendRelation.canonical_pair_key(viewer.id, owner.id),
            relation_name=relation_name,
            friend_relation_name="亲友" if authorized else None,
            status="active" if authorized else "pending",
            auth_status=authorized,
            reverse_auth_status=authorized,
            booking_auth_status=authorized,
            reverse_booking_auth_status=authorized,
            booking_authorized_at=now if authorized else None,
            reverse_booking_authorized_at=now if authorized else None,
            accepted_at=now if authorized else None,
        ))

    indicators = {row.code: row for row in IndicatorDict.query.all()}
    today = date.today()
    for index, person in enumerate(people, start=1):
        _seed_self_measurements(person, index, indicators, now)

        old_institution_index = (index - 1) % len(institutions)
        old_institution = institutions[old_institution_index]
        _seed_published_report(
            user=person,
            institution=old_institution,
            staff_user=staff_by_institution[old_institution_index][0],
            package=old_institution.packages[(index - 1) % len(old_institution.packages)],
            exam_day=today - timedelta(days=62 + index * 3),
            indicators=indicators,
            now=now,
            index=index,
        )

        if index != 1:
            recent_institution_index = index % len(institutions)
            recent_institution = institutions[recent_institution_index]
            _seed_published_report(
                user=person,
                institution=recent_institution,
                staff_user=staff_by_institution[recent_institution_index][0],
                package=recent_institution.packages[index % len(recent_institution.packages)],
                exam_day=today - timedelta(days=8 + index * 2),
                indicators=indicators,
                now=now,
                index=index,
            )

    # Fixed test1 report retains the exact trend-priority fixture used by the tests.
    exam_day = today - timedelta(days=4)
    _seed_published_report(
        user=people[0],
        institution=institutions[0],
        staff_user=staff_by_institution[0][0],
        package=institutions[0].packages[0],
        exam_day=exam_day,
        indicators=indicators,
        now=now,
        index=1,
        weight_value="71.9",
    )
    for hour, value in ((8, Decimal("72.2")), (20, Decimal("72.0"))):
        db.session.add(SelfMeasurement(
            user_id=people[0].id,
            indicator_dict_id=indicators["WEIGHT"].id,
            value=value,
            measured_at=datetime.combine(exam_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=hour),
        ))
    db.session.add(SelfMeasurement(
        user_id=people[0].id,
        indicator_dict_id=indicators["HR"].id,
        value=78,
        measured_at=datetime.combine(exam_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=21),
    ))

    additional_staff = staff_by_institution[1][0]
    additional_report = InstitutionReport(
        institution_id=institutions[1].id,
        created_by_user_id=additional_staff.id,
        created_by_username_snapshot=additional_staff.username,
        subject_name_snapshot=people[1].real_name,
        subject_health_id=people[1].health_id,
        exam_date=today - timedelta(days=20),
        matched_user_id=people[1].id,
        status="published",
        locked_at=now - timedelta(days=20),
        submitted_at=now - timedelta(days=20),
        published_at=now - timedelta(days=20),
    )
    db.session.add(additional_report)
    db.session.flush()
    additional_report.indicators.append(ReportIndicator(
        indicator_dict_id=indicators["FBG"].id,
        value="5.8",
        input_source="manual",
    ))
    db.session.commit()


def seed_admin_user():
    default_admin_username = os.getenv("DEFAULT_ADMIN_USERNAME", "admin").strip()
    allow_insecure_local_demo = os.getenv(
        "ALLOW_INSECURE_LOCAL_DEMO", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    require_secure_admin = bool(
        current_app.config.get("REQUIRE_SECURE_DEFAULT_ADMIN", False)
        and not allow_insecure_local_demo
    )
    if allow_insecure_local_demo and current_app.config.get(
        "REQUIRE_SECURE_DEFAULT_ADMIN", False
    ):
        current_app.logger.warning(
            "production is running in loopback-only local demo mode; "
            "do not expose this process to the network"
        )
    configured_admin_password = os.getenv("DEFAULT_ADMIN_PASSWORD", "").strip()
    default_admin_password = configured_admin_password or "admin123"
    default_admin_email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@example.com").strip()

    if not default_admin_username or not default_admin_password:
        return

    admin = User.query.filter_by(username=default_admin_username).first()
    if admin is not None:
        # Startup seeding must never undo an explicit account suspension.  It
        # may repair the reserved administrator's authentication invariants,
        # but every such repair must invalidate credentials issued against the
        # previous password/role/binding state.
        authentication_state_changed = False
        if require_secure_admin and admin.check_password("admin123"):
            if (
                len(configured_admin_password) < 12
                or configured_admin_password == "admin123"
            ):
                raise RuntimeError(
                    "Production startup refused the insecure default admin password. "
                    "Set DEFAULT_ADMIN_PASSWORD to at least 12 characters in backend/.env."
                )
            admin.set_password(configured_admin_password)
            authentication_state_changed = True
        if admin.role != "admin" or admin.managed_institution_id is not None or admin.health_id is not None:
            admin.role = "admin"
            admin.managed_institution_id = None
            admin.health_id = None
            authentication_state_changed = True
        if authentication_state_changed:
            increment_user_security_epochs(admin.id)
            revoke_account_security_artifacts(admin.id)
            db.session.commit()
        return

    if require_secure_admin and (
        len(configured_admin_password) < 12
        or configured_admin_password == "admin123"
    ):
        raise RuntimeError(
            "Production startup requires DEFAULT_ADMIN_PASSWORD with at least 12 characters "
            "before creating the initial administrator."
        )

    admin = User(
        username=default_admin_username,
        email=default_admin_email or None,
        role="admin",
    )
    admin.set_password(default_admin_password)
    db.session.add(admin)
    db.session.commit()
