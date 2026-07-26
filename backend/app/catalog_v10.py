"""Schema v10 adult physical-examination dictionary extensions.

Ranges are conservative demo defaults only. Institution report ranges and
age/sex-specific reference rules take precedence at runtime.
"""

from decimal import Decimal


def _seed(category, code, name, aliases, unit, low, high, domains, *, value_type="numeric"):
    return {
        "category": category,
        "code": code,
        "name": name,
        "aliases": list(dict.fromkeys([name, code, *aliases])),
        "unit": unit,
        "reference_low": Decimal(str(low)) if low is not None else None,
        "reference_high": Decimal(str(high)) if high is not None else None,
        "clinical_significance": f"{name}用于成人健康体检中的结构化观察，结果需结合机构原始参考范围和医生意见。",
        "value_type": value_type,
        "domains": domains,
    }


V10_INDICATOR_CATEGORIES = [
    {"name": "心血管检查", "sort_order": 6},
    {"name": "糖代谢与甲状腺", "sort_order": 7},
    {"name": "血常规与炎症", "sort_order": 8},
    {"name": "尿常规", "sort_order": 9},
    {"name": "肺功能", "sort_order": 10},
    {"name": "凝血功能", "sort_order": 11},
    {"name": "专科检查", "sort_order": 12},
]


