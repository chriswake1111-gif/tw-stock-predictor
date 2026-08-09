import math

import pytest

from src.domain.screening import SecurityValuationObservation
from src.engine.value_screening import ValueScreeningEngine


def profile(**changes):
    value = {
        "valuation_basis": "self_history",
        "pe_percentile_max": 30.0,
        "pb_percentile_max": 30.0,
        "dividend_yield_percentile_min": 70.0,
        "minimum_observations": 20,
        "forward_eps_source_name": "broker-a",
        "forward_eps_source_type": "broker_report",
        "forward_eps_growth_convention": "consecutive_fiscal_year_base",
        "technical_component": "wv03_confirmation",
    }
    value.update(changes)
    return value


def valuation_rows(pe_current=5.0, pb_current=0.5, yield_current=0.08, count=20):
    rows = []
    for index in range(count):
        rows.append({
            "id": f"valuation-{index}",
            "logical_observation_id": f"logical-{index}",
            "metric_date": f"2025-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
            "pe": 10.0 + index,
            "pb": 1.0 + index / 10,
            "dividend_yield_ratio": 0.01 + index / 1000,
        })
    rows[-1].update({
        "pe": pe_current,
        "pb": pb_current,
        "dividend_yield_ratio": yield_current,
    })
    return rows


def forward_eps(earlier=10.0, later=12.0, years=(2027, 2028)):
    return [
        {
            "id": f"eps-{year}",
            "fiscal_year": year,
            "eps_base": value,
            "source_name": "broker-a",
            "source_type": "broker_report",
            "verified_approval_id": f"approval-{year}",
        }
        for year, value in zip(years, (earlier, later))
    ]


def technical(turned_positive=True, rule_id="WV-03", evidence_level="B"):
    return {
        "status": "available",
        "reason": None,
        "turned_positive": turned_positive,
        "component_name": "wv03_confirmation",
        "rule_id": rule_id,
        "rule_version": "2.0.0",
        "evidence_level": evidence_level,
        "implementation_mode": "parameterized_support",
        "project_operationalization": False,
        "data_as_of": "2026-01-01T00:00:00Z",
        "details": {},
    }


def evaluate(**changes):
    inputs = {
        "symbol": "2330.TW",
        "knowledge_cutoff_at": "2026-01-01T00:00:00Z",
        "profile": profile(),
        "valuation_observations": valuation_rows(),
        "forward_eps_observations": forward_eps(),
        "technical_result": technical(),
        "window_start": "2021-01-01",
        "window_end": "2026-01-01",
    }
    inputs.update(changes)
    return ValueScreeningEngine().evaluate(**inputs)


def test_low_pe_low_pb_high_yield_meets_percentile_profile():
    result = evaluate()
    assert result["status"] == "available"
    assert result["research_result"] == "meets_approved_profile"
    assert result["components"]["pe"]["passed"] is True
    assert result["components"]["pb"]["passed"] is True
    assert result["components"]["dividend_yield"]["passed"] is True
    assert result["automatic_order"] is False


@pytest.mark.parametrize(
    ("changes", "component"),
    [
        ({"pe_current": 100.0}, "pe"),
        ({"pb_current": 10.0}, "pb"),
        ({"yield_current": 0.0}, "dividend_yield"),
    ],
)
def test_unfavorable_percentile_fails_component(changes, component):
    result = evaluate(valuation_observations=valuation_rows(**changes))
    assert result["status"] == "available"
    assert result["research_result"] == "does_not_meet_approved_profile"
    assert result["components"][component]["passed"] is False


def test_ties_use_deterministic_upper_rank():
    engine = ValueScreeningEngine()
    values = [2.0, 2.0, 2.0, 3.0]
    assert engine.percentile(2.0, values) == 75.0
    assert engine.percentile(2.0, values) == 75.0


def test_minimum_sample_fails_closed():
    result = evaluate(valuation_observations=valuation_rows(count=19))
    assert result["status"] == "insufficient_data"
    assert {"pe", "pb", "dividend_yield"}.issubset(result["missing_components"])
    assert result["components"]["pe"]["percentile"] is None


