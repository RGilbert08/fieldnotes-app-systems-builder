"""Strict, null-safe normalization for dashboard response metrics."""

from __future__ import annotations

import math


def normalize_int(value: object) -> int:
    """Return a finite integer metric, using zero for null or invalid values."""
    try:
        numeric_value = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(numeric_value):
        return 0
    return int(numeric_value)


def normalize_percentage_int(value: object) -> int:
    """Return a whole-number percentage from either fraction or percent storage."""
    try:
        numeric_value = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(numeric_value) or numeric_value == 0:
        return 0
    if -1 <= numeric_value <= 1:
        numeric_value *= 100
    return int(numeric_value)