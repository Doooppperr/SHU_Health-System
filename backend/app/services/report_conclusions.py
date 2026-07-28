"""Health-domain conclusion coverage and deterministic institution summaries."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation


DESCRIPTIVE_CODES = {"HEIGHT", "WEIGHT", "WAIST", "HIP", "WHR", "BODY_FAT"}
ATTENTION_STATUSES = {"high", "low", "positive", "abnormal"}
NORMAL_STATUSES = {"normal", "negative"}

DOMAIN_TITLES = {
    "basic": "体格检查结论",
    "cardio": "心血管检查结论",
    "metabolic": "代谢与内分泌检查结论",
    "digestive": "肝胆胰检查结论",
    "renal": "肾功能与尿检结论",
    "hematology": "血液检查结论",
    "respiratory": "呼吸功能结论",
    "other": "其他专科检查结论",
}

DOMAIN_RECOMMENDATIONS = {
    "basic": "建议保持规律作息和运动，并结合后续体重、腰围及血压变化持续观察。",
    "cardio": "建议继续关注血压、血脂及心电变化，并按复查计划进行随访。",
    "metabolic": "建议保持规律饮食与运动，并按复查计划观察糖脂代谢变化。",
    "digestive": "建议保持规律饮食、控制饮酒，并按复查计划观察肝胆胰相关指标。",
    "renal": "建议保证合理饮水，结合尿检及肾功能结果按计划复查。",
    "hematology": "建议结合血常规及炎症指标变化按计划复查。",
    "respiratory": "建议减少烟尘暴露并保持规律运动，按计划复查肺功能或胸部影像。",
    "other": "建议结合本次专科检查结果按计划复查。",
}

ASSET_FINDINGS = {
    "ECG_12": "十二导联心电图示窦性心律，节律规则，未见明显 ST-T 异常。",
    "ECHO_HEART": "心脏超声示各心腔大小未见明显异常，室壁运动协调，左心室收缩功能正常。",
    "US_THYROID": "甲状腺形态及大小正常，内部回声均匀，未见明确占位。",
    "US_ABDOMEN": "腹部超声示肝胆胰脾形态未见明显异常，未见明确局灶性病变。",
    "CHEST_IMAGE": "胸部影像示双肺纹理清晰，未见明显活动性病灶，心影大小正常。",
    "SPIROMETRY": "肺功能曲线形态正常，未见明显阻塞性或限制性通气障碍。",
    "BLOOD_MICROSCOPY": "血细胞形态及分布未见明显异常。",
}

ASSET_MODALITIES = {
    "ECG_12": "ecg",
    "ECHO_HEART": "ultrasound",
    "US_THYROID": "ultrasound",
    "US_ABDOMEN": "ultrasound",
    "CHEST_IMAGE": "xray",
    "SPIROMETRY": "spirometry",
    "BLOOD_MICROSCOPY": "microscopy",
}


def represented_domains(report):
    """Return the report's actual indicator/asset domains in display order."""
    domains = {}
    for item in report.indicators:
        if item.display_domain_id and item.display_domain:
            domains[item.display_domain_id] = item.display_domain
    for asset in report.assets:
        if asset.health_domain_id and asset.domain:
            domains[asset.health_domain_id] = asset.domain
    return sorted(domains.values(), key=lambda row: (row.sort_order, row.id))


def missing_conclusion_domains(report):
    covered = {item.health_domain_id for item in report.text_results}
    return [domain for domain in represented_domains(report) if domain.id not in covered]


def _status_label(status):
    return {
        "high": "偏高",
        "low": "偏低",
        "positive": "阳性",
        "negative": "阴性",
        "abnormal": "异常",
        "normal": "正常",
    }.get(status, "")


def _display_value(item):
    definition = item.indicator_dict
    unit = item.normalized_unit or (definition.unit if definition else None) or ""
    return f"{item.value}{(' ' + unit) if unit else ''}"


