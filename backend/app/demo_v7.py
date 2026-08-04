"""Deterministic, human-oriented schema-v8 demonstration snapshot.

The regular application startup only creates this snapshot for an empty local
database.  Destructive replacement of an existing demo snapshot is exposed by
``scripts/reset_v8_demo_data.py`` and guarded by strict account checks.
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from flask import current_app

from app.demo_indicator_values import demo_realistic_value
from app.extensions import db
from app.services.indicator_values import evaluate_result_status
from app.services.report_conclusions import (
    ASSET_FINDINGS,
    normalize_report_records,
)
from app.services.password_challenges import (
    consume_password_challenges,
    increment_user_security_epochs,
    revoke_account_security_artifacts,
)
from app.models import (
    Appointment,
    AppointmentComplaint,
    AppointmentCapacitySlot,
    AppointmentEvent,
    AvailabilityNotificationEvent,
    BookingGroup,
    BookingParticipantAuthorization,
    BookingParticipantToken,
    Comment,
    CommentAppeal,
    CommentSanction,
    ComplaintEvent,
    ComplaintMessage,
    FriendRelation,
    HealthDomain,
    Institution,
    InstitutionAudienceInsightCache,
    InstitutionImage,
    InstitutionInvite,
    InstitutionReport,
    IndicatorDict,
    NotificationDelivery,
    NotificationOutbox,
    Organization,
    Package,
    PackageChangeRequest,
    PackageVersion,
    PackageVersionDomain,
    ReportAsset,
    ReportAssetAnnotation,
    ReportIndicator,
    ReportTextResult,
    ReportAccessLog,
    ReportAssetType,
    SelfMeasurement,
    User,
    UserNotification,
    PackageVersionAssetRequirement,
    WaitlistSubscription,
    WaitlistSubscriptionParticipant,
)


DEMO_PASSWORD = "Shuhealthdoc！"
DEMO_DATASET_VERSION = 12
DEMO_UPLOAD_DOCTOR_NAME = "周明远"
DEMO_REVIEW_DOCTOR_NAME = "许文静"
DEMO_USERNAMES = tuple(f"test{index}" for index in range(1, 7))
DEMO_PROFILE_USERNAMES = tuple(f"test{index}" for index in range(1, 6))
DEMO_STAFF_USERNAMES = tuple(
    f"institution{institution}_staff1" for institution in range(1, 16)
)
LEGACY_EXTRA_STAFF_USERNAMES = {
    f"institution{institution}_staff2" for institution in range(1, 4)
}
REQUIRED_DEMO_USERNAMES = {"demo_admin", *DEMO_USERNAMES, *DEMO_STAFF_USERNAMES}
LEGACY_DEMO_USERNAMES = {
    "demo_admin", *DEMO_PROFILE_USERNAMES,
    *(f"institution{institution}_staff1" for institution in range(1, 4)),
}


ORGANIZATION_SCENARIOS = (
    {"name": "澄心健康管理中心", "description": "面向职场人和家庭成员的一站式年度体检与健康管理机构。", "service_features": ["家庭同行体检", "年度健康档案", "跨分院报告衔接"]},
    {"name": "衡康代谢与慢病管理中心", "description": "聚焦糖脂代谢、肝胆健康与慢病风险连续管理。", "service_features": ["代谢专项", "慢病随访", "营养生活方式建议"]},
    {"name": "云川影像与呼吸体检中心", "description": "提供呼吸功能、心电与循环影像检查的专业体检机构。", "service_features": ["呼吸功能", "心电影像", "职场体检"]},
    {"name": "安沐女性与家庭健康中心", "description": "围绕女性不同生命阶段及家庭健康需要提供预约制体检服务。", "service_features": ["女性专项", "家庭健康", "分阶段评估"]},
    {"name": "仁序职业健康与综合体检中心", "description": "服务企业员工与个人年度综合体检，重视流程效率与结果连续性。", "service_features": ["职业人群", "综合体检", "企业团队服务"]},
)


INSTITUTION_SCENARIOS = (
    {
        "name": "澄心健康管理中心",
        "branch_name": "徐汇综合院区",
        "district": "徐汇区",
        "address": "斜土路1609号健康服务楼2-5层",
        "metro_info": "4号线、12号线大木桥路站3号口步行约6分钟",
        "consult_phone": "021-64031188",
        "closed_day": "周一休",
        "description": "面向职场人和家庭成员的一站式年度体检与慢病风险评估中心。",
        "daily_appointment_limit": 18,
        "notification_email": "xuhui-demo@example.test",
        "packages": (
            {
                "name": "都市年度基础体检",
                "focus_area": "年度基础筛查与常见风险识别",
                "price": "699.00",
                "audience": "18—55 岁、希望完成年度基础健康检查的职场人",
                "description": "覆盖基础体征、循环、代谢、消化与肾脏等常见健康领域，适合作为年度健康档案起点。",
                "booking_notice": "检查前一天清淡饮食并在晚22点后禁食；当天请携带有效证件，具体检查结果以机构实际完成内容为准。",
                "domains": ("basic", "cardio", "metabolic", "digestive", "renal"),
                "historical_v1": {"price": "659.00", "booking_notice": "请空腹到检，具体检查结果以机构实际完成内容为准。"},
            },
            {
                "name": "都市年度综合体检",
                "focus_area": "成年人多系统年度综合评估",
                "price": "1699.00",
                "audience": "希望一次完成体格、生化、血液、呼吸及常见专科检查的成年人",
                "description": "覆盖基础体征、循环、代谢、肝胆胰、肾脏尿检、血液、呼吸及常见专科项目，形成完整年度健康档案。",
                "booking_notice": "需空腹8—10小时；当天请携带有效证件、既往报告和用药清单，并按现场流程完成各项检查。",
                "domains": (
                    "basic", "cardio", "metabolic", "digestive",
                    "renal", "hematology", "respiratory", "other",
                ),
            },
            {
                "name": "心脑血管风险筛查",
                "focus_area": "心脑血管与循环专项",
                "price": "899.00",
                "audience": "有血压、血脂或家族心血管风险关注的人群",
                "description": "围绕心脑血管与循环领域开展风险筛查，并由机构按实际检查形成结果。",
                "booking_notice": "如正在服用心血管相关药物，请按医嘱正常服药并携带用药清单。",
                "domains": ("cardio",),
            },
            {
                "name": "家庭长辈健康评估",
                "focus_area": "长辈慢病风险综合评估",
                "price": "1299.00",
                "audience": "50 岁以上及需要家人协助预约的长辈",
                "description": "综合关注基础体征、循环、代谢和肾脏健康，支持家庭成员代预约。",
                "booking_notice": "建议由熟悉既往用药的家属陪同；代预约只用于安排体检，不自动开放健康数据。",
                "domains": ("basic", "cardio", "metabolic", "renal"),
            },
        ),
    },
    {
        "name": "衡康代谢与慢病管理中心",
        "branch_name": "静安院区",
        "district": "静安区",
        "address": "恒丰路688号健康管理楼3层",
        "metro_info": "1号线上海火车站站5号口步行约8分钟",
        "consult_phone": "021-63810221",
        "closed_day": "周四休",
        "description": "聚焦糖脂代谢、肝胆健康与慢病风险管理的预约制健康服务中心。",
        "daily_appointment_limit": 12,
        "notification_email": "jingan-demo@example.test",
        "packages": (
            {
                "name": "糖脂代谢专项",
                "focus_area": "糖脂代谢专项评估",
                "price": "799.00",
                "audience": "关注空腹血糖、体重或血脂变化的人群",
                "description": "围绕内分泌与代谢领域形成结构化指标和医生文字结论。",
                "booking_notice": "需空腹8—10小时；如有近期自测记录，可在到检时向医生说明。",
                "domains": ("metabolic",),
            },
            {
                "name": "肝胆代谢联合评估",
                "focus_area": "代谢与肝胆联合评估",
                "price": "999.00",
                "audience": "有脂肪肝、饮食不规律或代谢风险关注的人群",
                "description": "联合关注代谢以及消化与肝胆胰领域，实际结果按当次检查归档。",
                "booking_notice": "检查前三天避免大量饮酒和高脂饮食；腹部检查安排以现场指引为准。",
                "domains": ("metabolic", "digestive"),
            },
            {
                "name": "慢病风险综合评估",
                "focus_area": "常见慢病多领域风险评估",
                "price": "1299.00",
                "audience": "需要连续观察体重、循环、代谢和肾脏指标的人群",
                "description": "用于形成多领域慢病风险基线，并与后续个人自测和复查结果分来源对照。",
                "booking_notice": "请携带近期用药清单和既往检查摘要；平台不会将不同来源结果静默合并。",
                "domains": ("basic", "cardio", "metabolic", "renal"),
            },
        ),
    },
    {
        "name": "云川影像与呼吸体检中心",
        "branch_name": "杨浦院区",
        "district": "杨浦区",
        "address": "淞沪路388号云川医学中心5层",
        "metro_info": "10号线江湾体育场站11号口步行约5分钟",
        "consult_phone": "021-35360351",
        "closed_day": "周五休",
        "description": "提供呼吸功能、心电与循环影像以及职场综合检查的专业体检中心。",
        "daily_appointment_limit": 15,
        "notification_email": "yangpu-demo@example.test",
        "packages": (
            {
                "name": "呼吸与肺功能专项",
                "focus_area": "呼吸系统专项",
                "price": "699.00",
                "audience": "长期咳嗽、吸烟史或关注肺功能变化的人群",
                "description": "围绕呼吸系统形成肺功能、血氧及相关影像或文字结果。",
                "booking_notice": "检查前2小时避免剧烈运动和吸烟；影像结果可能以图片或PDF形式归档。",
                "domains": ("respiratory",),
            },
            {
                "name": "心电与循环影像专项",
                "focus_area": "心电与循环影像专项",
                "price": "899.00",
                "audience": "关注心率、心电或循环影像结果的人群",
                "description": "在心脑血管与循环领域归档结构化指标、心电图片和机构批注。",
                "booking_notice": "检查当天避免浓茶和咖啡；如有既往心电图，可携带供医生参考。",
                "domains": ("cardio",),
            },
            {
                "name": "职场综合体检",
                "focus_area": "职场人群多领域综合筛查",
                "price": "1099.00",
                "audience": "工作节奏快、需要兼顾基础与呼吸健康的职场人",
                "description": "覆盖基础体征、循环、呼吸和消化领域，适用于常规职场年度检查。",
                "booking_notice": "建议提前15分钟到场；具体检查结果以机构当日实际完成内容为准。",
                "domains": ("basic", "cardio", "respiratory", "digestive"),
            },
        ),
    },
)


def _demo_package(name, focus, price, domains, audience):
    return {
        "name": name,
        "focus_area": focus,
        "price": price,
        "audience": audience,
        "description": f"围绕{focus}提供预约制检查，并按实际完成内容形成可持续查看的机构体检档案。",
        "booking_notice": "请按预约时间提前15分钟到院；涉及采血时需空腹8—10小时，具体准备事项以分院通知为准。",
        "domains": domains,
    }


def _demo_branch(name, branch_name, district, address, phone, packages):
    return {
        "name": name,
        "branch_name": branch_name,
        "district": district,
        "address": address,
        "metro_info": "地铁站步行约8分钟，预约成功后可查看详细到院指引",
        "consult_phone": phone,
        "closed_day": "周日休",
        "description": f"{name}{branch_name}，提供独立预约与本院体检服务，并可衔接同机构其他分院的已归档报告。",
        "daily_appointment_limit": 16,
        "notification_email": f"branch-{district}@example.test",
        "packages": packages,
    }


INSTITUTION_SCENARIOS += (
    _demo_branch("澄心健康管理中心", "浦东陆家嘴院区", "浦东新区", "浦东南路855号健康中心4层", "021-58881201", (
        _demo_package("陆家嘴职场轻体检", "基础体征与代谢风险筛查", "599.00", ("basic", "metabolic", "hematology"), "工作节奏快、希望半日完成基础筛查的职场人"),
        _demo_package("陆家嘴商务人士心血管筛查", "心脑血管与循环风险评估", "899.00", ("cardio",), "长期出差、应酬或关注循环风险的商务人士"),
    )),
    _demo_branch("澄心健康管理中心", "闵行虹桥院区", "闵行区", "申长路988号虹桥健康楼2层", "021-54881102", (
        _demo_package("家庭同行综合评估", "家庭成员年度综合健康评估", "1099.00", ("basic", "cardio", "metabolic", "renal"), "希望与家人同行完成年度体检的人群"),
    )),
    _demo_branch("衡康代谢与慢病管理中心", "普陀长寿路院区", "普陀区", "长寿路468号门诊楼3层", "021-62771103", (
        _demo_package("体重与糖代谢跟踪", "体重、血糖与生活方式连续评估", "699.00", ("basic", "metabolic"), "正在进行体重或糖代谢管理的人群"),
    )),
    _demo_branch("衡康代谢与慢病管理中心", "长宁中山公园院区", "长宁区", "长宁路1027号健康楼5层", "021-62121104", (
        _demo_package("血脂与循环联合筛查", "血脂代谢及循环风险评估", "899.00", ("metabolic", "cardio"), "关注血脂和心脑血管风险的人群"),
    )),
    _demo_branch("云川影像与呼吸体检中心", "虹口北外滩院区", "虹口区", "东大名路1089号医学影像楼", "021-65121105", (
        _demo_package("北外滩呼吸影像专项", "呼吸功能与检查影像评估", "799.00", ("respiratory",), "关注肺功能或有长期呼吸道暴露的人群"),
    )),
    _demo_branch("云川影像与呼吸体检中心", "宝山大场院区", "宝山区", "沪太路1866号体检中心3层", "021-66521106", (
        _demo_package("循环影像复查", "心电与循环影像复查", "899.00", ("cardio",), "已有既往心电或循环检查资料的人群"),
    )),
    _demo_branch("安沐女性与家庭健康中心", "黄浦院区", "黄浦区", "西藏南路518号安沐健康楼", "021-63281107", (
        _demo_package("女性年度基础关怀", "女性年度基础与代谢评估", "799.00", ("basic", "metabolic", "other"), "关注年度基础健康的成年女性"),
        _demo_package("女性心血管与代谢评估", "女性循环与代谢联合风险评估", "1099.00", ("cardio", "metabolic"), "关注血脂、血糖和循环风险的女性"),
        _demo_package("家庭照护者健康评估", "家庭照护者综合健康评估", "1299.00", ("basic", "cardio", "digestive", "renal"), "长期承担家庭照护、需要系统体检的人群"),
    )),
    _demo_branch("安沐女性与家庭健康中心", "浦东张江院区", "浦东新区", "祖冲之路887号健康服务中心", "021-50801108", (
        _demo_package("张江女性轻体检", "女性基础与消化健康筛查", "699.00", ("basic", "digestive", "other"), "希望半日完成基础检查的女性职场人"),
    )),
    _demo_branch("澄心健康管理中心", "黄浦人民广场院区", "黄浦区", "南京西路288号健康管理楼6层", "021-63221109", (
        _demo_package("人民广场都市综合体检", "中心城区职场人年度综合筛查", "799.00", ("basic", "cardio", "metabolic", "digestive", "hematology"), "在中心城区工作、希望便捷完成年度体检的人群"),
    )),
    _demo_branch("仁序职业健康与综合体检中心", "松江院区", "松江区", "新松江路925号仁序健康楼", "021-57701110", (
        _demo_package("职场年度标准体检", "职场人年度多领域筛查", "699.00", ("basic", "cardio", "metabolic", "digestive"), "18—60岁常规职场体检人群"),
        _demo_package("高强度工作人群评估", "循环、代谢与消化联合评估", "999.00", ("cardio", "metabolic", "digestive"), "长期加班、饮食作息不规律的人群"),
    )),
    _demo_branch("澄心健康管理中心", "宝山顾村院区", "宝山区", "陆翔路111号澄心健康楼3层", "021-66761111", (
        _demo_package("北上海家庭健康评估", "家庭成员基础、循环与呼吸健康筛查", "899.00", ("basic", "cardio", "respiratory"), "居住在北上海、希望家庭同行体检的人群"),
    )),
    _demo_branch("衡康代谢与慢病管理中心", "浦东金桥院区", "浦东新区", "金科路2889号慢病管理中心4层", "021-58981112", (
        _demo_package("金桥慢病风险评估", "职业人群代谢与慢病风险评估", "899.00", ("basic", "cardio", "metabolic", "renal"), "园区职工及需要持续观察代谢指标的人群"),
    )),
)


PROFILE_SCENARIOS = {
    "test1": ("林晓晨", date(1989, 4, 18), "male", "无已知过敏", "久坐办公，关注体重和年度健康变化"),
    "test2": ("陈雨桐", date(1992, 8, 7), "female", "无已知过敏", "有糖代谢家族史，持续记录空腹血糖"),
    "test3": ("林国安", date(1962, 11, 23), "male", "青霉素过敏", "轻度血脂异常，家人协助安排年度体检"),
    "test4": ("周婧", date(1986, 2, 14), "female", "海鲜过敏", "饮食不规律，关注肝胆与代谢健康"),
    "test5": ("顾远", date(1978, 6, 30), "male", "无已知过敏", "有吸烟史，关注肺功能和血氧变化"),
}

PROFILE_HEALTH_IDS = {
    "test1": "HID-8K3M2Q7A",
    "test2": "HID-5R9T4W2C",
    "test3": "HID-7N2P6X8D",
    "test4": "HID-4V8J3L5F",
    "test5": "HID-9C6H2M7K",
    "test6": "HID-3F7Q9R2N",
}


ACCOUNT_IDENTITY_FIELDS = (
    "id", "username", "password_hash", "role", "email", "health_id",
    "managed_institution_id", "phone", "is_active", "created_at",
)


class DemoResetSafetyError(RuntimeError):
    pass


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _domain_map() -> dict[str, HealthDomain]:
    return {item.code: item for item in HealthDomain.query.all()}


def _package_key(institution_index: int, name: str) -> tuple[int, str]:
    return institution_index, name


def _write_png(path: Path, palette: tuple[tuple[int, int, int], ...], width=480, height=270) -> bytes:
    """Create a tiny deterministic raster fixture used only by automated tests."""
    if current_app.config.get("TESTING", False):
        # Unit tests validate metadata and permissions, not raster rendering.
        # Keeping the in-memory fixture tiny avoids repeatedly generating six
        # large institution illustrations for every app fixture.
        width, height = 2, 2
        palette = (palette[0],)
        rows = b"\x00" + bytes(palette[0]) * width
        rows *= height
        def test_chunk(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        return (
            b"\x89PNG\r\n\x1a\n"
            + test_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + test_chunk(b"IDAT", zlib.compress(rows, 9))
            + test_chunk(b"IEND", b"")
        )
    try:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (width, height), palette[0])
        draw = ImageDraw.Draw(image)
        stripe = max(1, width // len(palette))
        for index, color in enumerate(palette):
            left = index * stripe
            right = width if index == len(palette) - 1 else (index + 1) * stripe
            draw.rectangle((left, 0, right, height), fill=color)
        for offset in range(-height, width, max(28, width // 18)):
            draw.line((offset, 0, offset + height, height), fill=(255, 255, 255), width=max(1, width // 360))

        font = None
        chinese_font = False
        candidates = (
            ("C:/Windows/Fonts/msyh.ttc", True),
            ("C:/Windows/Fonts/simhei.ttf", True),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", True),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", False),
        )
        for candidate, supports_chinese in candidates:
            if Path(candidate).is_file():
                font = ImageFont.truetype(candidate, max(15, width // 31))
                chinese_font = supports_chinese
                break
        if font is None:
            font = ImageFont.load_default()
        watermark = "机构影像资料" if chinese_font else "INSTITUTION IMAGE"
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        box = overlay_draw.textbbox((0, 0), watermark, font=font)
        text_width, text_height = box[2] - box[0], box[3] - box[1]
        padding_x, padding_y = max(12, width // 45), max(8, height // 34)
        right, bottom = width - max(14, width // 40), height - max(14, height // 30)
        left, top = right - text_width - padding_x * 2, bottom - text_height - padding_y * 2
        overlay_draw.rounded_rectangle((left, top, right, bottom), radius=max(8, height // 34), fill=(8, 23, 28, 178))
        overlay_draw.text((left + padding_x, top + padding_y - box[1]), watermark, font=font, fill=(255, 255, 255, 238))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG", compress_level=9, optimize=False)
        raw = buffer.getvalue()
    except ImportError:
        rows = bytearray()
        stripe = max(1, width // len(palette))
        for y in range(height):
            rows.append(0)
            for x in range(width):
                base = palette[min(x // stripe, len(palette) - 1)]
                shade = 18 if (x + y) % 37 < 4 else 0
                rows.extend(min(255, channel + shade) for channel in base)
        def chunk(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        raw += chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")
    if not current_app.config.get("TESTING", False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw


def _load_demo_media(
    storage_key: str,
    destination: Path,
    palette: tuple[tuple[int, int, int], ...],
    *,
    test_width: int = 480,
    test_height: int = 270,
) -> tuple[bytes, int, int]:
    """Copy a checked-in report asset without regenerating it."""
    if current_app.config.get("TESTING", False):
        raw = _write_png(destination, palette, test_width, test_height)
        return raw, 2, 2
    source = Path(__file__).resolve().parents[1] / "uploads" / storage_key
    if not source.is_file():
        raise DemoResetSafetyError(f"缺少报告媒体文件：{storage_key}")
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() != source.resolve():
        destination.write_bytes(raw)
    from PIL import Image
    with Image.open(BytesIO(raw)) as image:
        width, height = image.size
    return raw, width, height


# Checked-in report media used by the reset path.
CURATED_ASSET_SOURCES = {
    "US_THYROID": ("thyroid_normal",),
    "US_ABDOMEN": ("abdomen_liver", "abdomen_liver", "abdomen_liver"),
    "SPIROMETRY": ("spirometry_nih", "spirometry_nih"),
    "ECG_12": ("ecg_10sec", "ecg_10sec", "ecg_10sec", "ecg_10sec", "ecg_10sec"),
    "CHEST_IMAGE": ("chest_pa", "chest_lateral", "chest_pa"),
    "ECHO_HEART": ("echo_tte", "echo_tte"),
    "BLOOD_MICROSCOPY": ("blood_sem", "blood_sem", "blood_sem"),
}
CURATED_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "report_media"


def _load_curated_media(asset_code: str, destination: Path, sequence: int = 0) -> tuple[bytes, int, int]:
    if current_app.config.get("TESTING", False):
        return _write_png(destination, ((21, 96, 91),), 480, 270), 2, 2
    choices = CURATED_ASSET_SOURCES.get(asset_code)
    if not choices:
        raise DemoResetSafetyError(f"没有登记真实医学素材槽位：{asset_code}")
    source = CURATED_SOURCE_ROOT / f"{choices[sequence % len(choices)]}.png"
    if not source.is_file():
        raise DemoResetSafetyError(f"缺少真实医学素材：{source.name}")
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    from PIL import Image
    with Image.open(BytesIO(raw)) as image:
        image.verify()
    with Image.open(BytesIO(raw)) as image:
        return raw, image.width, image.height


def _create_catalog(institutions: list[Institution]) -> dict[tuple[int, str], Package]:
    domains = _domain_map()
    if len(domains) < 8:
        raise RuntimeError("health domains must be seeded before the demo catalog")
    package_map = {}
    now = datetime.now(timezone.utc)
    for institution_index, (institution, scenario) in enumerate(zip(institutions, INSTITUTION_SCENARIOS), start=1):
        for package_payload in scenario["packages"]:
            domain_codes = package_payload["domains"]
            package_type = "special" if len(domain_codes) == 1 else "combined"
            package = Package(
                institution_id=institution.id,
                name=package_payload["name"],
                focus_area=package_payload["focus_area"],
                gender_scope="all",
                price=Decimal(package_payload["price"]),
                description=package_payload["description"],
                package_type=package_type,
                audience=package_payload["audience"],
                booking_notice=package_payload["booking_notice"],
                is_active=True,
            )
            db.session.add(package)
            db.session.flush()
            history = package_payload.get("historical_v1")
            versions = []
            if history:
                versions.append(PackageVersion(
                    package_id=package.id,
                    version_number=1,
                    package_type=package_type,
                    name_snapshot=package.name,
                    price_snapshot=Decimal(history["price"]),
                    audience_snapshot=package.audience,
                    description_snapshot=package.description,
                    booking_notice_snapshot=history["booking_notice"],
                    approved_at=now - timedelta(days=240),
                ))
            versions.append(PackageVersion(
                package_id=package.id,
                version_number=2 if history else 1,
                package_type=package_type,
                name_snapshot=package.name,
                price_snapshot=package.price,
                audience_snapshot=package.audience,
                description_snapshot=package.description,
                booking_notice_snapshot=package.booking_notice,
                approved_at=now - timedelta(days=45 if history else 120),
            ))
            for version in versions:
                db.session.add(version)
                db.session.flush()
                for order, code in enumerate(domain_codes):
                    db.session.add(PackageVersionDomain(
                        package_version_id=version.id,
                        health_domain_id=domains[code].id,
                        sort_order=order,
                    ))
            package.current_version_id = versions[-1].id
            package_map[_package_key(institution_index, package.name)] = package
    db.session.flush()
    return package_map


def _apply_institution_scenario(institution: Institution, scenario: dict) -> None:
    for field in (
        "name", "branch_name", "district", "address", "metro_info",
        "consult_phone", "closed_day", "description", "daily_appointment_limit",
        "notification_email",
    ):
        setattr(institution, field, scenario[field])
    shared_email = current_app.config.get("DEMO_SHARED_EMAIL")
    if shared_email:
        institution.notification_email = shared_email
    institution.ext = None
    institution.logo_url = None
    institution.notification_enabled = True


def _ensure_organizations() -> dict[str, Organization]:
    rows = {item.name: item for item in Organization.query.all()}
    for scenario in ORGANIZATION_SCENARIOS:
        item = rows.get(scenario["name"])
        if item is None:
            item = Organization(name=scenario["name"], is_active=True)
            db.session.add(item)
            db.session.flush()
            rows[item.name] = item
        item.description = scenario["description"]
        item.service_features = scenario["service_features"]
    return rows


def _ensure_demo_branches() -> list[Institution]:
    organizations = _ensure_organizations()
    rows = Institution.query.order_by(Institution.id).all()
    if len(rows) not in {0, 3, 15}:
        raise DemoResetSafetyError(f"expected 0, 3 or 15 demo branches, found {len(rows)}")
    result = []
    for index, scenario in enumerate(INSTITUTION_SCENARIOS):
        organization = organizations[scenario["name"]]
        if index < len(rows):
            institution = rows[index]
        else:
            institution = Institution(
                organization_id=organization.id,
                name=organization.name,
                branch_name=scenario["branch_name"],
                district=scenario["district"],
                address=scenario["address"],
            )
            db.session.add(institution)
            db.session.flush()
        institution.organization_id = organization.id
        _apply_institution_scenario(institution, scenario)
        result.append(institution)
    db.session.flush()
    return result


def ensure_v7_demo_catalog() -> bool:
    """Create the five-organization, fifteen-branch catalog for a fresh database."""
    if Institution.query.first() is not None:
        return False
    institutions = _ensure_demo_branches()
    _create_catalog(institutions)
    db.session.commit()
    return True


def _create_demo_images(institutions: list[Institution]) -> None:
    upload_root = Path(current_app.config["UPLOAD_DIR"])
    palettes = (
        ((17, 94, 89), (42, 157, 143), (217, 241, 238)),
        ((39, 71, 119), (63, 117, 176), (224, 235, 247)),
        ((86, 58, 126), (123, 94, 165), (237, 229, 247)),
    )
    for index, institution in enumerate(institutions, start=1):
        palette = palettes[(index - 1) % len(palettes)]
        key = f"institutions/demo-v8/branch-{index}-cover.png"
        _load_demo_media(key, upload_root / key, palette, test_width=720, test_height=405)
        db.session.add(InstitutionImage(
            institution_id=institution.id,
            storage_key=key,
            image_url=f"/uploads/{key}",
            sort_order=0,
        ))


def ensure_v7_demo_accounts(*, commit: bool = True) -> bool:
    """Create fixed demo credentials and keep only whitelisted demo mailboxes in sync."""
    institutions = Institution.query.order_by(Institution.id).all()
    if len(institutions) != 15:
        raise RuntimeError("the schema v8 fifteen-branch catalog must exist before demo accounts")
    now = datetime.now(timezone.utc)
    shared_email = current_app.config.get("DEMO_SHARED_EMAIL") or "demo-shared@example.test"
    changed = False
    demo_admin = User.query.filter_by(username="demo_admin").first()
    if demo_admin is None:
        demo_admin = User(username="demo_admin", role="admin", email=shared_email, email_verified_at=now)
        demo_admin.set_password(DEMO_PASSWORD)
        db.session.add(demo_admin); changed = True
    elif demo_admin.email != shared_email:
        demo_admin.email = shared_email
        demo_admin.email_verified_at = now
        consume_password_challenges(demo_admin.id, consumed_at=now)
        changed = True
    for index, username in enumerate(DEMO_USERNAMES, start=1):
        existing = User.query.filter_by(username=username).first()
        if existing is not None:
            if existing.email != shared_email:
                existing.email = shared_email
                existing.email_verified_at = now
                consume_password_challenges(existing.id, consumed_at=now)
                changed = True
            continue
        scenario = PROFILE_SCENARIOS.get(username)
        name, birth_date, gender, allergy, history = (
            scenario if scenario else (None, None, None, None, None)
        )
        user = User(
            username=username,
            role="user",
            health_id=PROFILE_HEALTH_IDS[username],
            real_name=name,
            birth_date=birth_date,
            gender=gender,
            identity_completed_at=now if scenario else None,
            allergy_history=allergy,
            medical_history=history,
            email=shared_email,
            email_verified_at=now,
            phone=f"138000000{index:02d}",
        )
        user.set_password(DEMO_PASSWORD)
        db.session.add(user); changed = True
    for institution_index, institution in enumerate(institutions, start=1):
        username = f"institution{institution_index}_staff1"
        existing = User.query.filter_by(username=username).first()
        if existing is not None:
            if existing.email != shared_email:
                existing.email = shared_email
                existing.email_verified_at = now
                consume_password_challenges(existing.id, consumed_at=now)
                changed = True
            continue
        user = User(
            username=username,
            role="institution_admin",
            managed_institution_id=institution.id,
            email=shared_email,
            email_verified_at=now,
        )
        user.set_password(DEMO_PASSWORD)
        db.session.add(user); changed = True
    duplicate_security_ids = []
    for username in sorted(LEGACY_EXTRA_STAFF_USERNAMES):
        duplicate = User.query.filter_by(username=username).first()
        if duplicate is None:
            continue
        if duplicate.is_active or duplicate.managed_institution_id is not None:
            duplicate.is_active = False
            duplicate.managed_institution_id = None
            duplicate_security_ids.append(duplicate.id)
            changed = True
    increment_user_security_epochs(duplicate_security_ids)
    for duplicate_id in sorted(duplicate_security_ids):
        revoke_account_security_artifacts(duplicate_id)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return changed


def _update_demo_profiles() -> None:
    completed_at = datetime.now(timezone.utc)
    for username, (name, birth_date, gender, allergy, history) in PROFILE_SCENARIOS.items():
        user = User.query.filter_by(username=username).one()
        user.real_name = name
        user.birth_date = birth_date
        user.gender = gender
        user.identity_completed_at = user.identity_completed_at or completed_at
        user.allow_health_id_proxy_booking = username != "test5"
        user.allergy_history = allergy
        user.medical_history = history


def _add_measurement(user: User, indicator, value, when: datetime) -> None:
    db.session.add(SelfMeasurement(
        user_id=user.id,
        indicator_dict_id=indicator.id,
        value=Decimal(str(value)),
        measured_at=when,
    ))


def _seed_measurements(users: dict[str, User], indicators: dict, today: date) -> None:
    series = {
        "test1": {
            "HEIGHT": [(180, 176.0)],
            "WEIGHT": [(42, 73.4), (35, 73.0), (28, 72.8), (21, 72.6), (14, 72.4), (7, 72.1), (2, 71.9)],
            "HR": [(30, 76), (21, 73), (14, 72), (7, 70), (1, 72)],
            "FBG": [(28, 5.4), (14, 5.2), (3, 5.1)],
            "SPO2": [(10, 98), (3, 99), (1, 98)],
        },
        "test2": {
            "HEIGHT": [(180, 163.0)],
            "WEIGHT": [(35, 61.8), (21, 61.4), (7, 61.1)],
            "FBG": [(35, 5.9), (28, 5.8), (21, 5.7), (14, 5.6), (7, 5.5), (1, 5.4)],
            "HR": [(14, 74), (2, 72)],
        },
        "test3": {
            "WEIGHT": [(28, 70.8), (14, 70.5), (2, 70.2)],
            "HR": [(28, 79), (21, 78), (14, 76), (7, 77), (1, 75)],
            "SPO2": [(14, 97), (7, 98), (1, 97)],
        },
        "test4": {
            "HR": [(21, 77), (14, 75), (7, 74), (1, 73)],
            "FBG": [(21, 5.0), (7, 4.9)],
            "SPO2": [(28, 97), (14, 98), (7, 97), (1, 98)],
        },
        "test5": {
            "HEIGHT": [(180, 172.0)],
            "SPO2": [(28, 96), (21, 96), (14, 95), (7, 96), (1, 95)],
            "HR": [(14, 82), (7, 80), (1, 81)],
            "TEMP": [(10, 36.6), (3, 36.5), (1, 36.7)],
        },
    }
    for username, indicator_series in series.items():
        for code, points in indicator_series.items():
            for sequence, (days_ago, value) in enumerate(points):
                _add_measurement(users[username], indicators[code], value, _utc(today - timedelta(days=days_ago), 7 + sequence % 3, 10 + sequence * 3))


def _package_version(package: Package, number: int | None = None) -> PackageVersion:
    versions = sorted(package.versions, key=lambda item: item.version_number)
    if number is None:
        return next(item for item in versions if item.id == package.current_version_id)
    return next(item for item in versions if item.version_number == number)


def _create_booking_group(
    *, booker: User, participants: list[User], institution: Institution,
    package: Package, appointment_date: date, status: str,
    created_at: datetime, version_number: int | None = None,
) -> tuple[BookingGroup, list[Appointment]]:
    version = _package_version(package, version_number)
    domains = [row.domain.to_dict() for row in version.domains]
    group = BookingGroup(
        group_code=f"BG-V8-{DEMO_DATASET_VERSION}-{institution.id}-{package.id}-{appointment_date:%Y%m%d}-{booker.id}",
        booked_by_user_id=booker.id,
        institution_id=institution.id,
        package_id=package.id,
        package_version_id=version.id,
        appointment_date=appointment_date,
        party_size=len(participants),
        package_name_snapshot=version.name_snapshot,
        package_price_snapshot=version.price_snapshot,
        domain_snapshot=domains,
        booking_notice_snapshot=version.booking_notice_snapshot,
        notice_version_snapshot=version.version_number,
        notice_confirmed_at=created_at,
        contact_snapshot={"email": booker.email, "phone": booker.phone},
        created_at=created_at,
    )
    db.session.add(group)
    db.session.flush()
    appointments = []
    for participant in participants:
        active_date = appointment_date if status not in {"cancelled", "invalidated", "no_show", "institution_cancelled"} else None
        height = Decimal("176.0") if participant.gender == "male" else Decimal("163.0")
        weight = Decimal("72.0") if participant.gender == "male" else Decimal("59.0")
        appointment = Appointment(
            user_id=participant.id,
            institution_id=institution.id,
            package_id=package.id,
            package_version_id=version.id,
            booking_group_id=group.id,
            booked_by_user_id=booker.id,
            appointment_date=appointment_date,
            active_date_key=active_date,
            status=status,
            user_name_snapshot=participant.real_name,
            user_health_id_snapshot=participant.health_id,
            user_birth_date_snapshot=participant.birth_date,
            user_gender_snapshot=participant.gender,
            user_contact_snapshot=participant.phone or participant.email,
            height_cm_snapshot=height,
            weight_kg_snapshot=weight,
            bmi_snapshot=(weight / ((height / Decimal("100")) ** 2)).quantize(Decimal("0.01")),
            allergy_history_snapshot=participant.allergy_history,
            medical_history_snapshot=participant.medical_history,
            intake_captured_at=created_at,
            package_name_snapshot=version.name_snapshot,
            package_price_snapshot=version.price_snapshot,
            created_at=created_at,
        )
        if status in {"awaiting_report", "fulfilled"}:
            appointment.attended_at = _utc(appointment_date, 9, 20)
        if status == "fulfilled":
            appointment.fulfilled_at = _utc(appointment_date, 17, 30)
        elif status == "cancelled":
            appointment.cancelled_at = created_at + timedelta(days=1)
        elif status in {"invalidated", "no_show"}:
            appointment.invalidated_at = _utc(appointment_date + timedelta(days=1), 8)
            appointment.termination_party = "subject"
            appointment.termination_reason_code = "no_show"
        elif status == "institution_cancelled":
            appointment.invalidated_at = created_at + timedelta(hours=2)
            appointment.termination_party = "institution"
            appointment.termination_reason_code = "equipment_failure"
            appointment.termination_reason_text = "设备突发故障，机构已致歉并提供兄弟分院联系方式。"
        db.session.add(appointment)
        db.session.flush()
        db.session.add(AppointmentEvent(
            appointment_id=appointment.id,
            event_type="booked",
            status_snapshot="unfulfilled",
            message="预约成功",
            actor_user_id=booker.id,
            occurred_at=created_at,
        ))
        if appointment.attended_at:
            db.session.add(AppointmentEvent(
                appointment_id=appointment.id,
                event_type="attended",
                status_snapshot="awaiting_report",
                message="机构确认到检",
                occurred_at=appointment.attended_at,
            ))
        if appointment.fulfilled_at:
            db.session.add(AppointmentEvent(
                appointment_id=appointment.id,
                event_type="archived",
                status_snapshot="fulfilled",
                message="健康数据已归档",
                occurred_at=appointment.fulfilled_at,
            ))
        if appointment.cancelled_at:
            db.session.add(AppointmentEvent(
                appointment_id=appointment.id,
                event_type="cancelled",
                status_snapshot="cancelled",
                message="用户取消预约，后续已重新选择日期",
                actor_user_id=booker.id,
                occurred_at=appointment.cancelled_at,
            ))
        if appointment.invalidated_at:
            event_type = "institution_cancelled" if status == "institution_cancelled" else "no_show"
            db.session.add(AppointmentEvent(
                appointment_id=appointment.id,
                event_type=event_type,
                status_snapshot=event_type,
                message=appointment.termination_reason_text or "受检者未到检",
                occurred_at=appointment.invalidated_at,
            ))
        appointments.append(appointment)
    return group, appointments


def _seed_v12_mixed_booking_authorizations(
    *,
    group: BookingGroup,
    appointments: list[Appointment],
    users: dict[str, User],
    now: datetime,
) -> None:
    """Attach self, linked-account and health-code evidence to one group.

    The extra expired and revoked rows are deliberate negative fixtures for
    token-state handling; no raw participant token is stored in the demo.
    """

    appointments_by_user_id = {row.user_id: row for row in appointments}
    booker = users["test1"]
    linked_subject = users["test2"]
    token_subject = users["test3"]
    relation = FriendRelation.query.filter_by(
        pair_key=FriendRelation.canonical_pair_key(
            booker.id,
            linked_subject.id,
        ),
        status="active",
    ).one()
    consumed_token = BookingParticipantToken(
        token_hash=hashlib.sha256(
            b"healthdoc-demo-v12-consumed-participant-token"
        ).hexdigest(),
        booker_user_id=booker.id,
        subject_user_id=token_subject.id,
        authorization_version=token_subject.booking_authorization_version,
        expires_at=now + timedelta(days=1),
        consumed_at=group.created_at,
        created_at=group.created_at - timedelta(minutes=5),
    )
    db.session.add(consumed_token)
    db.session.flush()
    db.session.add_all([
        BookingParticipantAuthorization(
            appointment_id=appointments_by_user_id[booker.id].id,
            booker_user_id=booker.id,
            subject_user_id=booker.id,
            participant_type="self",
            authorization_version=booker.booking_authorization_version,
            created_at=group.created_at,
        ),
        BookingParticipantAuthorization(
            appointment_id=appointments_by_user_id[linked_subject.id].id,
            booker_user_id=booker.id,
            subject_user_id=linked_subject.id,
            participant_type="linked_account",
            friend_relation_id=relation.id,
            authorization_version=relation.booking_authorization_version,
            created_at=group.created_at,
        ),
        BookingParticipantAuthorization(
            appointment_id=appointments_by_user_id[token_subject.id].id,
            booker_user_id=booker.id,
            subject_user_id=token_subject.id,
            participant_type="health_code_token",
            authorization_version=consumed_token.authorization_version,
            participant_token_id=consumed_token.id,
            created_at=group.created_at,
        ),
        BookingParticipantToken(
            token_hash=hashlib.sha256(
                b"healthdoc-demo-v12-expired-participant-token"
            ).hexdigest(),
            booker_user_id=users["test2"].id,
            subject_user_id=users["test4"].id,
            authorization_version=users["test4"].booking_authorization_version,
            expires_at=now - timedelta(days=1),
            created_at=now - timedelta(days=3),
        ),
        BookingParticipantToken(
            token_hash=hashlib.sha256(
                b"healthdoc-demo-v12-revoked-participant-token"
            ).hexdigest(),
            booker_user_id=users["test4"].id,
            subject_user_id=users["test5"].id,
            authorization_version=users["test5"].booking_authorization_version,
            expires_at=now + timedelta(days=1),
            revoked_at=now - timedelta(hours=8),
            created_at=now - timedelta(days=2),
        ),
    ])


def _reference_text(indicator) -> str | None:
    if indicator.reference_low is None and indicator.reference_high is None:
        return None
    low = "" if indicator.reference_low is None else str(indicator.reference_low)
    high = "" if indicator.reference_high is None else str(indicator.reference_high)
    return f"{low}—{high} {indicator.unit}".strip()


def _create_report(
    *, appointment: Appointment, staff: User, indicators: dict, domains: dict,
    values: tuple[tuple[str, str, str, bool], ...],
    text_results: tuple[tuple[str, str, str], ...] = (),
    asset: tuple[str, str, tuple[tuple[int, int, int], ...], str] | None = None,
) -> InstitutionReport:
    report = InstitutionReport(
        institution_id=appointment.institution_id,
        created_by_user_id=staff.id,
        created_by_username_snapshot=staff.username,
        subject_name_snapshot=appointment.user_name_snapshot,
        subject_health_id=appointment.user_health_id_snapshot,
        exam_date=appointment.appointment_date,
        package_id=appointment.package_id,
        package_version_id=appointment.package_version_id,
        appointment_id=appointment.id,
        matched_user_id=appointment.user_id,
        status="published",
        upload_doctor_name=DEMO_UPLOAD_DOCTOR_NAME,
        review_doctor_name=DEMO_REVIEW_DOCTOR_NAME,
        submitted_for_review_at=appointment.attended_at + timedelta(hours=5, minutes=30),
        reviewed_by_user_id=staff.id,
        reviewed_by_username_snapshot=staff.username,
        reviewed_at=appointment.attended_at + timedelta(hours=6),
        locked_at=appointment.attended_at + timedelta(hours=6),
        submitted_at=appointment.fulfilled_at,
        published_at=appointment.fulfilled_at,
        created_at=appointment.attended_at,
    )
    db.session.add(report)
    db.session.flush()
    for code, value, domain_code, abnormal in values:
        definition = indicators[code]
        result_status = evaluate_result_status(
            definition,
            value,
            subject=appointment.user,
            on_date=appointment.appointment_date,
            abnormal_flag="high" if abnormal else None,
        )
        report.indicators.append(ReportIndicator(
            indicator_dict_id=definition.id,
            value=value,
            is_abnormal=abnormal,
            result_status=result_status,
            input_source="manual",
            display_domain_id=domains[domain_code].id,
            original_name=definition.name,
            original_value=value,
            original_unit=definition.unit,
            normalized_unit=definition.unit,
            reference_text=_reference_text(definition),
            method_snapshot="机构常规检测",
            abnormal_flag="high" if abnormal else "normal",
            mapping_confidence=Decimal("1.0000"),
            mapping_status="confirmed",
        ))
    for order, (domain_code, title, body) in enumerate(text_results):
        report.text_results.append(ReportTextResult(
            health_domain_id=domains[domain_code].id,
            title=title,
            body=body,
            source_snapshot="机构医生审核结论",
            sort_order=order,
            created_by_user_id=staff.id,
        ))
    if asset:
        domain_code, title, palette, annotation = asset
        initial_asset_code = {
            "metabolic": "US_THYROID",
            "digestive": "US_ABDOMEN",
            "respiratory": "SPIROMETRY",
        }.get(domain_code)
        asset_type = (
            ReportAssetType.query.filter_by(code=initial_asset_code, is_active=True).first()
            if initial_asset_code else None
        )
        key = f"health-assets/demo-v8/report-{report.id}-{domain_code}.png"
        if asset_type:
            raw, width, height = _load_curated_media(
                initial_asset_code, Path(current_app.config["UPLOAD_DIR"]) / key,
            )
        else:
            raw, width, height = _load_demo_media(
                key, Path(current_app.config["UPLOAD_DIR"]) / key, palette,
            )
        row = ReportAsset(
            report_id=report.id,
            health_domain_id=asset_type.health_domain_id if asset_type else domains[domain_code].id,
            asset_type_id=asset_type.id if asset_type else None,
            modality=asset_type.modality if asset_type else "image",
            title=asset_type.name if asset_type else title,
            storage_key=key,
            mime_type="image/png",
            byte_size=len(raw),
            width=width,
            height=height,
            sha256=hashlib.sha256(raw).hexdigest(),
            annotation_text=ASSET_FINDINGS.get(asset_type.code, annotation) if asset_type else annotation,
            sort_order=0,
            uploaded_by_user_id=staff.id,
        )
        db.session.add(row)
        db.session.flush()
        db.session.add(ReportAssetAnnotation(
            report_asset_id=row.id,
            annotation_type="text",
            text=ASSET_FINDINGS.get(asset_type.code, annotation) if asset_type else annotation,
            created_by_user_id=staff.id,
        ))
    return report


def _create_imported_historical_report(
    *, user: User, institution: Institution, package: Package, staff: User,
    exam_date: date, indicators: dict, domains: dict,
    values: tuple[tuple[str, str, str, bool], ...], title: str, body: str,
) -> InstitutionReport:
    """Create a legacy paper-result archive that predates platform booking.

    This is intentionally the only demo path with ``appointment_id=None`` and
    makes the distinction visible without weakening the live report workflow.
    """
    version = _package_version(package)
    published_at = _utc(exam_date + timedelta(days=2), 15)
    report = InstitutionReport(
        institution_id=institution.id,
        created_by_user_id=staff.id,
        created_by_username_snapshot=staff.username,
        subject_name_snapshot=user.real_name,
        subject_health_id=user.health_id,
        exam_date=exam_date,
        package_id=package.id,
        package_version_id=version.id,
        appointment_id=None,
        matched_user_id=user.id,
        status="published",
        upload_doctor_name=DEMO_UPLOAD_DOCTOR_NAME,
        review_doctor_name=DEMO_REVIEW_DOCTOR_NAME,
        submitted_for_review_at=published_at - timedelta(hours=1, minutes=30),
        reviewed_by_user_id=staff.id,
        reviewed_by_username_snapshot=staff.username,
        reviewed_at=published_at - timedelta(hours=1),
        ocr_diagnostics={"import_kind": "historical_paper_archive", "raw_text_retained": False},
        locked_at=published_at - timedelta(hours=1),
        submitted_at=published_at,
        published_at=published_at,
        created_at=published_at - timedelta(hours=2),
    )
    db.session.add(report)
    db.session.flush()
    for code, value, domain_code, abnormal in values:
        definition = indicators[code]
        result_status = evaluate_result_status(
            definition,
            value,
            subject=user,
            on_date=exam_date,
            abnormal_flag="high" if abnormal else None,
        )
        report.indicators.append(ReportIndicator(
            indicator_dict_id=definition.id,
            value=value,
            is_abnormal=abnormal,
            result_status=result_status,
            input_source="manual",
            display_domain_id=domains[domain_code].id,
            original_name=definition.name,
            original_value=value,
            original_unit=definition.unit,
            normalized_unit=definition.unit,
            reference_text=_reference_text(definition),
            method_snapshot="历史纸质结果人工归档",
            abnormal_flag="high" if abnormal else "normal",
            mapping_confidence=Decimal("1.0000"),
            mapping_status="confirmed",
        ))
    report.text_results.append(ReportTextResult(
        health_domain_id=domains[values[0][2]].id,
        title=title,
        body=body,
        source_snapshot="历史纸质报告人工归档",
        sort_order=0,
        created_by_user_id=staff.id,
    ))
    return report


def _create_review_workflow_report(
    *, appointment: Appointment, staff: User, status: str, now: datetime,
) -> InstitutionReport:
    report = InstitutionReport(
        institution_id=appointment.institution_id,
        created_by_user_id=staff.id,
        created_by_username_snapshot=staff.username,
        subject_name_snapshot=appointment.user_name_snapshot,
        subject_health_id=appointment.user_health_id_snapshot,
        exam_date=appointment.appointment_date,
        package_id=appointment.package_id,
        package_version_id=appointment.package_version_id,
        appointment_id=appointment.id,
        matched_user_id=appointment.user_id,
        status=status,
        upload_doctor_name=(
            DEMO_UPLOAD_DOCTOR_NAME
            if status == "pending_review"
            else None
        ),
        submitted_for_review_at=now - timedelta(hours=2) if status == "pending_review" else None,
        created_at=appointment.attended_at or now - timedelta(hours=4),
    )
    db.session.add(report)
    db.session.flush()
    domain_links = list(appointment.package_version.domains) if appointment.package_version else []
    for order, link in enumerate(domain_links):
        if status == "draft" and order > 0:
            break
        report.text_results.append(ReportTextResult(
            health_domain_id=link.health_domain_id,
            title=f"{link.domain.name}检查结论",
            body=(
                "初步结果已完成录入，等待复核医生结合原始检查资料确认。"
                if status == "pending_review"
                else "草稿结论，尚待上传医生继续完善。"
            ),
            source_snapshot="机构医生初步结论",
            sort_order=order,
            created_by_user_id=staff.id,
        ))
    return report


def _add_demo_report_asset(report, staff, domain, sequence):
    # These twelve comprehensive-report assets complement the three legacy
    # direction-specific assets (thyroid, abdomen and chest) so the complete
    # v8 set has the exact acceptance distribution.
    slot_plan = (
        ("US_ABDOMEN", "ECHO_HEART"),
        ("BLOOD_MICROSCOPY",),
        ("ECG_12",),
        ("ECG_12",),
        ("CHEST_IMAGE",),
        ("US_ABDOMEN",),
        ("BLOOD_MICROSCOPY",),
        ("ECG_12",),
        ("BLOOD_MICROSCOPY",),
        ("CHEST_IMAGE",),
        ("ECG_12",),
        (),
    )
    if sequence >= len(slot_plan):
        return
    allowed_domain_ids = {
        row.health_domain_id
        for row in report.package_version.domains
    }
    compatible_codes = []
    for asset_code in slot_plan[sequence]:
        asset_type = ReportAssetType.query.filter_by(code=asset_code, is_active=True).first()
        if asset_type is None:
            raise DemoResetSafetyError(f"缺少附件槽位定义：{asset_code}")
        if asset_type.health_domain_id in allowed_domain_ids:
            compatible_codes.append(asset_code)
    if not compatible_codes and slot_plan[sequence]:
        fallback = ReportAssetType.query.filter(
            ReportAssetType.is_active.is_(True),
            ReportAssetType.health_domain_id.in_(allowed_domain_ids),
            ReportAssetType.code.in_(tuple(CURATED_ASSET_SOURCES)),
        ).order_by(
            ReportAssetType.sort_order,
            ReportAssetType.id,
        ).first()
        if fallback is not None:
            compatible_codes.append(fallback.code)
    for asset_order, asset_code in enumerate(compatible_codes):
        asset_type = ReportAssetType.query.filter_by(
            code=asset_code,
            is_active=True,
        ).one()
        key = (
            f"health-assets/demo-v8/report-{report.id}-{domain.code}.png"
            if asset_order == 0
            else f"health-assets/demo-v8/report-{report.id}-{asset_code.lower()}.png"
        )
        source_sequence = ReportAsset.query.filter_by(asset_type_id=asset_type.id).count()
        raw, width, height = _load_curated_media(
            asset_code, Path(current_app.config["UPLOAD_DIR"]) / key, source_sequence,
        )
        row = ReportAsset(
            report_id=report.id,
            health_domain_id=asset_type.health_domain_id,
            asset_type_id=asset_type.id,
            modality=asset_type.modality,
            title=asset_type.name,
            storage_key=key,
            mime_type="image/png",
            byte_size=len(raw),
            width=width,
            height=height,
            sha256=hashlib.sha256(raw).hexdigest(),
            annotation_text=ASSET_FINDINGS.get(asset_type.code, f"{asset_type.name}检查所见未见明显异常。"),
            sort_order=asset_order,
            uploaded_by_user_id=staff.id,
        )
        db.session.add(row); db.session.flush()
        db.session.add(ReportAssetAnnotation(
            report_asset_id=row.id,
            annotation_type="text",
            text=row.annotation_text,
            created_by_user_id=staff.id,
        ))


SHARED_ARCHIVE_DOMAIN_PROFILES = {
    "basic": (
        ("HEIGHT", "168", None, False),
        ("WEIGHT", "63.5", "69.7", False),
        ("BMI", "22.5", "24.7", True),
        ("WAIST", "78", "89", False),
        ("TEMP", "36.6", None, False),
    ),
    "cardio": (
        ("HR", "76", None, False),
        ("SBP", "128", None, False),
        ("DBP", "82", None, False),
        ("TC", "4.7", None, False),
        ("LDL", "2.8", "3.65", True),
    ),
    "metabolic": (
        ("FBG", "5.2", "6.2", True),
        ("HBA1C", "5.5", None, False),
        ("INS", "8.5", None, False),
        ("TSH", "2.4", None, False),
        ("FT4", "16.2", None, False),
    ),
    "digestive": (
        ("ALT", "28", "48", True),
        ("AST", "24", None, False),
        ("GGT", "27", None, False),
        ("TBIL", "13", None, False),
        ("ALB", "44", None, False),
    ),
    "respiratory": (
        ("FENO", "18", "32", True),
        ("SPO2", "97", None, False),
        ("FVC", "3.8", None, False),
        ("FEV1", "3.1", None, False),
        ("FEV1_FVC", "82", None, False),
    ),
    "renal": (
        ("UA", "345", "450", True),
        ("CREA", "78", None, False),
        ("BUN", "5.2", None, False),
        ("EGFR", "96", None, False),
        ("CYSC", "0.83", None, False),
    ),
    "hematology": (
        ("CRP", "2.1", "12", True),
        ("WBC", "6.4", None, False),
        ("RBC", "4.7", None, False),
        ("HGB", "145", None, False),
        ("PLT", "235", None, False),
    ),
    "other": (
        ("IOP_L", "16", "23", True),
        ("IOP_R", "16", None, False),
        ("VA_L", "1.0", None, False),
        ("VA_R", "1.0", None, False),
        ("BMD_T", "-0.5", None, False),
    ),
}


def _expand_v8_demo_data(users, institutions, packages, indicators, domains, today, now):
    """Reach the fixed v8 demonstration scale with deterministic stories."""
    testing = current_app.config.get("TESTING", False)
    report_target = 15 if testing else 50
    asset_target = 4 if testing else 15
    group_target = BookingGroup.query.count() if testing else 40
    appointment_target = Appointment.query.count() if testing else 56
    measurement_target = max(SelfMeasurement.query.count(), 70) if testing else 120
    domain_profiles = SHARED_ARCHIVE_DOMAIN_PROFILES
    indicator_domain_ids = {
        definition.id: {
            link.health_domain_id
            for link in definition.domain_links
        }
        for definition in indicators.values()
    }

    def report_values(version, sequence_number):
        allowed_codes = [
            row.domain.code
            for row in sorted(version.domains, key=lambda item: item.sort_order)
            if row.domain and row.domain.code in domain_profiles
        ]
        if not allowed_codes:
            raise DemoResetSafetyError(f"套餐版本 {version.id} 没有可生成的健康方向")
        abnormal_domain = (
            allowed_codes[sequence_number % len(allowed_codes)]
            if sequence_number % 3 == 0
            else None
        )
        result = []
        used_codes = set()
        for domain_code in allowed_codes:
            for indicator_code, normal_value, abnormal_value, determines_abnormal in domain_profiles[domain_code]:
                if indicator_code in used_codes:
                    continue
                definition = indicators.get(indicator_code)
                if definition is None:
                    raise DemoResetSafetyError(f"缺少共享档案指标定义：{indicator_code}")
                if domains[domain_code].id not in indicator_domain_ids[definition.id]:
                    raise DemoResetSafetyError(
                        f"指标 {indicator_code} 未配置到健康方向 {domain_code}"
                    )
                use_variant = domain_code == abnormal_domain and abnormal_value is not None
                use_abnormal = use_variant and determines_abnormal
                result.append((
                    indicator_code,
                    abnormal_value if use_variant else normal_value,
                    domain_code,
                    use_abnormal,
                ))
                used_codes.add(indicator_code)
        return tuple(result)
    staff_by_branch = {
        index: users[f"institution{index}_staff1"]
        for index in range(1, len(institutions) + 1)
    }
    package_by_branch = {index: sorted(branch.packages, key=lambda item: item.id)[0] for index, branch in enumerate(institutions, start=1)}
    if testing:
        report_distribution = {"澄心健康管理中心": 5, "衡康代谢与慢病管理中心": 3,
                               "云川影像与呼吸体检中心": 3, "安沐女性与家庭健康中心": 2,
                               "仁序职业健康与综合体检中心": 2}
        group_distribution = {
            organization.name: BookingGroup.query.join(Institution).filter(
                Institution.organization_id == organization.id).count()
            for organization in Organization.query.all()
        }
    else:
        report_distribution = {"澄心健康管理中心": 18, "衡康代谢与慢病管理中心": 12,
                               "云川影像与呼吸体检中心": 9, "安沐女性与家庭健康中心": 7,
                               "仁序职业健康与综合体检中心": 4}
        group_distribution = {"澄心健康管理中心": 14, "衡康代谢与慢病管理中心": 10,
                              "云川影像与呼吸体检中心": 8, "安沐女性与家庭健康中心": 5,
                              "仁序职业健康与综合体检中心": 3}

    sequence = 0
    organizations = Organization.query.order_by(Organization.id).all()
    for organization in organizations:
        target = report_distribution[organization.name]
        branches = sorted(organization.branches, key=lambda item: item.id)
        while InstitutionReport.query.join(Institution).filter(Institution.organization_id == organization.id).count() < target:
            branch = branches[sequence % len(branches)]
            branch_index = institutions.index(branch) + 1
            user = users[
                DEMO_PROFILE_USERNAMES[sequence % len(DEMO_PROFILE_USERNAMES)]
            ]
            package = package_by_branch[branch_index]
            version = _package_version(package)
            domain = sorted(version.domains, key=lambda item: item.sort_order)[0].domain
            report = _create_imported_historical_report(
                user=user, institution=branch, package=package, staff=staff_by_branch[branch_index],
                exam_date=today - timedelta(days=760 + sequence * 9), indicators=indicators, domains=domains,
                values=report_values(version, sequence),
                title=f"{branch.branch_name}历史体检摘要",
                body="该报告由源分院完成复核与归档，同机构其他分院可按权限查阅。",
            )
            if ReportAsset.query.count() < asset_target:
                _add_demo_report_asset(report, staff_by_branch[branch_index], domain, sequence)
            sequence += 1

    group_sequence = 0
    for organization in organizations:
        target = group_distribution[organization.name]
        branches = sorted(organization.branches, key=lambda item: item.id)
        while BookingGroup.query.join(Institution).filter(Institution.organization_id == organization.id).count() < target:
            groups_left = max(group_target - BookingGroup.query.count(), 1)
            appointments_left = appointment_target - Appointment.query.count()
            party_size = 2 if appointments_left > groups_left else 1
            branch = branches[group_sequence % len(branches)]
            branch_index = institutions.index(branch) + 1
            participant_start = group_sequence % len(DEMO_PROFILE_USERNAMES)
            participants = [
                users[
                    DEMO_PROFILE_USERNAMES[
                        (participant_start + offset) % len(DEMO_PROFILE_USERNAMES)
                    ]
                ]
                for offset in range(party_size)
            ]
            _create_booking_group(
                booker=participants[0], participants=participants, institution=branch,
                package=package_by_branch[branch_index], appointment_date=today - timedelta(days=260 + group_sequence),
                status="cancelled" if group_sequence % 2 == 0 else "no_show",
                created_at=now - timedelta(days=300 + group_sequence),
            )
            group_sequence += 1

    measurement_sequence = 0
    measurement_indicators = ("WEIGHT", "HR", "FBG", "SPO2", "TEMP")
    while SelfMeasurement.query.count() < measurement_target:
        username = DEMO_PROFILE_USERNAMES[
            measurement_sequence % len(DEMO_PROFILE_USERNAMES)
        ]
        code = measurement_indicators[measurement_sequence % len(measurement_indicators)]
        # v12 acceptance fixtures: test4 has neither latest height nor weight,
        # while test5 has height only. Keep filler rows from erasing those
        # explicit health-code proxy-booking boundary cases.
        if code == "WEIGHT" and username in {"test4", "test5"}:
            code = "HR"
        base = {"WEIGHT": 65, "HR": 72, "FBG": 5.2, "SPO2": 98, "TEMP": 36.6}[code]
        _add_measurement(
            users[username], indicators[code], base + (measurement_sequence % 4) * 0.1,
            _utc(today - timedelta(days=220 + measurement_sequence), 7 + measurement_sequence % 4),
        )
        measurement_sequence += 1

    # Seed a few realistic audit rows without exposing report contents to the
    # platform administrator. Every row represents an actual sibling branch.
    audit_targets = {"澄心健康管理中心": 4, "衡康代谢与慢病管理中心": 3,
                     "云川影像与呼吸体检中心": 3, "安沐女性与家庭健康中心": 2}
    for organization in Organization.query.order_by(Organization.id).all():
        branches = [branch for branch in organization.branches if branch.is_active]
        if len(branches) < 2:
            continue
        reports = InstitutionReport.query.filter(InstitutionReport.institution_id.in_([branch.id for branch in branches]), InstitutionReport.status == "published").order_by(InstitutionReport.id).all()
        for index in range(audit_targets.get(organization.name, 1)):
            source = branches[index % len(branches)]
            actor_branch = branches[(index + 1) % len(branches)]
            report = next((item for item in reports if item.institution_id == source.id), reports[index % len(reports)] if reports else None)
            actor = User.query.filter_by(managed_institution_id=actor_branch.id, role="institution_admin").first()
            if not report or not actor:
                continue
            db.session.add(ReportAccessLog(
                actor_user_id=actor.id,
                actor_institution_id=actor_branch.id,
                report_id=report.id,
                source_institution_id=report.institution_id,
                access_type="detail",
                accessed_at=now - timedelta(days=organization.id + index),
            ))


def _demo_indicator_value(definition, sequence, *, value_sequence=None):
    value_sequence = sequence if value_sequence is None else value_sequence
    if definition.value_type == "text":
        if definition.code == "HEARING":
            return ("未见明显异常", "normal")
        if definition.code == "U_PRO" and sequence == 3:
            return ("弱阳性", "positive")
        if definition.code == "FOBT" and sequence == 19:
            return ("阳性", "positive")
        return ("阴性", "negative")
    realistic_value = demo_realistic_value(definition.code, value_sequence)
    if realistic_value is not None:
        return (realistic_value, None)
    low = Decimal(str(definition.reference_low)) if definition.reference_low is not None else None
    high = Decimal(str(definition.reference_high)) if definition.reference_high is not None else None
    story_phase = (
        Decimal("0.62"), Decimal("0.60"), Decimal("0.58"), Decimal("0.56"),
        Decimal("0.54"), Decimal("0.52"), Decimal("0.50"), Decimal("0.48"),
        Decimal("0.46"), Decimal("0.45"), Decimal("0.50"), Decimal("0.59"),
        Decimal("0.55"), Decimal("0.51"), Decimal("0.48"), Decimal("0.46"),
    )[value_sequence % 16]
    if low is not None and high is not None:
        value = low + (high - low) * story_phase
    elif low is not None:
        value = low * (Decimal("1.12") + story_phase / Decimal("5"))
    elif high is not None:
        value = high * (Decimal("0.52") + story_phase / Decimal("4"))
    else:
        value = Decimal("10") + Decimal(definition.id % 17) + story_phase
    normalized = value.quantize(Decimal("0.01"))
    return (format(normalized, "f").rstrip("0").rstrip("."), None)


TEST1_STORY_PLAN = (
    # days ago, institution index, package name, report kind
    (1460, 1, "都市年度综合体检", "comprehensive_exam"),
    (1390, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (1320, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (1280, 1, "都市年度综合体检", "comprehensive_exam"),
    (1210, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (1140, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (1100, 1, "都市年度综合体检", "comprehensive_exam"),
    (1030, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (960, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (920, 1, "都市年度综合体检", "comprehensive_exam"),
    (840, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (780, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (740, 1, "都市年度综合体检", "comprehensive_exam"),
    (700, 3, "呼吸与肺功能专项", "targeted_follow_up"),
    (650, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (620, 3, "心电与循环影像专项", "targeted_follow_up"),
    (590, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (560, 1, "都市年度综合体检", "comprehensive_exam"),
    (470, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (410, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (380, 1, "都市年度综合体检", "comprehensive_exam"),
    (280, 1, "家庭长辈健康评估", "targeted_follow_up"),
    (240, 3, "呼吸与肺功能专项", "targeted_follow_up"),
    (190, 1, "都市年度综合体检", "comprehensive_exam"),
    (160, 3, "心电与循环影像专项", "targeted_follow_up"),
    (120, 2, "肝胆代谢联合评估", "targeted_follow_up"),
    (35, 3, "心电与循环影像专项", "targeted_follow_up"),
    (14, 3, "呼吸与肺功能专项", "targeted_follow_up"),
    (2, 1, "都市年度综合体检", "comprehensive_exam"),
)

TEST1_CURRENT_ABNORMAL_VALUES = {
    "SBP": "146",
    "FBG": "6.42",
    "LDL": "3.68",
}


def _expand_v10_test1(users, institutions, packages, indicators, domains, today, now):
    """Build test1's coherent four-year package-aligned adult health story."""
    if current_app.config.get("TESTING", False):
        return
    user = users["test1"]
    types_by_domain = {}
    for asset_type in ReportAssetType.query.order_by(ReportAssetType.sort_order, ReportAssetType.id).all():
        types_by_domain.setdefault(asset_type.health_domain_id, asset_type)
    for asset in ReportAsset.query.filter(ReportAsset.asset_type_id.is_(None)).all():
        matched_type = types_by_domain.get(asset.health_domain_id)
        if matched_type:
            asset.asset_type_id = matched_type.id
            asset.modality = matched_type.modality
            asset.title = matched_type.name
    ocr_sequences = {2, 6, 10, 14, 18, 22, 26}
    reports = []
    final_sequence = len(TEST1_STORY_PLAN) - 1
    for sequence, (days_ago, branch_index, package_name, report_kind) in enumerate(TEST1_STORY_PLAN):
        # Keep the newest sample in the current calendar month so the monthly
        # abnormal summary remains demonstrable after every safe demo reset.
        effective_days_ago = (
            min(days_ago, max(today.day - 1, 0))
            if sequence == final_sequence
            else days_ago
        )
        exam_date = today - timedelta(days=effective_days_ago)
        institution = institutions[branch_index - 1]
        package = packages[_package_key(branch_index, package_name)]
        staff = users[f"institution{branch_index}_staff1"]
        version = _package_version(package)
        allowed_domain_codes = {
            link.domain.code
            for link in version.domains
            if link.domain is not None
        }
        published_at = _utc(exam_date + timedelta(days=2), 16)
        report = InstitutionReport(
            institution_id=institution.id,
            created_by_user_id=staff.id,
            created_by_username_snapshot=staff.username,
            subject_name_snapshot=user.real_name,
            subject_health_id=user.health_id,
            exam_date=exam_date,
            package_id=package.id,
            package_version_id=version.id,
            matched_user_id=user.id,
            status="published",
            upload_doctor_name=DEMO_UPLOAD_DOCTOR_NAME,
            review_doctor_name=DEMO_REVIEW_DOCTOR_NAME,
            submitted_for_review_at=published_at - timedelta(hours=1),
            reviewed_by_user_id=staff.id,
            reviewed_by_username_snapshot=staff.username,
            reviewed_at=published_at,
            ocr_diagnostics={
                "import_kind": report_kind,
                "sequence": sequence + 1,
                "contains_ocr_rows": sequence in ocr_sequences,
            },
            locked_at=published_at - timedelta(hours=1),
            submitted_at=published_at,
            published_at=published_at,
            created_at=published_at - timedelta(hours=3),
        )
        db.session.add(report)
        db.session.flush()
        definitions = [
            definition
            for definition in sorted(indicators.values(), key=lambda item: item.id)
            if any(
                link.domain and link.domain.code in allowed_domain_codes
                for link in definition.domain_links
            )
        ]
        story_phase = (sequence * 15 + final_sequence // 2) // final_sequence
        for definition in definitions:
            if sequence == final_sequence and definition.code in TEST1_CURRENT_ABNORMAL_VALUES:
                value = TEST1_CURRENT_ABNORMAL_VALUES[definition.code]
                explicit_status = None
            else:
                value, explicit_status = _demo_indicator_value(
                    definition, sequence, value_sequence=story_phase,
                )
            status = explicit_status or evaluate_result_status(
                definition,
                value,
                subject=user,
                on_date=exam_date,
            )
            domain = next(
                (
                    link.domain
                    for link in sorted(
                        definition.domain_links,
                        key=lambda link: (not link.is_primary, link.sort_order, link.id),
                    )
                    if link.domain and link.domain.code in allowed_domain_codes
                ),
                None,
            )
            if domain is None:
                raise RuntimeError(
                    f"套餐 {package.name} 未给指标 {definition.code} 提供可用健康方向"
                )
            report.indicators.append(ReportIndicator(
                indicator_dict_id=definition.id,
                value=value,
                is_abnormal=status in {"high", "low", "positive", "abnormal"},
                result_status=status,
                input_source="ocr" if sequence in ocr_sequences and definition.id % 3 == 0 else "manual",
                display_domain_id=domain.id,
                original_name=(definition.aliases or [definition.name])[-1],
                original_value=value,
                original_unit=definition.unit,
                normalized_unit=definition.unit,
                reference_text=_reference_text(definition),
                method_snapshot="机构常规检测",
                abnormal_flag={"high": "H", "low": "L", "positive": "+"}.get(status),
                mapping_confidence=Decimal("0.9900") if sequence in ocr_sequences else Decimal("1.0000"),
                mapping_status="confirmed",
            ))
        reports.append((report, staff))

    asset_types = {row.code: row for row in ReportAssetType.query.all()}
    attachment_reports = (
        (
            next(item for item in reversed(reports) if item[0].package.name == "呼吸与肺功能专项"),
            ("SPIROMETRY", "CHEST_IMAGE"),
        ),
        (
            next(item for item in reversed(reports) if item[0].package.name == "心电与循环影像专项"),
            ("ECG_12", "ECHO_HEART"),
        ),
    )
    for sequence, ((report, staff), slot_codes) in enumerate(attachment_reports):
        allowed_domain_ids = {link.health_domain_id for link in report.package_version.domains}
        for order, code in enumerate(slot_codes):
            asset_type = asset_types.get(code)
            if asset_type is None:
                continue
            if asset_type.health_domain_id not in allowed_domain_ids:
                raise RuntimeError(f"附件槽位 {code} 不属于套餐 {report.package.name}")
            key = f"health-assets/demo-v10/report-{report.id}-{code.lower()}.png"
            raw, width, height = _load_curated_media(
                code, Path(current_app.config["UPLOAD_DIR"]) / key, sequence,
            )
            db.session.add(ReportAsset(
                report_id=report.id,
                health_domain_id=asset_type.health_domain_id,
                asset_type_id=asset_type.id,
                modality=asset_type.modality,
                title=asset_type.name,
                storage_key=key,
                mime_type="image/png",
                byte_size=len(raw),
                width=width,
                height=height,
                sha256=hashlib.sha256(raw).hexdigest(),
                annotation_text=ASSET_FINDINGS.get(asset_type.code, f"{asset_type.name}检查所见未见明显异常。"),
                sort_order=order,
                uploaded_by_user_id=staff.id,
            ))

    db.session.add_all([
        UserNotification(
            user_id=user.id,
            event_type="report_published",
            idempotency_key="demo-v10-report-published",
            title="体检报告已交付",
            body="最新一份成人综合体检报告已交付，可查看检查结果、影像附件和机构结论。",
            action_url=f"/health-data/hd-i-{reports[-1][0].id:x}",
            payload={"report_id": reports[-1][0].id},
            created_at=now - timedelta(days=2),
        ),
        UserNotification(
            user_id=user.id,
            event_type="appointment_institution_cancelled",
            idempotency_key="demo-v10-institution-cancelled",
            title="很抱歉，机构取消了本次预约",
            body="机构因设备突发故障取消预约，请查看平台提供的兄弟分院解决方案。",
            action_url="/appointments",
            payload={"reason": "设备突发故障"},
            created_at=now - timedelta(days=1),
        ),
    ])


def _normalize_report_business_records():
    """Make every represented report domain complete and clinically readable."""

    def text_result_factory(report, domain, title, body, order):
        return ReportTextResult(
            health_domain_id=domain.id,
            title=title,
            body=body,
            source_snapshot="机构医生审核结论",
            sort_order=order,
            created_by_user_id=report.created_by_user_id,
        )

    def synchronize_asset_annotation(asset):
        finding = asset.annotation_text
        annotations = ReportAssetAnnotation.query.filter_by(report_asset_id=asset.id).all()
        if not annotations:
            db.session.add(ReportAssetAnnotation(
                report_asset_id=asset.id,
                annotation_type="text",
                text=finding,
                created_by_user_id=asset.uploaded_by_user_id,
            ))
            return
        for annotation in annotations:
            annotation.text = finding

    reports = InstitutionReport.query.filter_by(status="published").order_by(
        InstitutionReport.matched_user_id,
        InstitutionReport.exam_date,
        InstitutionReport.id,
    ).all()
    normalize_report_records(
        reports,
        text_result_factory=text_result_factory,
        asset_annotation_factory=synchronize_asset_annotation,
    )


def enrich_institution1_shared_archives(*, commit: bool = True) -> dict:
    """Replace sparse sibling-branch archives with package-aligned report facts."""
    staff = User.query.filter_by(username="institution1_staff1").one()
    current = staff.managed_institution
    reports = (
        InstitutionReport.query
        .join(Institution)
        .filter(
            Institution.organization_id == current.organization_id,
            InstitutionReport.institution_id != current.id,
            InstitutionReport.status == "published",
        )
        .order_by(InstitutionReport.exam_date, InstitutionReport.id)
        .all()
    )
    if len(reports) < 10:
        raise DemoResetSafetyError(
            f"institution1 共享档案数量不足：{len(reports)}"
        )
    indicators = {row.code: row for row in IndicatorDict.query.all()}
    domains = {row.code: row for row in HealthDomain.query.all()}
    indicator_domain_ids = {
        definition.id: {
            link.health_domain_id
            for link in definition.domain_links
        }
        for definition in indicators.values()
    }

    for sequence, report in enumerate(reports):
        for item in list(report.indicators):
            report.indicators.remove(item)
            db.session.delete(item)
        for item in list(report.text_results):
            report.text_results.remove(item)
            db.session.delete(item)
        db.session.flush()
        allowed_codes = [
            row.domain.code
            for row in sorted(report.package_version.domains, key=lambda item: item.sort_order)
            if row.domain and row.domain.code in SHARED_ARCHIVE_DOMAIN_PROFILES
        ]
        if not allowed_codes:
            raise DemoResetSafetyError(
                f"共享档案 {report.id} 的套餐没有可用健康方向"
            )
        abnormal_domain = (
            allowed_codes[sequence % len(allowed_codes)]
            if sequence % 3 == 0
            else None
        )
        used_codes = set()
        for domain_code in allowed_codes:
            for indicator_code, normal_value, abnormal_value, determines_abnormal in (
                SHARED_ARCHIVE_DOMAIN_PROFILES[domain_code]
            ):
                if indicator_code in used_codes:
                    continue
                definition = indicators[indicator_code]
                if domains[domain_code].id not in indicator_domain_ids[definition.id]:
                    raise DemoResetSafetyError(
                        f"指标 {indicator_code} 未配置到健康方向 {domain_code}"
                    )
                use_variant = domain_code == abnormal_domain and abnormal_value is not None
                value = abnormal_value if use_variant else normal_value
                abnormal_flag = "high" if use_variant and determines_abnormal else None
                status = evaluate_result_status(
                    definition,
                    value,
                    subject=report.owner,
                    on_date=report.exam_date,
                    abnormal_flag=abnormal_flag,
                )
                report.indicators.append(ReportIndicator(
                    indicator_dict_id=definition.id,
                    value=value,
                    is_abnormal=status in {"high", "low", "positive", "abnormal"},
                    result_status=status,
                    input_source="manual",
                    display_domain_id=domains[domain_code].id,
                    original_name=definition.name,
                    original_value=value,
                    original_unit=definition.unit,
                    normalized_unit=definition.unit,
                    reference_text=_reference_text(definition),
                    method_snapshot="机构常规检测",
                    abnormal_flag={"high": "H", "low": "L", "positive": "+"}.get(status),
                    mapping_confidence=Decimal("1.0000"),
                    mapping_status="confirmed",
                ))
                used_codes.add(indicator_code)
        # Some two-domain packages only contribute ten rows through the
        # curated story profiles above.  Preserve the established acceptance
        # quality floor by adding deterministic, package-aligned indicators
        # from the same allowed domains until the archive has at least 15.
        if len(used_codes) < 15:
            allowed_domain_ids = {
                domains[domain_code].id for domain_code in allowed_codes
            }
            for definition in sorted(indicators.values(), key=lambda row: row.id):
                if definition.code in used_codes:
                    continue
                matching_link = next(
                    (
                        link
                        for link in sorted(
                            definition.domain_links,
                            key=lambda link: (
                                not link.is_primary,
                                link.sort_order,
                                link.id,
                            ),
                        )
                        if link.health_domain_id in allowed_domain_ids
                    ),
                    None,
                )
                if matching_link is None:
                    continue
                value, explicit_status = _demo_indicator_value(
                    definition,
                    sequence,
                    value_sequence=sequence % 16,
                )
                status = explicit_status or evaluate_result_status(
                    definition,
                    value,
                    subject=report.owner,
                    on_date=report.exam_date,
                )
                report.indicators.append(ReportIndicator(
                    indicator_dict_id=definition.id,
                    value=value,
                    is_abnormal=status in {
                        "high",
                        "low",
                        "positive",
                        "abnormal",
                    },
                    result_status=status,
                    input_source="manual",
                    display_domain_id=matching_link.health_domain_id,
                    original_name=definition.name,
                    original_value=value,
                    original_unit=definition.unit,
                    normalized_unit=definition.unit,
                    reference_text=_reference_text(definition),
                    method_snapshot="机构常规检测",
                    abnormal_flag={
                        "high": "H",
                        "low": "L",
                        "positive": "+",
                    }.get(status),
                    mapping_confidence=Decimal("1.0000"),
                    mapping_status="confirmed",
                ))
                used_codes.add(definition.code)
                if len(used_codes) >= 15:
                    break
        if len(used_codes) < 15:
            raise DemoResetSafetyError(
                f"共享档案 {report.id} 的套餐内指标不足 15 项"
            )
    db.session.flush()
    _normalize_report_business_records()
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return {
        "shared_reports": len(reports),
        "minimum_indicators": min(len(report.indicators) for report in reports),
        "maximum_indicators": max(len(report.indicators) for report in reports),
        "abnormal_reports": sum(
            any(item.result_status in {"high", "low", "positive", "abnormal"} for item in report.indicators)
            for report in reports
        ),
    }


def _seed_v12_governance_workflows(users, now):
    awaiting = Appointment.query.filter_by(status="awaiting_report").order_by(
        Appointment.appointment_date,
        Appointment.id,
    ).all()
    for index, appointment in enumerate(awaiting[:2]):
        if appointment.report is None:
            _create_review_workflow_report(
                appointment=appointment,
                staff=appointment.institution.administrator,
                status="pending_review" if index == 0 else "draft",
                now=now,
            )

    states = (
        ("test1", "institution_pending"),
        ("test2", "user_confirmation"),
        ("test3", "platform_pending"),
        ("test4", "platform_processing"),
        ("test5", "resolved"),
    )
    complaint_stories = {
        "test1": {
            "category": "service",
            "content": "我按预约时间提前十分钟到店，取号后在前台等了快四十分钟。中间问了两次还要等多久，工作人员只说让我继续等，也没有说明是系统故障还是排队人数多。希望查一下当天上午的接待记录，以后遇到延迟能及时告知。",
        },
        "test2": {
            "category": "appointment",
            "content": "短信通知我上午九点到检，我八点五十分到了前台，但工作人员说系统里安排的是九点半，只能重新排队。因为这个时间差，后面的抽血和超声都往后延了。请帮忙核对短信和现场系统为什么不一致。",
            "institution_reply": "您好，我们核对了当天排班和短信发送记录。当天早班调整到九点半后，预约系统已更新，但提醒短信没有同步刷新，确实是分院操作遗漏。我们已电话向您说明，并为您下次到检备注优先登记。",
        },
        "test3": {
            "category": "report",
            "content": "报告里的低密度脂蛋白标了偏高，但页面上只有结果和参考范围，没有看到复查时间或注意事项。我按报告上的电话打了两次，一次无人接听，一次转接后断线。希望机构补充说明，并安排能看懂报告的人员回复。",
            "institution_reply": "报告医生已重新审核该项结果，并在报告结论中补充了复查建议。经查，您第一次来电处于午间交接时段，第二次转接发生中断。我们已登记回访，将由报告解读医生在工作日下午联系您。",
            "escalation_reason": "机构说已经补充建议，但我重新登录后报告页面仍是原来的内容，约好的回访时间也没有接到电话。麻烦平台帮我确认修改是否真的提交成功。",
        },
        "test4": {
            "category": "privacy",
            "content": "我在超声候检区等候时，工作人员直接念了我的全名和具体检查项目，当时旁边坐着不少人。我现场提醒后，对方才改成叫姓氏。希望机构说明目前的叫号规范，也请避免再公开完整姓名和检查内容。",
            "institution_reply": "我们查看了当日候检区记录，确认工作人员没有按规定使用编号叫号。分院已对当班人员进行停岗培训，并从当天起统一使用“排队编号＋姓氏”叫号，不再播报完整姓名和检查项目。",
            "escalation_reason": "机构已经回复会改用编号叫号，我认可这个处理方向。不过涉及个人隐私，希望平台再确认新流程已经在现场执行，而不只是口头说明。",
            "admin_reply": "平台已核对分院提交的叫号流程、培训签到和现场屏幕照片。编号叫号已启用，我们要求机构连续四周留存抽查记录，并在本次投诉关闭前完成电话回访。",
        },
        "test5": {
            "category": "service",
            "content": "做肺功能检查前，工作人员只让我对着设备吹气，没有先完整说明动作。我第一次没做成功后，对方才解释要先深吸气再持续吹，语气也比较着急。第二次讲清楚后就完成了，希望以后检查前先把动作说明白。",
            "institution_reply": "检查组负责人已电话回访并向您致歉。我们确认当班人员首次指导时漏掉了动作讲解，现已在肺功能检查前增加“讲解、示范、受检者复述”三个确认步骤，并完成当班人员复训。",
            "resolved_note": "负责人已经电话解释了检查步骤和后续整改，沟通态度也很好，我确认这个问题已经处理完成。",
        },
    }
    admin = users["demo_admin"]
    for offset, (username, status) in enumerate(states, start=1):
        user = users[username]
        appointment = Appointment.query.filter_by(
            user_id=user.id,
            status="fulfilled",
        ).order_by(Appointment.appointment_date.desc(), Appointment.id.desc()).first()
        if appointment is None:
            continue
        story = complaint_stories[username]
        created_at = now - timedelta(days=10 - offset)
        item = AppointmentComplaint(
            appointment_id=appointment.id,
            institution_id=appointment.institution_id,
            complainant_user_id=user.id,
            complainant_username_snapshot=user.username,
            category=story["category"],
            content=story["content"],
            status=status,
            created_at=created_at,
            updated_at=created_at,
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(ComplaintEvent(
            complaint_id=item.id,
            event_type="created",
            actor_user_id=user.id,
            actor_role="user",
            content=item.content,
            created_at=created_at,
        ))
        db.session.add(ComplaintMessage(
            complaint_id=item.id,
            sender_user_id=user.id,
            sender_role="user",
            content=item.content,
            created_at=created_at,
        ))
        if status in {"user_confirmation", "platform_pending", "platform_processing"}:
            reply_at = created_at + timedelta(hours=6)
            item.institution_reply = story["institution_reply"]
            item.institution_replied_by_user_id = appointment.institution.administrator.id
            item.institution_replied_at = reply_at
            db.session.add(ComplaintEvent(
                complaint_id=item.id,
                event_type="institution_replied",
                actor_user_id=appointment.institution.administrator.id,
                actor_role="institution_admin",
                content=item.institution_reply,
                created_at=reply_at,
            ))
            db.session.add(ComplaintMessage(
                complaint_id=item.id,
                sender_user_id=appointment.institution.administrator.id,
                sender_role="institution_admin",
                content=item.institution_reply,
                created_at=reply_at,
            ))
        if status in {"platform_pending", "platform_processing"}:
            escalated_at = created_at + timedelta(days=1)
            item.escalation_reason = story["escalation_reason"]
            item.escalated_at = escalated_at
            db.session.add(ComplaintEvent(
                complaint_id=item.id,
                event_type="escalated",
                actor_user_id=user.id,
                actor_role="user",
                content=item.escalation_reason,
                created_at=escalated_at,
            ))
            db.session.add(ComplaintMessage(
                complaint_id=item.id,
                sender_user_id=user.id,
                sender_role="user",
                content=item.escalation_reason,
                created_at=escalated_at,
            ))
        if status == "platform_processing":
            handled_at = created_at + timedelta(days=2)
            item.handled_by_admin_id = admin.id
            item.handled_at = handled_at
            db.session.add(ComplaintEvent(
                complaint_id=item.id,
                event_type="admin_started",
                actor_user_id=admin.id,
                actor_role="admin",
                content="平台管理员已开始核验服务记录。",
                created_at=handled_at,
            ))
            admin_reply_at = handled_at + timedelta(hours=4)
            item.admin_reply = story["admin_reply"]
            db.session.add(ComplaintEvent(
                complaint_id=item.id,
                event_type="admin_replied",
                actor_user_id=admin.id,
                actor_role="admin",
                content=item.admin_reply,
                created_at=admin_reply_at,
            ))
            db.session.add(ComplaintMessage(
                complaint_id=item.id,
                sender_user_id=admin.id,
                sender_role="admin",
                content=item.admin_reply,
                created_at=admin_reply_at,
            ))
        if status == "resolved":
            item.institution_reply = story["institution_reply"]
            item.institution_replied_by_user_id = appointment.institution.administrator.id
            item.institution_replied_at = created_at + timedelta(hours=4)
            item.resolved_at = created_at + timedelta(days=1)
            db.session.add(ComplaintEvent(
                complaint_id=item.id,
                event_type="institution_replied",
                actor_user_id=appointment.institution.administrator.id,
                actor_role="institution_admin",
                content=item.institution_reply,
                created_at=item.institution_replied_at,
            ))
            db.session.add(ComplaintMessage(
                complaint_id=item.id,
                sender_user_id=appointment.institution.administrator.id,
                sender_role="institution_admin",
                content=item.institution_reply,
                created_at=item.institution_replied_at,
            ))
            db.session.add(ComplaintEvent(
                complaint_id=item.id,
                event_type="user_confirmed",
                actor_user_id=user.id,
                actor_role="user",
                content=story["resolved_note"],
                created_at=item.resolved_at,
            ))

    comments = {
        row.user.username: row
        for row in Comment.query.order_by(Comment.id).all()
        if row.user and row.user.username in {"test3", "test4", "test5"}
    }
    test5_comment = comments.get("test5")
    if test5_comment:
        test5_comment.hidden_reason = "演示恶意灌水治理流程"
        test5_comment.moderated_by_user_id = admin.id
        test5_comment.moderated_at = now - timedelta(days=2)
        sanction = CommentSanction(
            user_id=users["test5"].id,
            source_comment_id=test5_comment.id,
            reason="演示：短期重复灌水言论",
            duration_days=7,
            status="active",
            starts_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=5),
            created_by_admin_id=admin.id,
            created_at=now - timedelta(days=2),
        )
        db.session.add(sanction)
        db.session.flush()
        db.session.add(CommentAppeal(
            sanction_id=sanction.id,
            user_id=users["test5"].id,
            content="演示申诉：已理解社区规范，请平台复核。",
            status="pending",
            submitted_at=now - timedelta(days=1),
        ))

    test4_comment = comments.get("test4")
    if test4_comment:
        sanction = CommentSanction(
            user_id=users["test4"].id,
            source_comment_id=test4_comment.id,
            reason="演示：疑似营销水军内容",
            duration_days=30,
            status="active",
            starts_at=now - timedelta(days=5),
            expires_at=now + timedelta(days=25),
            created_by_admin_id=admin.id,
            created_at=now - timedelta(days=5),
        )
        db.session.add(sanction)
        db.session.flush()
        db.session.add(CommentAppeal(
            sanction_id=sanction.id,
            user_id=users["test4"].id,
            content="演示申诉：请求重新核验评价来源。",
            status="rejected",
            review_note="核验后维持原治理决定。",
            reviewed_by_admin_id=admin.id,
            submitted_at=now - timedelta(days=4),
            reviewed_at=now - timedelta(days=3),
        ))

    test3_comment = comments.get("test3")
    if test3_comment:
        test3_comment.hidden_reason = "演示：包含需人工复核的不当引导内容"
        test3_comment.moderated_by_user_id = admin.id
        test3_comment.moderated_at = now - timedelta(days=12)
        sanction = CommentSanction(
            user_id=users["test3"].id,
            source_comment_id=test3_comment.id,
            reason="演示：严重不当言论，初始永久禁言",
            duration_days=None,
            status="lifted",
            starts_at=now - timedelta(days=12),
            expires_at=None,
            created_by_admin_id=admin.id,
            lifted_by_admin_id=admin.id,
            lifted_at=now - timedelta(days=9),
            lift_reason="申诉复核通过，解除永久禁言",
            created_at=now - timedelta(days=12),
        )
        db.session.add(sanction)
        db.session.flush()
        db.session.add(CommentAppeal(
            sanction_id=sanction.id,
            user_id=users["test3"].id,
            content="演示申诉：原内容表述不当，已补充事实材料并承诺遵守规范。",
            status="approved",
            review_note="补充材料成立，批准申诉并解除禁言。",
            reviewed_by_admin_id=admin.id,
            submitted_at=now - timedelta(days=11),
            reviewed_at=now - timedelta(days=9),
        ))


def _seed_waitlists(users, institutions, packages, today, now):
    full_day = today + timedelta(days=14)
    db.session.add(AppointmentCapacitySlot(
        institution_id=institutions[1].id,
        appointment_date=full_day,
        capacity=1,
        revision=1,
        updated_at=now,
    ))
    active = WaitlistSubscription(
        subscriber_user_id=users["test1"].id,
        institution_id=institutions[1].id,
        package_id=packages[_package_key(2, "慢病风险综合评估")].id,
        package_version_id=_package_version(packages[_package_key(2, "慢病风险综合评估")]).id,
        appointment_date=full_day,
        party_size=2,
        notification_email=users["test1"].email,
        status="active",
        created_at=now - timedelta(days=2),
    )
    db.session.add(active)
    db.session.flush()
    for participant in (users["test1"], users["test3"]):
        db.session.add(WaitlistSubscriptionParticipant(
            subscription_id=active.id,
            subject_user_id=participant.id,
            name_snapshot=participant.real_name,
            health_id_snapshot=participant.health_id,
            booking_authorized_at=now - timedelta(days=10),
        ))

    notified_day = today + timedelta(days=16)
    db.session.add(AppointmentCapacitySlot(
        institution_id=institutions[2].id,
        appointment_date=notified_day,
        capacity=3,
        revision=2,
        updated_at=now - timedelta(hours=4),
    ))
    notified = WaitlistSubscription(
        subscriber_user_id=users["test4"].id,
        institution_id=institutions[2].id,
        package_id=packages[_package_key(3, "职场综合体检")].id,
        package_version_id=_package_version(packages[_package_key(3, "职场综合体检")]).id,
        appointment_date=notified_day,
        party_size=1,
        notification_email=users["test4"].email,
        status="active",
        last_satisfied_revision=2,
        created_at=now - timedelta(days=3),
    )
    db.session.add(notified)
    db.session.flush()
    db.session.add(WaitlistSubscriptionParticipant(
        subscription_id=notified.id,
        subject_user_id=users["test4"].id,
        name_snapshot=users["test4"].real_name,
        health_id_snapshot=users["test4"].health_id,
        booking_authorized_at=now - timedelta(days=3),
    ))
    db.session.add(AvailabilityNotificationEvent(
        subscription_id=notified.id,
        capacity_revision=2,
        remaining_snapshot=2,
        created_at=now - timedelta(hours=4),
    ))
    outbox = NotificationOutbox(
        event_type="waitlist_available",
        idempotency_key=f"demo-v8-waitlist-{notified.id}-revision-2",
        recipient=users["test4"].email,
        payload={
            "message": "预约日期已有空位，请登录平台重新确认；本提醒不代表预约成功，也不会保留名额。",
            "institution": institutions[2].name,
            "appointment_date": notified_day.isoformat(),
            "party_size": 1,
        },
        status="sent",
        attempts=1,
        next_attempt_at=now - timedelta(hours=4),
        created_at=now - timedelta(hours=4),
        sent_at=now - timedelta(hours=4),
    )
    db.session.add(outbox)
    db.session.flush()
    db.session.add(NotificationDelivery(
        outbox_id=outbox.id,
        success=True,
        provider_message_id="demo-v8-local-delivery",
        attempted_at=now - timedelta(hours=4),
    ))

    invalid = WaitlistSubscription(
        subscriber_user_id=users["test5"].id,
        institution_id=institutions[0].id,
        package_id=packages[_package_key(1, "家庭长辈健康评估")].id,
        package_version_id=_package_version(packages[_package_key(1, "家庭长辈健康评估")]).id,
        appointment_date=today + timedelta(days=19),
        party_size=1,
        notification_email=users["test5"].email,
        status="invalid",
        created_at=now - timedelta(days=5),
        closed_at=now - timedelta(days=1),
    )
    db.session.add(invalid)
    db.session.flush()
    db.session.add(WaitlistSubscriptionParticipant(
        subscription_id=invalid.id,
        subject_user_id=users["test5"].id,
        name_snapshot=users["test5"].real_name,
        health_id_snapshot=users["test5"].health_id,
        booking_authorized_at=None,
    ))


def _package_dict(package: Package) -> dict:
    version = _package_version(package)
    return {
        "name": package.name,
        "focus_area": package.focus_area,
        "gender_scope": package.gender_scope,
        "price": float(package.price),
        "description": package.description,
        "package_type": package.package_type,
        "audience": package.audience,
        "booking_notice": package.booking_notice,
        "domain_ids": [row.health_domain_id for row in version.domains],
        "is_active": package.is_active,
    }


def _seed_package_reviews(users, institutions, packages, domains, now):
    pending_payload = {
        "name": "午间轻量健康筛查",
        "focus_area": "基础体征与循环快速筛查",
        "gender_scope": "all",
        "price": 399.0,
        "description": "机构拟新增的工作日午间预约服务。",
        "package_type": "combined",
        "audience": "时间有限、希望完成基础风险筛查的职场人",
        "booking_notice": "午间时段名额有限，具体结果以实际完成内容为准。",
        "domain_ids": [domains["basic"].id, domains["cardio"].id],
        "is_active": True,
    }
    approved_package = packages[_package_key(2, "糖脂代谢专项")]
    rejected_package = packages[_package_key(3, "呼吸与肺功能专项")]
    approved_before = _package_dict(approved_package)
    db.session.add_all([
        PackageChangeRequest(
            institution_id=institutions[0].id,
            action="create",
            status="pending",
            proposed_data=pending_payload,
            requested_by_user_id=users["institution1_staff1"].id,
            requested_at=now - timedelta(hours=6),
        ),
        PackageChangeRequest(
            institution_id=institutions[1].id,
            package_id=approved_package.id,
            action="update",
            status="approved",
            before_data={**approved_before, "price": 759.0},
            proposed_data=approved_before,
            requested_by_user_id=users["institution2_staff1"].id,
            reviewed_by_user_id=users["demo_admin"].id,
            requested_at=now - timedelta(days=12),
            reviewed_at=now - timedelta(days=11),
            review_note="适用人群和预约提示清楚，领域范围与专项定位一致。",
        ),
        PackageChangeRequest(
            institution_id=institutions[2].id,
            package_id=rejected_package.id,
            action="deactivate",
            status="rejected",
            before_data=_package_dict(rejected_package),
            proposed_data={**_package_dict(rejected_package), "is_active": False},
            requested_by_user_id=users["institution3_staff1"].id,
            reviewed_by_user_id=users["demo_admin"].id,
            requested_at=now - timedelta(days=7),
            reviewed_at=now - timedelta(days=6),
            review_note="该专项仍有未来预约，需先说明承接安排后再申请停用。",
        ),
    ])


def seed_v7_demo_experience(*, commit: bool = True) -> bool:
    """Populate the realistic schema-v8 snapshot when business tables are empty."""
    if any(model.query.first() is not None for model in (Appointment, InstitutionReport, SelfMeasurement)):
        return False
    institutions = Institution.query.order_by(Institution.id).all()
    if len(institutions) != 15 or Package.query.count() != 26:
        raise RuntimeError("the five-organization, fifteen-branch, twenty-six-package catalog is required")
    users = {item.username: item for item in User.query.filter(User.username.in_(REQUIRED_DEMO_USERNAMES)).all()}
    if set(users) != REQUIRED_DEMO_USERNAMES:
        raise RuntimeError("all fixed v8 demo accounts are required")
    from app.models import IndicatorDict
    indicators = {item.code: item for item in IndicatorDict.query.all()}
    domains = _domain_map()
    packages = {}
    for institution_index, institution in enumerate(institutions, start=1):
        for package in institution.packages:
            packages[_package_key(institution_index, package.name)] = package
    today = date.today()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _update_demo_profiles()
    _create_demo_images(institutions)

    relations = (
        ("test1", "test2", "伴侣", "active"),
        ("test2", "test4", "姐妹", "active"),
        ("test4", "test5", "朋友", "active"),
        ("test1", "test3", "待确认亲友", "pending"),
        ("test3", "test5", "已撤销亲友", "revoked"),
    )
    for viewer, owner, relation_name, status in relations:
        active = status == "active"
        db.session.add(FriendRelation(
            user_id=users[viewer].id,
            friend_user_id=users[owner].id,
            pair_key=FriendRelation.canonical_pair_key(
                users[viewer].id,
                users[owner].id,
            ),
            relation_name=relation_name,
            friend_relation_name="亲友" if active else None,
            status=status,
            auth_status=active,
            reverse_auth_status=active,
            booking_auth_status=active,
            reverse_booking_auth_status=active,
            booking_authorized_at=now - timedelta(days=60) if active else None,
            reverse_booking_authorized_at=(
                now - timedelta(days=60) if active else None
            ),
            accepted_at=now - timedelta(days=60) if active else None,
            revoked_at=now - timedelta(days=20) if status == "revoked" else None,
            created_at=now - timedelta(days=90),
        ))
    _seed_measurements(users, indicators, today)

    staff = {
        1: users["institution1_staff1"],
        2: users["institution2_staff1"],
        3: users["institution3_staff1"],
    }
    completed = []
    _, appointments = _create_booking_group(
        booker=users["test1"], participants=[users["test1"]], institution=institutions[0],
        package=packages[_package_key(1, "都市年度基础体检")], appointment_date=today - timedelta(days=500),
        status="fulfilled", created_at=_utc(today - timedelta(days=516), 10), version_number=1,
    )
    completed.append((appointments[0], staff[1], (
        ("WEIGHT", "75.2", "basic", False), ("BMI", "24.3", "basic", True),
        ("HR", "79", "cardio", False), ("FBG", "5.6", "metabolic", False),
        ("ALT", "31", "digestive", False), ("CREA", "85", "renal", False),
    ), (("basic", "年度健康基线", "该记录用于形成跨年度对照基线，后续结果请按来源分别查看。"),), None))

    _, appointments = _create_booking_group(
        booker=users["test1"], participants=[users["test1"]], institution=institutions[0],
        package=packages[_package_key(1, "都市年度基础体检")], appointment_date=today - timedelta(days=180),
        status="fulfilled", created_at=_utc(today - timedelta(days=195), 10), version_number=1,
    )
    completed.append((appointments[0], staff[1], (
        ("WEIGHT", "73.8", "basic", False), ("BMI", "23.8", "basic", False),
        ("HR", "76", "cardio", False), ("FBG", "5.5", "metabolic", False),
        ("ALT", "28", "digestive", False), ("CREA", "82", "renal", False),
    ), (("cardio", "循环检查结论", "静息状态下心率平稳，建议继续保持规律运动。"),), None))

    _, appointments = _create_booking_group(
        booker=users["test1"], participants=[users["test1"]], institution=institutions[1],
        package=packages[_package_key(2, "糖脂代谢专项")], appointment_date=today - timedelta(days=4),
        status="fulfilled", created_at=_utc(today - timedelta(days=18), 9),
    )
    completed.append((appointments[0], staff[2], (
        ("WEIGHT", "71.9", "metabolic", False), ("FBG", "5.2", "metabolic", False),
        ("TC", "4.8", "metabolic", False), ("TG", "1.4", "metabolic", False),
    ), (("metabolic", "代谢评估摘要", "本次糖脂代谢结果总体平稳，可结合个人自测继续观察体重与空腹血糖趋势。"),),
       ("metabolic", "甲状腺超声", ((31, 91, 145), (70, 145, 180), (199, 233, 238)), ASSET_FINDINGS["US_THYROID"])))
    for hour, value in ((8, 72.2), (20, 72.0)):
        _add_measurement(users["test1"], indicators["WEIGHT"], value, _utc(today - timedelta(days=4), hour))
    _add_measurement(users["test1"], indicators["HR"], 78, _utc(today - timedelta(days=4), 21))

    _, appointments = _create_booking_group(
        booker=users["test2"], participants=[users["test2"]], institution=institutions[1],
        package=packages[_package_key(2, "慢病风险综合评估")], appointment_date=today - timedelta(days=62),
        status="fulfilled", created_at=_utc(today - timedelta(days=78), 14),
    )
    completed.append((appointments[0], staff[2], (
        ("WEIGHT", "61.6", "basic", False), ("HR", "74", "cardio", False),
        ("FBG", "5.8", "metabolic", False), ("TC", "5.3", "metabolic", True),
        ("CREA", "68", "renal", False),
    ), (("metabolic", "随访建议", "总胆固醇略高，建议结合饮食和后续复查持续观察，不在平台内作诊断结论。"),), None))

    _, appointments = _create_booking_group(
        booker=users["test3"], participants=[users["test3"]], institution=institutions[0],
        package=packages[_package_key(1, "家庭长辈健康评估")], appointment_date=today - timedelta(days=91),
        status="fulfilled", created_at=_utc(today - timedelta(days=110), 11),
    )
    completed.append((appointments[0], staff[1], (
        ("WEIGHT", "70.9", "basic", False), ("HR", "78", "cardio", False),
        ("TC", "5.7", "cardio", True), ("FBG", "5.9", "metabolic", False),
        ("UA", "428", "renal", True), ("CREA", "96", "renal", False),
    ), (("renal", "肾脏与代谢关注", "尿酸结果偏高，建议携带本次结果向专业医务人员咨询后续管理。"),), None))

    _, appointments = _create_booking_group(
        booker=users["test4"], participants=[users["test4"]], institution=institutions[1],
        package=packages[_package_key(2, "肝胆代谢联合评估")], appointment_date=today - timedelta(days=31),
        status="fulfilled", created_at=_utc(today - timedelta(days=45), 16),
    )
    completed.append((appointments[0], staff[2], (
        ("FBG", "4.9", "metabolic", False), ("TC", "4.6", "metabolic", False),
        ("ALT", "22", "digestive", False), ("AST", "20", "digestive", False),
    ), (("digestive", "肝胆检查结论", "本次肝功能相关指标未见明显异常，建议保持规律饮食。"),),
       ("digestive", "腹部超声", ((76, 63, 111), (125, 105, 154), (222, 210, 232)), ASSET_FINDINGS["US_ABDOMEN"])))

    _, appointments = _create_booking_group(
        booker=users["test5"], participants=[users["test5"]], institution=institutions[2],
        package=packages[_package_key(3, "呼吸与肺功能专项")], appointment_date=today - timedelta(days=49),
        status="fulfilled", created_at=_utc(today - timedelta(days=64), 10),
    )
    completed.append((appointments[0], staff[3], (
        ("SPO2", "95", "respiratory", False),
    ), (("respiratory", "肺功能检查摘要", "本次静息血氧处于参考范围下沿，建议减少吸烟暴露并按需复查。"),),
       ("respiratory", "肺功能图", ((45, 75, 89), (79, 131, 144), (205, 226, 225)), ASSET_FINDINGS["SPIROMETRY"])))

    for appointment, creator, values, texts, asset in completed:
        _create_report(
            appointment=appointment, staff=creator, indicators=indicators, domains=domains,
            values=values, text_results=texts, asset=asset,
        )

    shared_history_day = today - timedelta(days=120)
    _create_imported_historical_report(
        user=users["test5"], institution=institutions[0],
        package=packages[_package_key(1, "都市年度基础体检")], staff=staff[1],
        exam_date=shared_history_day, indicators=indicators, domains=domains,
        values=(("HR", "82", "cardio", False), ("FBG", "5.6", "metabolic", False)),
        title="历史综合检查摘要",
        body="由纸质历史结果人工归档；与同日其他机构结果分别保留，不作静默合并。",
    )
    _create_imported_historical_report(
        user=users["test5"], institution=institutions[2],
        package=packages[_package_key(3, "职场综合体检")], staff=staff[3],
        exam_date=shared_history_day, indicators=indicators, domains=domains,
        values=(("HR", "80", "cardio", False), ("SPO2", "96", "respiratory", False), ("ALT", "26", "digestive", False)),
        title="历史职场检查摘要",
        body="同一自然日的另一机构来源，平台按机构独立展示原始结果。",
    )

    _create_booking_group(
        booker=users["test2"], participants=[users["test2"]], institution=institutions[1],
        package=packages[_package_key(2, "糖脂代谢专项")], appointment_date=today - timedelta(days=1),
        status="awaiting_report", created_at=_utc(today - timedelta(days=8), 12),
    )
    mixed_group, mixed_appointments = _create_booking_group(
        booker=users["test1"], participants=[users["test1"], users["test2"], users["test3"]], institution=institutions[0],
        package=packages[_package_key(1, "家庭长辈健康评估")], appointment_date=today + timedelta(days=12),
        status="unfulfilled", created_at=now - timedelta(days=2),
    )
    _seed_v12_mixed_booking_authorizations(
        group=mixed_group,
        appointments=mixed_appointments,
        users=users,
        now=now,
    )
    _create_booking_group(
        booker=users["test2"], participants=[users["test2"]], institution=institutions[1],
        package=packages[_package_key(2, "慢病风险综合评估")], appointment_date=today + timedelta(days=14),
        status="unfulfilled", created_at=now - timedelta(days=3),
    )
    _create_booking_group(
        booker=users["test4"], participants=[users["test4"]], institution=institutions[2],
        package=packages[_package_key(3, "职场综合体检")], appointment_date=today + timedelta(days=10),
        status="cancelled", created_at=now - timedelta(days=5),
    )
    _create_booking_group(
        booker=users["test4"], participants=[users["test4"]], institution=institutions[2],
        package=packages[_package_key(3, "职场综合体检")], appointment_date=today + timedelta(days=18),
        status="unfulfilled", created_at=now - timedelta(days=2),
    )
    _create_booking_group(
        booker=users["test5"], participants=[users["test5"]], institution=institutions[2],
        package=packages[_package_key(3, "心电与循环影像专项")], appointment_date=today - timedelta(days=2),
        status="no_show", created_at=now - timedelta(days=12),
    )
    _create_booking_group(
        booker=users["test4"], participants=[users["test4"]], institution=institutions[0],
        package=packages[_package_key(1, "都市年度基础体检")], appointment_date=today,
        status="unfulfilled", created_at=now - timedelta(days=4),
    )
    _create_booking_group(
        booker=users["test5"], participants=[users["test5"]], institution=institutions[1],
        package=packages[_package_key(2, "糖脂代谢专项")], appointment_date=today,
        status="awaiting_report", created_at=now - timedelta(days=6),
    )
    db.session.add_all([
        AppointmentCapacitySlot(institution_id=institutions[0].id, appointment_date=today,
                                capacity=18, revision=0, updated_at=now),
        AppointmentCapacitySlot(institution_id=institutions[1].id, appointment_date=today,
                                capacity=12, revision=0, updated_at=now),
    ])

    _seed_waitlists(users, institutions, packages, today, now)
    _seed_package_reviews(users, institutions, packages, domains, now)
    db.session.add_all([
        Comment(user_id=users["test1"].id, institution_id=institutions[0].id, rating=5,
                content="家庭预约流程清楚，陪父亲一起预约时能分别确认受检人。", is_visible=True,
                created_at=now - timedelta(days=20)),
        Comment(user_id=users["test3"].id, institution_id=institutions[0].id, rating=3,
                content="预约说明已收到，建议进一步优化到检前提醒。", is_visible=False,
                created_at=now - timedelta(days=18)),
        Comment(user_id=users["test4"].id, institution_id=institutions[1].id, rating=4,
                content="报告按代谢和肝胆分区展示，图片批注也比较直观。", is_visible=True,
                created_at=now - timedelta(days=15)),
        Comment(user_id=users["test5"].id, institution_id=institutions[2].id, rating=4,
                content="呼吸检查指引明确，希望后续增加更多可选时间段。", is_visible=False,
                created_at=now - timedelta(days=5)),
    ])
    _seed_v12_governance_workflows(users, now)
    _expand_v8_demo_data(users, institutions, packages, indicators, domains, today, now)
    _expand_v10_test1(users, institutions, packages, indicators, domains, today, now)
    db.session.flush()
    _normalize_report_business_records()
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return True


def account_identity_snapshot() -> dict[str, tuple]:
    return {
        user.username: tuple(getattr(user, field) for field in ACCOUNT_IDENTITY_FIELDS)
        for user in User.query.order_by(User.id).all()
    }


def validate_reset_target() -> None:
    users = User.query.order_by(User.id).all()
    names = {item.username for item in users}
    missing = sorted(LEGACY_DEMO_USERNAMES - names)
    if missing:
        raise DemoResetSafetyError(f"missing fixed demo accounts: {', '.join(missing)}")
    default_admin_username = os.getenv("DEFAULT_ADMIN_USERNAME", "admin").strip() or "admin"
    allowed_usernames = (
        REQUIRED_DEMO_USERNAMES
        | LEGACY_EXTRA_STAFF_USERNAMES
        | {default_admin_username}
    )
    unexpected_accounts = sorted(item.username for item in users if item.username not in allowed_usernames)
    if unexpected_accounts:
        raise DemoResetSafetyError(
            "refusing to erase business data while unknown accounts exist: " + ", ".join(unexpected_accounts)
        )
    institutions = Institution.query.order_by(Institution.id).all()
    if len(institutions) not in {3, 15}:
        raise DemoResetSafetyError(f"expected three legacy branches or fifteen v8 branches, found {len(institutions)}")
    for institution_index, institution in enumerate(institutions, start=1):
        expected = {f"institution{institution_index}_staff1"}
        actual = {
            item.username for item in users
            if item.role == "institution_admin" and item.managed_institution_id == institution.id
        }
        if actual != expected:
            raise DemoResetSafetyError(
                f"institution {institution.id} account binding differs from the fixed demo matrix"
            )


def _clear_demo_business_data() -> None:
    """Delete in FK-safe order while deliberately leaving every user row intact."""
    models = (
        UserNotification, InstitutionAudienceInsightCache, ComplaintMessage,
        ComplaintEvent,
        AppointmentComplaint, CommentAppeal, CommentSanction,
        ReportAccessLog, ReportAssetAnnotation, ReportAsset, ReportTextResult, ReportIndicator,
        InstitutionReport, AppointmentEvent, NotificationDelivery, NotificationOutbox,
        AvailabilityNotificationEvent, WaitlistSubscriptionParticipant,
        WaitlistSubscription, BookingParticipantAuthorization,
        BookingParticipantToken, Appointment, BookingGroup,
        AppointmentCapacitySlot,
        PackageChangeRequest, Comment, FriendRelation, SelfMeasurement,
        InstitutionInvite, InstitutionImage, PackageVersionAssetRequirement, PackageVersionDomain,
    )
    for model in models:
        model.query.delete(synchronize_session=False)
    Package.query.update({Package.current_version_id: None}, synchronize_session=False)
    PackageVersion.query.delete(synchronize_session=False)
    Package.query.delete(synchronize_session=False)
    db.session.flush()


def rebuild_v7_demo_data(*, commit: bool = True) -> dict:
    """Replace all demo business data after strict target validation.

    The caller owns database and upload backups. All database mutations share
    one transaction; ``commit=False`` lets the reset command validate staged
    attachments before it commits. Account identity is compared before commit.
    """
    validate_reset_target()
    before = account_identity_snapshot()
    try:
        _clear_demo_business_data()
        institutions = _ensure_demo_branches()
        ensure_v7_demo_accounts(commit=False)
        temperature = IndicatorDict.query.filter_by(code="TEMP").first()
        if temperature:
            temperature.reference_low = Decimal("36.10")
            temperature.reference_high = Decimal("37.20")
        _create_catalog(institutions)
        db.session.flush()
        # A reset must also work immediately after a v9-to-v10 upgrade, where
        # the persisted dictionary can still contain only the legacy entries.
        # These inserts stay in the reset transaction and roll back together.
        from app.seed import (
            seed_health_domains_and_versions,
            seed_indicator_dicts,
            seed_v10_asset_types,
            seed_v10_reference_rules,
        )
        seed_indicator_dicts(commit=False)
        seed_health_domains_and_versions(commit=False)
        seed_v10_asset_types(commit=False)
        seed_v10_reference_rules(commit=False)
        seeded = seed_v7_demo_experience(commit=False)
        if not seeded:
            raise RuntimeError("v8 demo experience was not rebuilt")
        # The compact TESTING fixture intentionally stops at five reports for
        # the first organization and may keep all of them in its first branch.
        # Shared-archive enrichment is a full demo-dataset quality gate (where
        # the organization has at least ten sibling-branch reports), so do not
        # apply that production-only threshold to the compact unit-test reset.
        if not current_app.config.get("TESTING", False):
            enrich_institution1_shared_archives(commit=False)
        after = account_identity_snapshot()
        for username, snapshot in before.items():
            if after.get(username) != snapshot:
                raise DemoResetSafetyError(f"account identity changed during demo rebuild: {username}")
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except Exception:
        db.session.rollback()
        raise
    return demo_snapshot_summary()


def demo_snapshot_summary() -> dict:
    summary = {
        "target_dataset_version": DEMO_DATASET_VERSION,
        "users": User.query.count(),
        "organizations": Organization.query.count(),
        "institutions": Institution.query.count(),
        "packages": Package.query.count(),
        "package_versions": PackageVersion.query.count(),
        "booking_groups": BookingGroup.query.count(),
        "appointments": Appointment.query.count(),
        "published_reports": InstitutionReport.query.filter_by(status="published").count(),
        "self_measurements": SelfMeasurement.query.count(),
        "waitlist_subscriptions": WaitlistSubscription.query.count(),
        "report_text_results": ReportTextResult.query.count(),
        "report_assets": ReportAsset.query.count(),
        "comments": Comment.query.count(),
    }
    summary["branch_distribution"] = {
        row.name: len(row.branches) for row in Organization.query.order_by(Organization.id).all()
    }
    summary["package_distribution"] = {
        row.name: Package.query.join(Institution).filter(Institution.organization_id == row.id).count()
        for row in Organization.query.order_by(Organization.id).all()
    }
    summary["report_distribution"] = {
        row.name: InstitutionReport.query.join(Institution).filter(
            Institution.organization_id == row.id, InstitutionReport.status == "published").count()
        for row in Organization.query.order_by(Organization.id).all()
    }
    summary["booking_group_distribution"] = {
        row.name: BookingGroup.query.join(Institution).filter(Institution.organization_id == row.id).count()
        for row in Organization.query.order_by(Organization.id).all()
    }
    return summary
