from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import date


_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_DECORATION_RE = re.compile(r"[\s,，↑↓↗↘→←▲▼△▽!！*＊()（）\[\]【】]+")
_OCR_REFERENCE_SUFFIX_RE = re.compile(
    r"\s*(?:[\(\[（【]\s*)?"
    r"(?:reference|ref(?:erence)?\.?|参考(?:值|范围)?|正常(?:值|范围))"
    r"\s*[:：]?\s*.*$",
    flags=re.IGNORECASE,
)
_REFERENCE_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_NON_CLASSIFIABLE_CODES = frozenset({"HEIGHT", "WEIGHT", "HIP"})
_PLAUSIBLE_INPUT_BOUNDS = {
    "HEIGHT": (Decimal("80"), Decimal("250"), "成人身高应填写 80–250 cm"),
    "WEIGHT": (Decimal("20"), Decimal("500"), "成人体重应填写 20–500 kg"),
}
_DEFINITE_RESULT_STATUSES = frozenset(
    {"normal", "high", "low", "positive", "negative", "abnormal"}
)


def _normalize_unit_aliases(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"(?i)\b(?:u|μ)mol\b", "μmol", normalized)
    normalized = re.sub(
        r"(?i)\b([munμ]?mol)\s*[·⋅]\s*l\s*(?:\^?\s*[-−]\s*1|⁻¹)\b",
        r"\1/L",
        normalized,
    )
    return normalized


class IndicatorValueError(ValueError):
    pass


def parse_numeric_value(raw_value) -> Decimal | None:
    if raw_value is None:
        return None
    text = _normalize_unit_aliases(str(raw_value)).strip()
    if not text:
        return None
    # A single comma can be either a decimal separator or a thousands
    # separator. Guessing turns values such as 5,6 into 56, so ambiguous OCR
    # punctuation must be reviewed instead of silently rewritten.
    if "," in text:
        return None
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(0))
    except (InvalidOperation, ValueError):
        return None


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def normalize_indicator_value(indicator_dict, raw_value) -> str:
    text = _normalize_unit_aliases(
        str("" if raw_value is None else raw_value)
    ).strip()
    if not text:
        raise IndicatorValueError("indicator value is required")

    if indicator_dict.value_type != "numeric":
        return text

    if "," in text:
        raise IndicatorValueError(
            "numeric comma is ambiguous; use a decimal point or remove thousands separators"
        )

    matches = list(_NUMBER_RE.finditer(text))
    if len(matches) != 1:
        raise IndicatorValueError("numeric indicator value must contain one number")

    normalized_text = text
    match = _NUMBER_RE.search(normalized_text)
    numeric = parse_numeric_value(match.group(0))
    if numeric is None:
        raise IndicatorValueError("invalid numeric indicator value")

    remainder = normalized_text[: match.start()] + normalized_text[match.end() :]
    expected_unit = _normalize_unit_aliases(indicator_dict.unit or "").strip()
    if expected_unit:
        remainder = re.sub(re.escape(expected_unit), "", remainder, flags=re.IGNORECASE)
    remainder = _DECORATION_RE.sub("", remainder)
    if remainder:
        raise IndicatorValueError(
            "indicator unit does not match the standard dictionary"
        )
    return _canonical_decimal(numeric)


def normalize_ocr_indicator_value(indicator_dict, raw_value) -> str:
    """Normalize OCR output while ignoring an explicit reference-range suffix.

    Table OCR sometimes merges the result and the adjacent reference column into
    one cell (for example ``5.6 mmol/L(reference 3.9-6.1)``). Only a suffix with
    an explicit reference marker is removed; otherwise the normal strict
    multi-number and unit checks remain unchanged.
    """
    try:
        return normalize_indicator_value(indicator_dict, raw_value)
    except IndicatorValueError as original_error:
        if indicator_dict.value_type != "numeric":
            raise

        text = _normalize_unit_aliases(
            str("" if raw_value is None else raw_value)
        ).strip()
        primary_result = _OCR_REFERENCE_SUFFIX_RE.sub("", text).strip()
        if not primary_result or primary_result == text:
            raise original_error
        return normalize_indicator_value(indicator_dict, primary_result)