def _numeric_value(item):
    try:
        return Decimal(str(item.value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _comparison_text(item, previous):
    current_value = _numeric_value(item)
    previous_value = _numeric_value(previous) if previous is not None else None
    if current_value is None or previous_value is None:
        return ""
    baseline = abs(previous_value) if previous_value else Decimal("1")
    change_ratio = abs(current_value - previous_value) / baseline
    if change_ratio <= Decimal("0.03"):
        direction = "基本持平"
    elif current_value > previous_value:
        direction = "升高"
    else:
        direction = "下降"
    return f"，较上次{direction}"


def _recommendation(domain, attention):
    codes = {
        item.indicator_dict.code
        for item in attention
        if item.indicator_dict is not None
    }
    if "FOBT" in codes:
        return "建议排除饮食及痔出血等影响后复查粪便隐血；如持续阳性，建议至消化专科进一步评估。"
    if codes & {"U_PRO", "U_BLD", "U_LEU", "U_NIT"}:
        return "建议避开剧烈运动后留取清洁晨尿复查尿常规；如结果持续异常，结合肾功能至相关专科评估。"
    if codes & {"FBG", "HBA1C", "INS"}:
        return "建议调整精制糖和总能量摄入、保持规律运动，并按计划复查空腹血糖及糖化血红蛋白。"
    if codes & {"TC", "TG", "HDL", "LDL", "NON_HDL", "APOA1", "APOB", "LPA"}:
        return "建议减少高脂饮食、增加规律有氧运动，并按计划复查血脂；结合血压及其他风险因素综合评估。"
    if codes & {"SBP", "DBP"}:
        return "建议连续监测安静状态下血压，减少高盐饮食；如多次测量仍偏高，至相关专科进一步评估。"
    if codes & {"ALT", "AST", "GGT", "ALP", "TBIL", "DBIL"}:
        return "建议近期避免饮酒和熬夜，复查肝功能；如持续异常，结合腹部超声进一步评估。"
    if "UA" in codes:
        return "建议保证合理饮水、减少高嘌呤饮食，并按计划复查尿酸和肾功能。"
    if codes & {"BMI", "WAIST", "BODY_FAT"}:
        return "建议通过饮食结构调整和规律运动控制体重与腰围，并持续观察相关代谢指标。"
    if codes & {"SPO2", "FEV1", "FEV1_FVC", "FVC", "PEF", "MVV"}:
        return "建议减少烟尘暴露并按计划复查肺功能；如出现持续咳嗽、气促等情况，至呼吸专科评估。"
    if codes & {"BMD_T"}:
        return "建议结合饮食、日照和运动情况关注骨健康，并按计划复查骨密度。"
    return DOMAIN_RECOMMENDATIONS.get(domain.code, DOMAIN_RECOMMENDATIONS["other"])


def build_domain_conclusion(report, domain, previous_by_code=None):
    """Build a concise conclusion from the report's persisted facts."""
    previous_by_code = previous_by_code or {}
    indicators = [
        item for item in report.indicators
        if item.display_domain_id == domain.id and item.indicator_dict is not None
    ]
    attention = [
        item for item in indicators
        if item.result_status in ATTENTION_STATUSES
        and item.indicator_dict.code not in DESCRIPTIVE_CODES
    ][:3]
    normal = [
        item for item in indicators
        if item.result_status in NORMAL_STATUSES
        and item.indicator_dict.code not in DESCRIPTIVE_CODES
    ][:2]
    asset_findings = [
        ASSET_FINDINGS.get(asset.asset_type.code)
        for asset in report.assets
        if asset.health_domain_id == domain.id and asset.asset_type
        and ASSET_FINDINGS.get(asset.asset_type.code)
    ]

    sentences = []
    if attention:
        details = []
        for item in attention:
            definition = item.indicator_dict
            previous = previous_by_code.get(definition.code)
            details.append(
                f"{definition.name}{_display_value(item)}，结果{_status_label(item.result_status)}"
                f"{_comparison_text(item, previous)}"
            )
        sentences.append("本次检查需要关注：" + "；".join(details) + "。")
    elif indicators:
        sentences.append("本次该方向检查结果总体平稳，未见明显异常。")

    if normal:
        details = "、".join(
            f"{item.indicator_dict.name}{_display_value(item)}"
            for item in normal
        )
        sentences.append(f"{details}处于参考范围内。")
    if asset_findings:
        sentences.extend(dict.fromkeys(asset_findings))
    if not sentences:
        sentences.append("本次该方向检查未见明显异常。")
    sentences.append(_recommendation(domain, attention))
    return DOMAIN_TITLES.get(domain.code, f"{domain.name}检查结论"), "".join(sentences)


def indicator_method(item):
    definition = item.indicator_dict
    if definition is None:
        return "机构常规检测"
    code = definition.code
    category = definition.category.name if definition.category else ""
    if code in {"HEIGHT", "WEIGHT", "BMI", "WAIST", "HIP", "WHR", "BODY_FAT", "SBP", "DBP", "HR", "TEMP", "SPO2"}:
        return "体格测量"
    if code.startswith("U_") or category == "肾脏与尿检":
        return "尿干化学与流式分析" if code.startswith("U_") else "全自动生化分析"
    if category == "血常规与炎症" or code in {"WBC", "RBC", "HGB", "HCT", "PLT"}:
        return "全自动血细胞分析"
    if category == "凝血":
        return "凝固法"
    if category in {"甲状腺与骨代谢", "糖代谢"} and code not in {"FBG", "CA", "P"}:
        return "化学发光免疫分析"
    if code in {"CRP"}:
        return "免疫比浊法"
    return "全自动生化分析"


def normalize_report_records(reports, *, text_result_factory, asset_annotation_factory=None):
    """Replace report conclusions and normalize technical metadata in place."""
    history = defaultdict(dict)
    for report in sorted(reports, key=lambda row: (row.matched_user_id or 0, row.exam_date, row.id)):
        for text in list(report.text_results):
            report.text_results.remove(text)
        domains = represented_domains(report)
        owner_history = history[report.matched_user_id or 0]
        for order, domain in enumerate(domains):
            title, body = build_domain_conclusion(report, domain, owner_history)
            report.text_results.append(text_result_factory(report, domain, title, body, order))
        for item in report.indicators:
            item.method_snapshot = indicator_method(item)
            if item.indicator_dict:
                owner_history[item.indicator_dict.code] = item
        for asset in report.assets:
            if asset.asset_type:
                code = asset.asset_type.code
                asset.annotation_text = ASSET_FINDINGS.get(code, asset.annotation_text)
                asset.modality = ASSET_MODALITIES.get(code, asset.modality)
            if asset_annotation_factory:
                asset_annotation_factory(asset)