V10_INDICATOR_SEEDS = [
    _seed("一般检查", "WAIST", "腰围", ["腹围"], "cm", None, None, ("basic", "metabolic")),
    _seed("一般检查", "HIP", "臀围", [], "cm", None, None, ("basic",)),
    _seed("一般检查", "WHR", "腰臀比", ["腰围臀围比"], None, None, None, ("basic", "metabolic")),
    _seed("一般检查", "SBP", "收缩压", ["高压", "SYS"], "mmHg", 90, 139, ("cardio", "basic")),
    _seed("一般检查", "DBP", "舒张压", ["低压", "DIA"], "mmHg", 60, 89, ("cardio", "basic")),
    _seed("一般检查", "BODY_FAT", "体脂率", ["体脂百分比", "PBF"], "%", None, None, ("basic", "metabolic")),

    _seed("血脂", "NON_HDL", "非高密度脂蛋白胆固醇", ["非HDL-C", "non-HDL-C"], "mmol/L", 0, 4.1, ("cardio", "metabolic")),
    _seed("血脂", "APOA1", "载脂蛋白A1", ["ApoA1", "载脂蛋白AⅠ"], "g/L", 1.0, 1.6, ("cardio", "metabolic")),
    _seed("血脂", "APOB", "载脂蛋白B", ["ApoB"], "g/L", 0.6, 1.1, ("cardio", "metabolic")),
    _seed("血脂", "LPA", "脂蛋白(a)", ["Lp(a)", "脂蛋白a"], "mg/L", 0, 300, ("cardio",)),
    _seed("心血管检查", "HCY", "同型半胱氨酸", ["Hcy", "血同型半胱氨酸"], "μmol/L", 5, 15, ("cardio",)),
    _seed("心血管检查", "LVEF", "左心室射血分数", ["EF", "射血分数"], "%", 50, 75, ("cardio",)),
    _seed("心血管检查", "ECG_HR", "心电图心率", ["ECG心率"], "次/分", 60, 100, ("cardio",)),

    _seed("糖代谢与甲状腺", "HBA1C", "糖化血红蛋白", ["HbA1c", "GHb"], "%", 4, 6, ("metabolic",)),
    _seed("糖代谢与甲状腺", "INS", "空腹胰岛素", ["FINS", "胰岛素"], "mIU/L", None, None, ("metabolic",)),
    _seed("糖代谢与甲状腺", "TSH", "促甲状腺激素", ["促甲状腺素"], "mIU/L", 0.27, 4.2, ("metabolic",)),
    _seed("糖代谢与甲状腺", "FT3", "游离三碘甲状腺原氨酸", ["游离T3"], "pmol/L", 3.1, 6.8, ("metabolic",)),
    _seed("糖代谢与甲状腺", "FT4", "游离甲状腺素", ["游离T4"], "pmol/L", 12, 22, ("metabolic",)),
    _seed("糖代谢与甲状腺", "T3", "总三碘甲状腺原氨酸", ["总T3"], "nmol/L", 1.3, 3.1, ("metabolic",)),
    _seed("糖代谢与甲状腺", "T4", "总甲状腺素", ["总T4"], "nmol/L", 66, 181, ("metabolic",)),
    _seed("糖代谢与甲状腺", "CA", "血清钙", ["钙", "Calcium"], "mmol/L", 2.1, 2.6, ("metabolic",)),
    _seed("糖代谢与甲状腺", "PHOS", "血清磷", ["无机磷", "P"], "mmol/L", 0.81, 1.45, ("metabolic",)),
    _seed("糖代谢与甲状腺", "VITD", "25羟维生素D", ["25-OH-VD", "维生素D"], "ng/mL", 20, 100, ("metabolic", "other")),

    _seed("肝功能", "ALP", "碱性磷酸酶", ["AKP"], "U/L", 45, 125, ("digestive",)),
    _seed("肝功能", "GGT", "γ-谷氨酰转移酶", ["谷氨酰转肽酶", "γ-GT"], "U/L", 0, 60, ("digestive",)),
    _seed("肝功能", "TBIL", "总胆红素", ["T-BIL"], "μmol/L", 5, 21, ("digestive",)),
    _seed("肝功能", "DBIL", "直接胆红素", ["D-BIL"], "μmol/L", 0, 7, ("digestive",)),
    _seed("肝功能", "IBIL", "间接胆红素", ["I-BIL"], "μmol/L", 0, 14, ("digestive",)),
    _seed("肝功能", "TP", "总蛋白", ["血清总蛋白"], "g/L", 65, 85, ("digestive",)),
    _seed("肝功能", "ALB", "白蛋白", ["Albumin"], "g/L", 40, 55, ("digestive",)),
    _seed("肝功能", "GLOB", "球蛋白", ["GLB"], "g/L", 20, 40, ("digestive",)),
    _seed("肝功能", "AGR", "白球比", ["A/G"], None, 1.2, 2.4, ("digestive",)),
    _seed("肝功能", "LDH", "乳酸脱氢酶", [], "U/L", 120, 250, ("digestive", "hematology")),
    _seed("肝功能", "AMY", "淀粉酶", ["AMS"], "U/L", 30, 110, ("digestive",)),
    _seed("肝功能", "LIP", "脂肪酶", ["LPS"], "U/L", 13, 60, ("digestive",)),
    _seed("肝功能", "FOBT", "粪便隐血", ["大便隐血", "OB"], None, None, None, ("digestive",), value_type="text"),

    _seed("肾功能", "BUN", "尿素氮", ["血尿素氮"], "mmol/L", 2.9, 8.2, ("renal",)),
    _seed("肾功能", "EGFR", "估算肾小球滤过率", ["eGFR"], "mL/min/1.73m²", 90, None, ("renal",)),
    _seed("肾功能", "CYSC", "胱抑素C", ["Cys-C", "Cystatin C"], "mg/L", 0.51, 1.09, ("renal",)),
    _seed("尿常规", "U_PH", "尿液酸碱度", ["尿pH", "PH"], None, 4.5, 8, ("renal",)),
    _seed("尿常规", "U_SG", "尿比重", ["尿液比重", "SG"], None, 1.005, 1.03, ("renal",)),
    _seed("尿常规", "U_PRO", "尿蛋白", ["PRO", "蛋白质"], None, None, None, ("renal",), value_type="text"),
    _seed("尿常规", "U_GLU", "尿糖", ["尿葡萄糖", "UGLU"], None, None, None, ("renal",), value_type="text"),
    _seed("尿常规", "U_KET", "尿酮体", ["KET"], None, None, None, ("renal",), value_type="text"),
    _seed("尿常规", "U_BLD", "尿潜血", ["BLD", "尿隐血"], None, None, None, ("renal",), value_type="text"),
    _seed("尿常规", "U_LEU", "尿白细胞酯酶", ["LEU"], None, None, None, ("renal",), value_type="text"),
    _seed("尿常规", "U_NIT", "尿亚硝酸盐", ["NIT"], None, None, None, ("renal",), value_type="text"),
    _seed("尿常规", "U_RBC", "尿红细胞", ["尿RBC"], "个/μL", 0, 25, ("renal",)),
    _seed("尿常规", "U_WBC", "尿白细胞", ["尿WBC"], "个/μL", 0, 25, ("renal",)),
    _seed("尿常规", "UACR", "尿白蛋白肌酐比", ["尿微量白蛋白肌酐比"], "mg/g", 0, 30, ("renal",)),

    _seed("血常规与炎症", "WBC", "白细胞计数", ["白细胞", "白细胞总数"], "10^9/L", 3.5, 9.5, ("hematology",)),
    _seed("血常规与炎症", "RBC", "红细胞计数", ["红细胞"], "10^12/L", None, None, ("hematology",)),
    _seed("血常规与炎症", "HGB", "血红蛋白", ["Hb", "血色素"], "g/L", None, None, ("hematology",)),
    _seed("血常规与炎症", "HCT", "红细胞压积", ["血细胞比容"], "%", None, None, ("hematology",)),
    _seed("血常规与炎症", "MCV", "平均红细胞体积", [], "fL", 82, 100, ("hematology",)),
    _seed("血常规与炎症", "MCH", "平均红细胞血红蛋白量", [], "pg", 27, 34, ("hematology",)),
    _seed("血常规与炎症", "MCHC", "平均红细胞血红蛋白浓度", [], "g/L", 316, 354, ("hematology",)),
    _seed("血常规与炎症", "RDW", "红细胞分布宽度", ["RDW-CV"], "%", 11, 16, ("hematology",)),
    _seed("血常规与炎症", "PLT", "血小板计数", ["血小板"], "10^9/L", 125, 350, ("hematology",)),
    _seed("血常规与炎症", "MPV", "平均血小板体积", [], "fL", 7, 13, ("hematology",)),
    _seed("血常规与炎症", "NEUT_COUNT", "中性粒细胞计数", ["NEUT#", "中性粒细胞绝对值"], "10^9/L", 1.8, 6.3, ("hematology",)),
    _seed("血常规与炎症", "NEUT_PCT", "中性粒细胞百分比", ["NEUT%"], "%", 40, 75, ("hematology",)),
    _seed("血常规与炎症", "LYMPH_COUNT", "淋巴细胞计数", ["LYMPH#", "淋巴细胞绝对值"], "10^9/L", 1.1, 3.2, ("hematology",)),
    _seed("血常规与炎症", "LYMPH_PCT", "淋巴细胞百分比", ["LYMPH%"], "%", 20, 50, ("hematology",)),
    _seed("血常规与炎症", "MONO_COUNT", "单核细胞计数", ["MONO#"], "10^9/L", 0.1, 0.6, ("hematology",)),
    _seed("血常规与炎症", "MONO_PCT", "单核细胞百分比", ["MONO%"], "%", 3, 10, ("hematology",)),
    _seed("血常规与炎症", "EOS_COUNT", "嗜酸性粒细胞计数", ["EOS#"], "10^9/L", 0.02, 0.52, ("hematology",)),
    _seed("血常规与炎症", "EOS_PCT", "嗜酸性粒细胞百分比", ["EOS%"], "%", 0.4, 8, ("hematology",)),
    _seed("血常规与炎症", "BASO_COUNT", "嗜碱性粒细胞计数", ["BASO#"], "10^9/L", 0, 0.06, ("hematology",)),
    _seed("血常规与炎症", "BASO_PCT", "嗜碱性粒细胞百分比", ["BASO%"], "%", 0, 1, ("hematology",)),
    _seed("血常规与炎症", "CRP", "C反应蛋白", ["C-反应蛋白"], "mg/L", 0, 10, ("hematology",)),
    _seed("血常规与炎症", "ESR", "红细胞沉降率", ["血沉"], "mm/h", 0, 20, ("hematology",)),

    _seed("肺功能", "FVC", "用力肺活量", [], "L", None, None, ("respiratory",)),
    _seed("肺功能", "FEV1", "第一秒用力呼气容积", [], "L", None, None, ("respiratory",)),
    _seed("肺功能", "FEV1_FVC", "第一秒率", ["FEV1/FVC"], "%", 70, 100, ("respiratory",)),
    _seed("肺功能", "PEF", "呼气峰值流量", [], "L/s", None, None, ("respiratory",)),
    _seed("肺功能", "MVV", "最大自主通气量", [], "L/min", None, None, ("respiratory",)),
    _seed("肺功能", "FENO", "呼出气一氧化氮", ["FeNO"], "ppb", 0, 25, ("respiratory",)),

    _seed("凝血功能", "PT", "凝血酶原时间", [], "s", 9, 13, ("hematology", "other")),
    _seed("凝血功能", "INR", "国际标准化比值", [], None, 0.8, 1.2, ("hematology", "other")),
    _seed("凝血功能", "APTT", "活化部分凝血活酶时间", [], "s", 20, 40, ("hematology", "other")),
    _seed("凝血功能", "TT", "凝血酶时间", [], "s", 14, 21, ("hematology", "other")),
    _seed("凝血功能", "FIB", "纤维蛋白原", [], "g/L", 2, 4, ("hematology", "other")),

    _seed("专科检查", "VA_L", "左眼裸眼视力", ["左眼视力"], None, None, None, ("other",)),
    _seed("专科检查", "VA_R", "右眼裸眼视力", ["右眼视力"], None, None, None, ("other",)),
    _seed("专科检查", "IOP_L", "左眼眼压", ["左眼压"], "mmHg", 10, 21, ("other",)),
    _seed("专科检查", "IOP_R", "右眼眼压", ["右眼压"], "mmHg", 10, 21, ("other",)),
    _seed("专科检查", "BMD_T", "骨密度T值", ["T-score", "骨密度T评分"], None, -1, None, ("other",)),
    _seed("专科检查", "HEARING", "听力检查结论", ["听力"], None, None, None, ("other",), value_type="text"),
]


ASSET_TYPE_SEEDS = [
    ("GENERAL_EXAM", "basic", "综合体检附件", "image", 1),
    ("ECG_12", "cardio", "十二导联心电图", "image", 1),
    ("ECHO_HEART", "cardio", "心脏彩超", "image", 1),
    ("US_CAROTID", "cardio", "颈动脉超声", "image", 1),
    ("US_THYROID", "metabolic", "甲状腺超声", "image", 1),
    ("US_ABDOMEN", "digestive", "腹部超声", "image", 1),
    ("CHEST_IMAGE", "respiratory", "胸片或胸部CT", "image", 1),
    ("SPIROMETRY", "respiratory", "肺功能图", "image", 1),
    ("US_URINARY", "renal", "泌尿系统超声", "image", 1),
    ("BONE_DENSITY", "other", "骨密度报告", "image", 1),
    ("FUNDUS", "other", "眼底检查影像", "image", 1),
    ("BREAST_IMAGE", "other", "乳腺检查影像", "image", 1),
    ("GYNE_IMAGE", "other", "妇科检查影像", "image", 1),
    ("OTHER", "other", "其他检查附件", "image", 2),
]
