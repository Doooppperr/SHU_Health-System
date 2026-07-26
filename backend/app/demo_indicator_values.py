"""Plausible deterministic values for the full-scale synthetic report series."""

from __future__ import annotations

from decimal import Decimal


# These values are synthetic and intentionally include a few plausible
# high/low results, but never exceed ordinary measurement/physiological
# limits merely to exercise an abnormal label.
DEMO_REALISTIC_SERIES = {
    "HEIGHT": ("170", "171", "172", "173", "174", "173", "172", "171"),
    "WEIGHT": ("62", "64", "66", "68", "70", "72", "74", "76"),
    "WAIST": ("72", "75", "78", "81", "84", "87", "90", "93"),
    "HIP": ("90", "91", "92", "93", "94", "95", "96", "97"),
    "WHR": ("0.80", "0.82", "0.85", "0.87", "0.89", "0.92", "0.94", "0.96"),
    "BODY_FAT": ("16", "18", "20", "22", "24", "26", "28", "30"),
    "SBP": ("84", "108", "116", "124", "132", "138", "146", "156"),
    "DBP": ("56", "68", "72", "76", "80", "86", "92", "100"),
    "HR": ("54", "62", "68", "74", "80", "88", "96", "108"),
    "TEMP": ("35.8", "36.2", "36.4", "36.6", "36.8", "37.0", "37.4", "38.5"),
    "SPO2": ("91", "95", "96", "97", "98", "99", "97", "94"),
    "LVEF": ("45", "52", "55", "58", "61", "64", "67", "70"),
    "FVC": ("2.8", "3.0", "3.2", "3.4", "3.6", "3.8", "4.0", "4.2"),
    "FEV1": ("2.2", "2.4", "2.6", "2.8", "3.0", "3.2", "3.4", "3.6"),
    "FEV1_FVC": ("65", "72", "75", "78", "80", "82", "84", "86"),
    "PEF": ("4.8", "5.2", "5.6", "6.0", "6.4", "6.8", "7.2", "7.6"),
    "MVV": ("70", "78", "86", "94", "102", "110", "118", "126"),
    "VA_L": ("0.6", "0.8", "1.0", "1.0", "1.2", "1.0", "0.8", "1.0"),
    "VA_R": ("0.8", "1.0", "1.0", "1.2", "1.0", "0.8", "1.0", "1.0"),
    "IOP_L": ("12", "13", "14", "15", "16", "17", "18", "19"),
    "IOP_R": ("13", "14", "15", "16", "17", "18", "19", "20"),
    "BMD_T": ("-2.8", "-2.0", "-1.4", "-0.9", "-0.5", "-0.2", "0.1", "0.3"),
}

DEMO_RESULT_RANGES = {
    "SBP": (Decimal("90"), Decimal("139")),
    "DBP": (Decimal("60"), Decimal("89")),
    "HR": (Decimal("60"), Decimal("100")),
    "TEMP": (Decimal("36.1"), Decimal("37.2")),
    "SPO2": (Decimal("95"), Decimal("100")),
    "LVEF": (Decimal("50"), Decimal("75")),
    "FEV1_FVC": (Decimal("70"), Decimal("100")),
    "IOP_L": (Decimal("10"), Decimal("21")),
    "IOP_R": (Decimal("10"), Decimal("21")),
    "BMD_T": (Decimal("-1"), None),
}


def demo_realistic_value(code: str, sequence: int) -> str | None:
    values = DEMO_REALISTIC_SERIES.get(code)
    if not values:
        return None
    value = Decimal(values[sequence % len(values)]).quantize(Decimal("0.01"))
    return format(value, "f").rstrip("0").rstrip(".")


def demo_realistic_status(code: str, value: str) -> str:
    bounds = DEMO_RESULT_RANGES.get(code)
    if bounds is None:
        return "unknown"
    low, high = bounds
    numeric = Decimal(value)
    if low is not None and numeric < low:
        return "low"
    if high is not None and numeric > high:
        return "high"
    return "normal"
