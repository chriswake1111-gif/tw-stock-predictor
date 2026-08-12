from decimal import Decimal

import pytest

from src.domain.snapshot_comparison import (
    MISSING,
    SnapshotDelta,
    ChangeCategory,
    canonical_decimal,
    canonical_timestamp,
    canonical_value,
    delta_sort_key,
)


def test_decimal_canonicalization_is_finite_exponent_free_and_stable():
    assert canonical_decimal("1.2300") == "1.23"
    assert canonical_decimal(Decimal("1E+3")) == "1000"
    assert canonical_decimal("-0.000") == "0"
    with pytest.raises(ValueError, match="finite"):
        canonical_decimal("NaN")
    with pytest.raises(ValueError, match="finite"):
        canonical_decimal("Infinity")


def test_timestamp_requires_timezone_and_normalizes_to_utc():
    assert canonical_timestamp("2026-08-12T20:00:00+08:00") == "2026-08-12T12:00:00Z"
    with pytest.raises(ValueError, match="timezone"):
        canonical_timestamp("2026-08-12T12:00:00")


def test_missing_null_set_and_record_values_have_distinct_stable_forms():
    assert canonical_value(MISSING) == {"state": "missing"}
    assert canonical_value(None) is None
    assert canonical_value(["B", "A", "A"], value_kind="set") == ["A", "B"]
    nested = [{"b": [2, 1], "a": 1}, {"a": 1, "b": [2, 1]}]
    assert canonical_value(nested, value_kind="set") == [{"a": 1, "b": [2, 1]}]
    assert canonical_value({"b": 2.0, "a": [2, 1]}) == {
        "a": [2, 1],
        "b": "2",
    }


def test_delta_sorting_is_independent_of_input_order():
    first = SnapshotDelta(
        category=ChangeCategory.STORED_FACT,
        change_type="section_status_changed",
        section="valuation",
        canonical_identity="valuation",
        field_path="valuation.status",
        before="partial",
        after="available",
    )
    second = SnapshotDelta(
        category=ChangeCategory.STORED_FACT,
        change_type="data_quality_status_changed",
        section="data_quality",
        canonical_identity="data_quality",
        field_path="data_quality.status",
        before="partial",
        after="available",
    )
    assert sorted([first, second], key=delta_sort_key) == [second, first]
