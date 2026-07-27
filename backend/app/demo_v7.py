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
from app.models import (
    Appointment,
    AppointmentCapacitySlot,
    AppointmentEvent,
    AvailabilityNotificationEvent,
    BookingGroup,
    Comment,
    FriendRelation,
    HealthDomain,
    Institution,
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
DEMO_DATASET_VERSION = 10
DEMO_USERNAMES = tuple(f"test{index}" for index in range(1, 6))
DEMO_STAFF_USERNAMES = (
    *(f"institution{institution}_staff{staff}" for institution in range(1, 4) for staff in range(1, 3)),
    *(f"institution{institution}_staff1" for institution in range(4, 16)),
)
REQUIRED_DEMO_USERNAMES = {"demo_admin", *DEMO_USERNAMES, *DEMO_STAFF_USERNAMES}
LEGACY_DEMO_USERNAMES = {
    "demo_admin", *DEMO_USERNAMES,
    *(f"institution{institution}_staff{staff}" for institution in range(1, 4) for staff in range(1, 3)),
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
        _demo_package("陆家嘴职场轻体检", "基础体征与代谢风险筛查", "599.00", ("basic", "metabolic"), "工作节奏快、希望半日完成基础筛查的职场人"),
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
        _demo_package("人民广场都市综合体检", "中心城区职场人年度综合筛查", "799.00", ("basic", "cardio", "metabolic", "digestive"), "在中心城区工作、希望便捷完成年度体检的人群"),
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
        watermark = "演示数据 · 非真实医学影像" if chinese_font else "DEMO DATA · NOT A MEDICAL IMAGE"
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
    """Copy a checked-in open-license demo asset without regenerating it."""
    if current_app.config.get("TESTING", False):
        raw = _write_png(destination, palette, test_width, test_height)
        return raw, 2, 2
    source = Path(__file__).resolve().parents[1] / "uploads" / storage_key
    if not source.is_file():
        raise DemoResetSafetyError(f"缺少开放授权演示素材：{storage_key}")
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() != source.resolve():
        destination.write_bytes(raw)
    from PIL import Image
    with Image.open(BytesIO(raw)) as image:
        width, height = image.size
    return raw, width, height


# Checked-in real clinical examples used by the production/demo reset path.
# They are deliberately kept outside ``uploads`` so reset cannot fall back to
# the former synthetic colour fixtures.
CURATED_ASSET_SOURCES = {
    "US_THYROID": ("thyroid_normal",),
    "US_ABDOMEN": ("abdomen_liver", "abdomen_liver", "abdomen_liver"),
    "SPIROMETRY": ("spirometry_nih", "spirometry_nih"),
    "ECG_12": ("ecg_10sec", "ecg_10sec", "ecg_10sec", "ecg_10sec", "ecg_10sec"),
    "CHEST_IMAGE": ("chest_pa", "chest_lateral", "chest_pa"),
    "ECHO_HEART": ("echo_tte", "echo_tte"),
    "BLOOD_MICROSCOPY": ("blood_sem", "blood_sem", "blood_sem"),
}
CURATED_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "demo_media_sources"


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
    institution.is_active = True


def _ensure_organizations() -> dict[str, Organization]:
    rows = {item.name: item for item in Organization.query.all()}
    for scenario in ORGANIZATION_SCENARIOS:
        item = rows.get(scenario["name"])
        if item is None:
            item = Organization(name=scenario["name"])
            db.session.add(item)
            db.session.flush()
            rows[item.name] = item
        item.description = scenario["description"]
        item.service_features = scenario["service_features"]
        item.is_active = True
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
        changed = True
    for index, username in enumerate(DEMO_USERNAMES, start=1):
        existing = User.query.filter_by(username=username).first()
        if existing is not None:
            if existing.email != shared_email:
                existing.email = shared_email
                existing.email_verified_at = now
                changed = True
            continue
        name, birth_date, gender, allergy, history = PROFILE_SCENARIOS[username]
        user = User(
            username=username,
            role="user",
            health_id=f"HID-DEMO000{index}",
            real_name=name,
            birth_date=birth_date,
            gender=gender,
            allergy_history=allergy,
            medical_history=history,
            email=shared_email,
            email_verified_at=now,
            phone=f"138000000{index:02d}",
        )
        user.set_password(DEMO_PASSWORD)
        db.session.add(user); changed = True
    for institution_index, institution in enumerate(institutions, start=1):
        staff_indexes = (1, 2) if institution_index <= 3 else (1,)
        for staff_index in staff_indexes:
            username = f"institution{institution_index}_staff{staff_index}"
            existing = User.query.filter_by(username=username).first()
            if existing is not None:
                if existing.email != shared_email:
                    existing.email = shared_email
                    existing.email_verified_at = now
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
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return changed


def _update_demo_profiles() -> None:
    for username, (name, birth_date, gender, allergy, history) in PROFILE_SCENARIOS.items():
        user = User.query.filter_by(username=username).one()
        user.real_name = name
        user.birth_date = birth_date
        user.gender = gender
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
            "HEIGHT": [(180, 169.0)],
            "WEIGHT": [(28, 70.8), (14, 70.5), (2, 70.2)],
            "HR": [(28, 79), (21, 78), (14, 76), (7, 77), (1, 75)],
            "SPO2": [(14, 97), (7, 98), (1, 97)],
        },
        "test4": {
            "HEIGHT": [(180, 166.0)],
            "WEIGHT": [(35, 58.9), (28, 58.7), (21, 58.5), (14, 58.4), (7, 58.2)],
            "HR": [(21, 77), (14, 75), (7, 74), (1, 73)],
            "FBG": [(21, 5.0), (7, 4.9)],
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
            modality="open_license_demo_image",
            title=asset_type.name if asset_type else title,
            storage_key=key,
            mime_type="image/png",
            byte_size=len(raw),
            width=width,
            height=height,
            sha256=hashlib.sha256(raw).hexdigest(),
            annotation_text=(
                "开放授权真实医学样例，仅用于系统功能展示，不对应系统用户，不作为诊断依据。"
                if asset_type else annotation
            ),
            sort_order=0,
            uploaded_by_user_id=staff.id,
        )
        db.session.add(row)
        db.session.flush()
        db.session.add(ReportAssetAnnotation(
            report_asset_id=row.id,
            annotation_type="text",
            text=annotation,
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
    for asset_order, asset_code in enumerate(slot_plan[sequence]):
        asset_type = ReportAssetType.query.filter_by(code=asset_code, is_active=True).first()
        if asset_type is None:
            raise DemoResetSafetyError(f"缺少附件槽位定义：{asset_code}")
        if not PackageVersionDomain.query.filter_by(
            package_version_id=report.package_version_id,
            health_domain_id=asset_type.health_domain_id,
        ).first():
            max_order = max(
                (
                    row.sort_order
                    for row in PackageVersionDomain.query.filter_by(
                        package_version_id=report.package_version_id,
                    ).all()
                ),
                default=-1,
            )
            db.session.add(PackageVersionDomain(
                package_version_id=report.package_version_id,
                health_domain_id=asset_type.health_domain_id,
                sort_order=max_order + 1,
            ))
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
            modality="open_license_demo_image",
            title=asset_type.name,
            storage_key=key,
            mime_type="image/png",
            byte_size=len(raw),
            width=width,
            height=height,
            sha256=hashlib.sha256(raw).hexdigest(),
            annotation_text="开放授权真实医学样例，仅用于系统功能展示，不对应系统用户，不作为诊断依据。",
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


def _expand_v8_demo_data(users, institutions, packages, indicators, domains, today, now):
    """Reach the fixed v8 demonstration scale with deterministic stories."""
    testing = current_app.config.get("TESTING", False)
    report_target = 15 if testing else 50
    asset_target = 4 if testing else 15
    group_target = BookingGroup.query.count() if testing else 40
    appointment_target = Appointment.query.count() if testing else 56
    measurement_target = max(SelfMeasurement.query.count(), 70) if testing else 120
    domain_indicator = {
        "basic": ("WEIGHT", "72.0"),
        "cardio": ("HR", "76"),
        "metabolic": ("FBG", "5.4"),
        "digestive": ("ALT", "25"),
        "respiratory": ("SPO2", "97"),
        "renal": ("CREA", "78"),
        "hematology": ("HR", "74"),
        "other": ("WEIGHT", "61.0"),
    }
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
            user = users[DEMO_USERNAMES[sequence % len(DEMO_USERNAMES)]]
            package = package_by_branch[branch_index]
            version = _package_version(package)
            domain = sorted(version.domains, key=lambda item: item.sort_order)[0].domain
            indicator_code, value = domain_indicator[domain.code]
            report = _create_imported_historical_report(
                user=user, institution=branch, package=package, staff=staff_by_branch[branch_index],
                exam_date=today - timedelta(days=760 + sequence * 9), indicators=indicators, domains=domains,
                values=((indicator_code, value, domain.code, sequence % 9 == 0),),
                title=f"{branch.branch_name}历史体检摘要",
                body="该合成记录用于演示同机构不同分院之间的已归档报告衔接，源分院继续保留内容责任。",
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
            participant_start = group_sequence % len(DEMO_USERNAMES)
            participants = [users[DEMO_USERNAMES[(participant_start + offset) % len(DEMO_USERNAMES)]] for offset in range(party_size)]
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
        username = DEMO_USERNAMES[measurement_sequence % len(DEMO_USERNAMES)]
        code = measurement_indicators[measurement_sequence % len(measurement_indicators)]
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


def _demo_indicator_value(definition, sequence):
    if definition.value_type == "text":
        return ("阳性", "positive") if sequence in {2, 10} else ("阴性", "negative")
    realistic_value = demo_realistic_value(definition.code, sequence)
    if realistic_value is not None:
        return (realistic_value, None)
    low = Decimal(str(definition.reference_low)) if definition.reference_low is not None else None
    high = Decimal(str(definition.reference_high)) if definition.reference_high is not None else None
    story_phase = (
        Decimal("0.62"), Decimal("0.60"), Decimal("0.58"), Decimal("0.56"),
        Decimal("0.54"), Decimal("0.52"), Decimal("0.50"), Decimal("0.48"),
        Decimal("0.46"), Decimal("0.45"), Decimal("0.50"), Decimal("0.59"),
        Decimal("0.55"), Decimal("0.51"), Decimal("0.48"), Decimal("0.46"),
    )[sequence % 16]
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


def _expand_v10_test1(users, institutions, packages, indicators, domains, today, now):
    """Build test1's coherent four-year, sixteen-exam adult health story."""
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
    dates = [
        today - timedelta(days=days)
        for days in (1460, 1320, 1180, 1040, 900, 760, 620, 500,
                     380, 280, 190, 120, 70, 35, 14, 2)
    ]
    comprehensive_sequences = {0, 4, 8, 12, 15}
    targeted_domains = {
        1: {"cardio", "basic", "metabolic"},
        2: {"digestive", "renal", "hematology"},
        3: {"respiratory", "other", "cardio"},
        5: {"basic", "metabolic", "digestive"},
        6: {"renal", "hematology", "respiratory"},
        7: {"other", "cardio", "basic"},
        9: {"metabolic", "digestive", "renal"},
        10: {"hematology", "respiratory", "other"},
        11: {"cardio", "metabolic", "hematology"},
        13: {"basic", "digestive", "respiratory"},
        14: {"renal", "other", "metabolic"},
    }
    core_trend_codes = {
        "BMI", "WEIGHT", "WAIST", "SBP", "DBP", "FBG", "HBA1C",
        "TC", "TG", "HDL", "LDL", "ALT", "AST", "GGT", "UA", "CREA",
    }
    ocr_sequences = {2, 6, 10, 14}
    reports = []
    for sequence, exam_date in enumerate(dates):
        institution = institutions[sequence % 3]
        branch_index = institutions.index(institution) + 1
        package = sorted(institution.packages, key=lambda item: item.id)[0]
        staff = users[f"institution{branch_index}_staff1"]
        version = _package_version(package)
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
            ocr_diagnostics={
                "import_kind": (
                    "v10_comprehensive_exam"
                    if sequence in comprehensive_sequences
                    else "v10_targeted_follow_up"
                ),
                "sequence": sequence + 1,
                "contains_ocr_rows": sequence in ocr_sequences,
                "synthetic_story": "early_risk_to_improvement_with_brief_relapse",
            },
            locked_at=published_at - timedelta(hours=1),
            submitted_at=published_at,
            published_at=published_at,
            created_at=published_at - timedelta(hours=3),
        )
        db.session.add(report)
        db.session.flush()
        definitions = sorted(indicators.values(), key=lambda item: item.id)
        if sequence not in comprehensive_sequences:
            selected_domains = targeted_domains[sequence]
            definitions = [
                definition
                for definition in definitions
                if definition.code in core_trend_codes
                or any(
                    link.domain and link.domain.code in selected_domains
                    for link in definition.domain_links
                )
            ]
        for definition in definitions:
            value, explicit_status = _demo_indicator_value(definition, sequence)
            status = explicit_status or evaluate_result_status(
                definition,
                value,
                subject=user,
                on_date=exam_date,
            )
            domain = next(
                (link.domain for link in sorted(definition.domain_links, key=lambda link: (not link.is_primary, link.sort_order, link.id)) if link.domain),
                domains["other"],
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
                method_snapshot="v10 合成体检演示",
                abnormal_flag={"high": "H", "low": "L", "positive": "+"}.get(status),
                mapping_confidence=Decimal("0.9900") if sequence in ocr_sequences else Decimal("1.0000"),
                mapping_status="confirmed",
            ))
        report_kind = "综合体检" if sequence in comprehensive_sequences else "专项复查"
        report.text_results.append(ReportTextResult(
            health_domain_id=domains["basic"].id,
            title=f"第 {sequence + 1} 次{report_kind}演示",
            body=(
                "本报告及全部数值均为合成测试内容。四年故事用于演示早期轻度代谢风险、"
                "生活方式改善、短期反弹和近期稳定，仅用于功能验收，不作为诊断依据。"
            ),
            source_snapshot="v10 合成演示数据",
            sort_order=0,
            created_by_user_id=staff.id,
        ))
        reports.append((report, staff))

    asset_types = {row.code: row for row in ReportAssetType.query.all()}
    v10_asset_slots = (
        ("SPIROMETRY", "CHEST_IMAGE"),
        ("ECG_12", "ECHO_HEART"),
    )
    for sequence, (report, staff) in enumerate(reports[-2:]):
        for order, code in enumerate(v10_asset_slots[sequence]):
            asset_type = asset_types.get(code)
            if asset_type is None:
                continue
            key = f"health-assets/demo-v10/report-{report.id}-{code.lower()}.png"
            raw, width, height = _load_curated_media(
                code, Path(current_app.config["UPLOAD_DIR"]) / key, sequence,
            )
            db.session.add(ReportAsset(
                report_id=report.id,
                health_domain_id=asset_type.health_domain_id,
                asset_type_id=asset_type.id,
                modality="image",
                title=asset_type.name,
                storage_key=key,
                mime_type="image/png",
                byte_size=len(raw),
                width=width,
                height=height,
                sha256=hashlib.sha256(raw).hexdigest(),
                annotation_text=f"{asset_type.name}开放授权真实医学样例：仅用于系统功能展示，不对应系统用户，不作为诊断依据。",
                sort_order=order,
                uploaded_by_user_id=staff.id,
            ))

    db.session.add_all([
        UserNotification(
            user_id=user.id,
            event_type="report_published",
            idempotency_key="demo-v10-report-published",
            title="体检报告已交付",
            body="最新一份全量成人体检演示报告已交付，可查看规范附件和机构批注。",
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
    if len(institutions) != 15 or Package.query.count() != 25:
        raise RuntimeError("the five-organization, fifteen-branch, twenty-five-package v8 catalog is required")
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
        ("test1", "test2", "伴侣", True, True),
        ("test1", "test3", "父亲", True, True),
        ("test2", "test4", "姐妹", True, False),
        ("test4", "test5", "朋友", False, False),
    )
    for viewer, owner, relation_name, health_auth, booking_auth in relations:
        db.session.add(FriendRelation(
            user_id=users[viewer].id,
            friend_user_id=users[owner].id,
            relation_name=relation_name,
            auth_status=health_auth,
            booking_auth_status=booking_auth,
            booking_authorized_at=now - timedelta(days=60) if booking_auth else None,
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
       ("metabolic", "代谢检查开放授权演示附件", ((31, 91, 145), (70, 145, 180), (199, 233, 238)), "开放授权演示附件，仅用于系统功能展示，不作为诊断依据。")))
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
       ("digestive", "腹部超声开放授权演示附件", ((76, 63, 111), (125, 105, 154), (222, 210, 232)), "开放授权演示附件，仅用于系统功能展示，不作为诊断依据。")))

    _, appointments = _create_booking_group(
        booker=users["test5"], participants=[users["test5"]], institution=institutions[2],
        package=packages[_package_key(3, "呼吸与肺功能专项")], appointment_date=today - timedelta(days=49),
        status="fulfilled", created_at=_utc(today - timedelta(days=64), 10),
    )
    completed.append((appointments[0], staff[3], (
        ("SPO2", "95", "respiratory", False),
    ), (("respiratory", "肺功能检查摘要", "本次静息血氧处于参考范围下沿，建议减少吸烟暴露并按需复查。"),),
       ("respiratory", "肺功能开放授权演示附件", ((45, 75, 89), (79, 131, 144), (205, 226, 225)), "开放授权演示附件，仅用于系统功能展示，不作为诊断依据。")))

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
    _create_booking_group(
        booker=users["test1"], participants=[users["test1"], users["test2"], users["test3"]], institution=institutions[0],
        package=packages[_package_key(1, "家庭长辈健康评估")], appointment_date=today + timedelta(days=12),
        status="unfulfilled", created_at=now - timedelta(days=2),
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
        Comment(user_id=users["test4"].id, institution_id=institutions[1].id, rating=4,
                content="报告按代谢和肝胆分区展示，图片批注也比较直观。", is_visible=True,
                created_at=now - timedelta(days=15)),
        Comment(user_id=users["test5"].id, institution_id=institutions[2].id, rating=4,
                content="呼吸检查指引明确，希望后续增加更多可选时间段。", is_visible=False,
                created_at=now - timedelta(days=5)),
    ])
    _expand_v8_demo_data(users, institutions, packages, indicators, domains, today, now)
    _expand_v10_test1(users, institutions, packages, indicators, domains, today, now)
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
    allowed_usernames = REQUIRED_DEMO_USERNAMES | {default_admin_username}
    unexpected_accounts = sorted(item.username for item in users if item.username not in allowed_usernames)
    if unexpected_accounts:
        raise DemoResetSafetyError(
            "refusing to erase business data while unknown accounts exist: " + ", ".join(unexpected_accounts)
        )
    institutions = Institution.query.order_by(Institution.id).all()
    if len(institutions) not in {3, 15}:
        raise DemoResetSafetyError(f"expected three legacy branches or fifteen v8 branches, found {len(institutions)}")
    for institution_index, institution in enumerate(institutions, start=1):
        expected = ({f"institution{institution_index}_staff1", f"institution{institution_index}_staff2"}
                    if institution_index <= 3 else {f"institution{institution_index}_staff1"})
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
        UserNotification, ReportAccessLog, ReportAssetAnnotation, ReportAsset, ReportTextResult, ReportIndicator,
        InstitutionReport, AppointmentEvent, NotificationDelivery, NotificationOutbox,
        AvailabilityNotificationEvent, WaitlistSubscriptionParticipant,
        WaitlistSubscription, Appointment, BookingGroup, AppointmentCapacitySlot,
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