@pytest.mark.parametrize(
    ("field", "value", "component"),
    [
        ("pe_current", -3.0, "pe"),
        ("pe_current", 0.0, "pe"),
        ("pe_current", math.nan, "pe"),
        ("pe_current", math.inf, "pe"),
        ("pb_current", -1.0, "pb"),
        ("pb_current", 0.0, "pb"),
        ("pb_current", math.nan, "pb"),
        ("pb_current", math.inf, "pb"),
        ("yield_current", -0.01, "dividend_yield"),
        ("yield_current", math.nan, "dividend_yield"),
        ("yield_current", math.inf, "dividend_yield"),
    ],
)
def test_invalid_current_metrics_never_pass(field, value, component):
    result = evaluate(valuation_observations=valuation_rows(**{field: value}))
    assert result["status"] == "available"
    assert result["research_result"] == "does_not_meet_approved_profile"
    assert result["components"][component]["status"] == "invalid_metric"
    assert result["components"][component]["passed"] is False


def test_absolute_pb_and_yield_values_do_not_override_percentiles():
    rows = valuation_rows(pb_current=1.4, yield_current=0.05)
    for row in rows[:-1]:
        row["pb"] = 1.0
        row["dividend_yield_ratio"] = 0.06
    result = evaluate(valuation_observations=rows)
    assert result["components"]["pb"]["passed"] is False
    assert result["components"]["dividend_yield"]["passed"] is False
    assert result["research_result"] == "does_not_meet_approved_profile"


@pytest.mark.parametrize(
    ("earlier", "later", "status", "passed"),
    [
        (10.0, 12.0, "available", True),
        (10.0, 10.0, "available", True),
        (10.0, 8.0, "available", False),
    ],
)
def test_forward_eps_growth_gate(earlier, later, status, passed):
    component = ValueScreeningEngine.forward_eps_growth_component(
        forward_eps(earlier, later), profile()
    )
    assert component["status"] == status
    assert component["passed"] is passed


def test_forward_eps_requires_two_consecutive_years():
    one = ValueScreeningEngine.forward_eps_growth_component(
        forward_eps()[:1], profile()
    )
    gap = ValueScreeningEngine.forward_eps_growth_component(
        forward_eps(years=(2027, 2029)), profile()
    )
    assert one["reason"] == "forward_eps_growth_requires_two_years"
    assert gap["reason"] == "forward_eps_growth_requires_consecutive_years"


def test_forward_eps_source_is_not_averaged_or_auto_selected():
    rows = forward_eps() + [
        {
            **forward_eps()[1],
            "id": "eps-2028-duplicate",
            "verified_approval_id": "approval-duplicate",
        }
    ]
    component = ValueScreeningEngine.forward_eps_growth_component(rows, profile())
    assert component["status"] == "insufficient_data"
    assert component["reason"] == "forward_eps_source_selection_ambiguous"


def test_nonpositive_earlier_forward_eps_is_not_low_pe_pass():
    result = evaluate(forward_eps_observations=forward_eps(-1.0, 2.0))
    assert result["status"] == "insufficient_data"
    assert result["research_result"] is None
    assert result["components"]["forward_eps_growth"]["reason"] == "non_comparable_forward_eps"


def test_technical_turn_is_replaceable_and_legacy_ma_cannot_pass():
    negative = evaluate(technical_result=technical(False))
    legacy = evaluate(technical_result=technical(True, rule_id="MA-03", evidence_level="U"))
    assert negative["research_result"] == "does_not_meet_approved_profile"
    assert legacy["components"]["technical_turn"]["status"] == "unsupported"
    assert legacy["status"] == "insufficient_data"
    assert legacy["research_result"] is None


def test_dividend_yield_contract_rejects_percent_unit():
    observation = SecurityValuationObservation(
        logical_observation_id="x",
        revision_number=1,
        symbol="2330.TW",
        metric_date="2026-01-01",
        dividend_yield_ratio=4.0,
        dividend_yield_unit="percent",
        source_name="source",
        source_dataset="dataset",
        available_at="2026-01-02T00:00:00Z",
    )
    with pytest.raises(ValueError, match="must be ratio"):
        observation.canonical_payload()
