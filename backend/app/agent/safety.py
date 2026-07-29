from __future__ import annotations

import re


_EMERGENCY_PATTERNS = (
    ("chest_pain", re.compile(r"(持续|剧烈|突然)?胸(痛|闷|部压榨感)|心前区(疼|痛)")),
    ("breathing", re.compile(r"(呼吸困难|喘不上气|无法呼吸|嘴唇发紫|窒息)")),
    ("stroke", re.compile(r"(口角歪斜|一侧肢体无力|突然说不清话|言语不清|疑似脑卒中|疑似中风)")),
    ("anaphylaxis", re.compile(r"(严重过敏|喉头水肿|过敏性休克|全身风团.{0,8}呼吸)")),
    ("unconscious", re.compile(r"(失去意识|意识丧失|昏迷|叫不醒)")),
    ("seizure", re.compile(r"(正在抽搐|持续抽搐|癫痫大发作)")),
    ("trauma", re.compile(r"(大量出血|严重创伤|高处坠落|车祸重伤)")),
    ("self_harm", re.compile(r"(想自杀|不想活了|准备自残|已经自残|结束生命)")),
)
_NEGATED = re.compile(r"(没有|并无|否认|未出现|不是).{0,4}$")


def detect_emergency(message: str):
    normalized = "".join(str(message or "").split())
    for code, pattern in _EMERGENCY_PATTERNS:
        match = pattern.search(normalized)
        if match and not _NEGATED.search(normalized[:match.start()]):
            return {
                "code": code,
                "message": (
                    "你描述的情况可能需要紧急处理。请立即停止普通咨询，拨打 120，"
                    "或让身边的人陪同前往最近的急诊。不要独自驾车。"
                ),
            }
    return None
