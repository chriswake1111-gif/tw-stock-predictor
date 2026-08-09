"""Pure deterministic geometry for approved-anchor Phase 4 scenarios."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


OUTPUT_QUANTUM = Decimal("0.0001")


def _price(value: float, name: str) -> Decimal:
    candidate = Decimal(str(value))
    if not candidate.is_finite() or candidate <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return candidate


def _output(value: Decimal) -> float:
    return float(value.quantize(OUTPUT_QUANTUM, rounding=ROUND_HALF_UP))


def calculate_equal_amplitude(origin: float, swing_end: float, projection_origin: float) -> dict:
    """FB-03: D = C + (B - A), preserving the sign of the original move."""
    a = _price(origin, "origin")
    b = _price(swing_end, "swing_end")
    c = _price(projection_origin, "projection_origin")
    amplitude = b - a
    if amplitude == 0:
        raise ValueError("equal-amplitude scenario requires a non-zero move")
    level = c + amplitude
    if level <= 0:
        raise ValueError("equal-amplitude calculated level is not a valid positive price")
    return {
        "scenario_type": "equal_amplitude",
        "formula": "equal_move",
        "formula_expression": "C + (B - A)",
        "direction": "upward" if amplitude > 0 else "downward" if amplitude < 0 else "flat",
        "swing_amplitude": _output(amplitude),
        "calculated_level": _output(level),
        "price_unit": "TWD_per_share",
    }


def calculate_retracement_0382(origin: float, swing_end: float) -> dict:
    """FB-04: approved upward swing retracement B - 0.382 * (B - A)."""
    a = _price(origin, "origin")
    b = _price(swing_end, "swing_end")
    if b <= a:
        raise ValueError("FB-04 only supports the specified upward swing relationship B > A")
    amplitude = b - a
    level = b - Decimal("0.382") * amplitude
    return {
        "scenario_type": "retracement_0382",
        "formula": "retracement_0382",
        "formula_expression": "B - 0.382 * (B - A)",
        "direction": "upward_swing_retracement",
        "swing_amplitude": _output(amplitude),
        "calculated_level": _output(level),
        "price_unit": "TWD_per_share",
    }
