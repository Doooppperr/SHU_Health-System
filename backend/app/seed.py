import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import current_app

from app.extensions import db
from app.models import (
    FriendRelation, IndicatorCategory, IndicatorDict,
    Institution, InstitutionReport, Package, ReportIndicator, SelfMeasurement, User,
)


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
        "gender_scope": "female_all",
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
        "reference_low": Decimal("36.0"), "reference_high": Decimal("37.3"),
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


def seed_indicator_dicts():
    if IndicatorDict.query.first() is not None:
        return

    category_map = {}
    for category_payload in INDICATOR_CATEGORY_SEEDS:
        category = IndicatorCategory(
            name=category_payload["name"],
            sort_order=category_payload["sort_order"],
        )
        db.session.add(category)
        db.session.flush()
        category_map[category.name] = category

    for item in INDICATOR_DICT_SEEDS:
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

    db.session.commit()


def seed_core_data():
    seed_admin_user()
    seed_institutions_and_packages()
    seed_indicator_dicts()
    seed_demo_data()


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
        ("test1", "演示用户1", "male", "无已知过敏", "模拟轻度脂代谢关注"),
        ("test2", "演示用户2", "female", "无已知过敏", "模拟血糖随访资料"),
        ("test3", "演示用户3", "male", "青霉素过敏", "模拟常规年度体检资料"),
        ("test4", "演示用户4", "female", "海鲜过敏", "模拟心率与体重管理资料"),
        ("test5", "演示用户5", "other", "无已知过敏", "模拟多指标异常关注资料"),
    )
    people = []
    for index, (username, real_name, gender, allergy, history) in enumerate(profile_seeds, start=1):
        user = User(
            username=username,
            role="user",
            health_id=f"HID-DEMO000{index}",
            real_name=real_name,
            birth_date=date(1983 + index * 5, index, min(index * 4, 28)),
            gender=gender,
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
        for staff_index in (1, 2):
            user = User(
                username=f"institution{institution_index}_staff{staff_index}",
                role="institution_admin",
                managed_institution_id=institution.id,
                email=f"institution{institution_index}-staff{staff_index}@example.test",
            )
            user.set_password(password)
            db.session.add(user)
            institution_staff.append(user)
        staff_by_institution.append(institution_staff)
    db.session.flush()

    for viewer, owner, relation_name, authorized in (
        (people[0], people[1], "家人", True),
        (people[0], people[2], "父母", True),
        (people[1], people[3], "家人", True),
        (people[3], people[4], "朋友", False),
    ):
        db.session.add(FriendRelation(
            user_id=viewer.id,
            friend_user_id=owner.id,
            relation_name=relation_name,
            auth_status=authorized,
        ))

    indicators = {row.code: row for row in IndicatorDict.query.all()}
    now = datetime.now(timezone.utc)
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
                staff_user=staff_by_institution[recent_institution_index][1],
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

    waiting_staff = staff_by_institution[1][0]
    withdrawn = InstitutionReport(
        institution_id=institutions[1].id,
        created_by_user_id=waiting_staff.id,
        created_by_username_snapshot=waiting_staff.username,
        subject_name_snapshot=people[1].real_name,
        subject_health_id=people[1].health_id,
        exam_date=today - timedelta(days=20),
        matched_user_id=people[1].id,
        status="withdrawn",
        locked_at=now - timedelta(days=20),
        submitted_at=now - timedelta(days=20),
        published_at=now - timedelta(days=20),
        withdrawn_at=now - timedelta(days=10),
    )
    db.session.add(withdrawn)
    db.session.flush()
    withdrawn.indicators.append(ReportIndicator(
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
        password_changed = False
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
            password_changed = True
        if admin.role != "admin" or admin.managed_institution_id is not None or admin.health_id is not None:
            admin.role = "admin"
            admin.managed_institution_id = None
            admin.health_id = None
            password_changed = True
        if not admin.is_active:
            admin.is_active = True
            password_changed = True
        if password_changed:
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
