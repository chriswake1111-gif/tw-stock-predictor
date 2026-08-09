import pytest

from src.engine.fibonacci_scenarios import (
    calculate_equal_amplitude,
    calculate_retracement_0382,
)


def test_equal_amplitude_preserves_upward_and_downward_direction():
    upward = calculate_equal_amplitude(100, 150, 130)
    downward = calculate_equal_amplitude(150, 100, 120)

    assert upward["calculated_level"] == 180.0
    assert upward["direction"] == "upward"
    assert downward["calculated_level"] == 70.0
    assert downward["direction"] == "downward"
    assert upward["formula"] == downward["formula"] == "C + (B - A)"


def test_0382_uses_specified_upward_retracement_and_deterministic_rounding():
    result = calculate_retracement_0382(100, 150)

    assert result["calculated_level"] == 130.9
    assert result["price_unit"] == "TWD_per_share"
    assert result["formula"] == "B - 0.382 * (B - A)"
    assert calculate_retracement_0382(100.001, 150.009)["calculated_level"] == 130.9059


def test_0382_downward_relationship_fails_closed_instead_of_guessing():
    with pytest.raises(ValueError, match="upward swing"):
        calculate_retracement_0382(150, 100)


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan")])
def test_scenario_prices_must_be_positive_and_finite(bad):
    with pytest.raises(ValueError):
        calculate_equal_amplitude(bad, 150, 130)