def validate_indicator_plausibility(indicator_dict, normalized_value: str) -> None:
    bounds = _PLAUSIBLE_INPUT_BOUNDS.get(getattr(indicator_dict, "code", None))
    if bounds is None:
        return
    value = parse_numeric_value(normalized_value)
    low, high, message = bounds
    if value is None or value < low or value > high:
        raise IndicatorValueError(message)


def resolve_reference_rule(indicator_dict, *, subject=None, on_date=None):
    if subject is None:
        return None
    gender = getattr(subject, "gender", None) or "other"
    birth_date = getattr(subject, "birth_date", None)
    check_date = on_date or date.today()
    age = None
    if birth_date:
        age = check_date.year - birth_date.year - (
            (check_date.month, check_date.day) < (birth_date.month, birth_date.day)
        )
    candidates = []
    for rule in getattr(indicator_dict, "reference_rules", []) or []:
        if rule.gender_scope not in {"all", gender}:
            continue
        if age is not None and rule.min_age is not None and age < rule.min_age:
            continue
        if age is not None and rule.max_age is not None and age > rule.max_age:
            continue
        candidates.append(rule)
    return candidates[0] if candidates else None


def parse_reference_bounds(reference_text):
    """Return numeric bounds carried by an institution report, if usable."""
    text = unicodedata.normalize("NFKC", str(reference_text or "")).strip()
    if not text:
        return None
    numbers = _REFERENCE_NUMBER_RE.findall(text)
    if not numbers:
        return None
    try:
        if len(numbers) == 1:
            bound = Decimal(numbers[0])
            if re.search(r"(?:>=|>|≥|不低于|高于|大于)", text):
                return bound, None
            if re.search(r"(?:<=|<|≤|不超过|低于|小于)", text):
                return None, bound
            return None
        # Reference text can contain digits in the unit (for example 10^9/L).
        # The first two values are the range endpoints.
        low = Decimal(numbers[0])
        high_text = numbers[1]
        if high_text.startswith("-") and not numbers[0].startswith("-") and re.search(
            r"\d\s*[-—–~至]\s*\d", text
        ):
            high_text = high_text[1:]
        high = Decimal(high_text)
    except (InvalidOperation, ValueError):
        return None
    return (low, high) if low <= high else None


def result_status_is_displayable(status) -> bool:
    return str(status or "").strip().lower() in _DEFINITE_RESULT_STATUSES


def evaluate_result_status(
    indicator_dict,
    normalized_value: str,
    *,
    subject=None,
    on_date=None,
    abnormal_flag=None,
    reference_text=None,
) -> str:
    # Raw height, weight and hip circumference describe body dimensions. They
    # are not independently labelled normal/abnormal; derived indicators such
    # as BMI or a properly configured waist/WHR rule carry that interpretation.
    if getattr(indicator_dict, "code", None) in _NON_CLASSIFIABLE_CODES:
        return "unknown"

    flag = unicodedata.normalize("NFKC", str(abnormal_flag or "")).strip().lower()
    if flag in {"h", "↑", "high", "偏高", "升高"}:
        return "high"
    if flag in {"l", "↓", "low", "偏低", "降低"}:
        return "low"
    if flag in {"positive", "+", "阳性"}:
        return "positive"
    if flag in {"negative", "-", "阴性"}:
        return "negative"
    if flag in {"abnormal", "异常"}:
        return "abnormal"

    if indicator_dict.value_type != "numeric":
        text = unicodedata.normalize("NFKC", str(normalized_value or "")).strip().lower()
        if text in {"阴性", "negative", "neg", "-"}:
            return "negative"
        if text in {"阳性", "positive", "pos", "+"}:
            return "positive"
        return "unknown"

    value = parse_numeric_value(normalized_value)
    if value is None:
        return "unknown"
    report_bounds = parse_reference_bounds(reference_text)
    if report_bounds is not None:
        low, high = report_bounds
    else:
        rule = resolve_reference_rule(indicator_dict, subject=subject, on_date=on_date)
        low = rule.reference_low if rule is not None else indicator_dict.reference_low
        high = rule.reference_high if rule is not None else indicator_dict.reference_high
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    if low is None and high is None:
        return "unknown"
    return "normal"


def evaluate_is_abnormal(indicator_dict, normalized_value: str) -> bool:
    return evaluate_result_status(indicator_dict, normalized_value) in {
        "high",
        "low",
        "positive",
        "abnormal",
    }
