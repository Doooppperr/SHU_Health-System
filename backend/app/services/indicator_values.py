from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation


_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_DECORATION_RE = re.compile(r"[\s,，↑↓↗↘→←▲▼△▽!！*＊()（）\[\]【】]+")
_OCR_REFERENCE_SUFFIX_RE = re.compile(
    r"\s*(?:[\(\[（【]\s*)?"
    r"(?:reference|ref(?:erence)?\.?|参考(?:值|范围)?|正常(?:值|范围))"
    r"\s*[:：]?\s*.*$",
    flags=re.IGNORECASE,
)


class IndicatorValueError(ValueError):
    pass


def parse_numeric_value(raw_value) -> Decimal | None:
    if raw_value is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw_value)).strip().replace(",", "")
    if not text:
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
    text = unicodedata.normalize(
        "NFKC", str("" if raw_value is None else raw_value)
    ).strip()
    if not text:
        raise IndicatorValueError("indicator value is required")

    if indicator_dict.value_type != "numeric":
        return text

    matches = list(_NUMBER_RE.finditer(text.replace(",", "")))
    if len(matches) != 1:
        raise IndicatorValueError("numeric indicator value must contain one number")

    normalized_text = text.replace(",", "")
    match = _NUMBER_RE.search(normalized_text)
    numeric = parse_numeric_value(match.group(0))
    if numeric is None:
        raise IndicatorValueError("invalid numeric indicator value")

    remainder = normalized_text[: match.start()] + normalized_text[match.end() :]
    expected_unit = unicodedata.normalize("NFKC", indicator_dict.unit or "").strip()
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

        text = unicodedata.normalize(
            "NFKC", str("" if raw_value is None else raw_value)
        ).strip()
        primary_result = _OCR_REFERENCE_SUFFIX_RE.sub("", text).strip()
        if not primary_result or primary_result == text:
            raise original_error
        return normalize_indicator_value(indicator_dict, primary_result)


def evaluate_is_abnormal(indicator_dict, normalized_value: str) -> bool:
    if indicator_dict.value_type != "numeric":
        return False
    value = parse_numeric_value(normalized_value)
    if value is None:
        return False
    if indicator_dict.reference_low is not None and value < indicator_dict.reference_low:
        return True
    if indicator_dict.reference_high is not None and value > indicator_dict.reference_high:
        return True
    return False
