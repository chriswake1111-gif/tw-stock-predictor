from decimal import Decimal

import pytest

from src.strategy.three_tranche_planner import build_three_tranche_plan


def triggers(count=3):
    return [
        {"stage": stage, "trigger_type": "manual_price", "value": str(100 - stage)}
        for stage in range(1, count + 1)
    ]


def approval():
    return {
        "decision": "approved", "rule_id": "ENT-02", "rule_version": "2.0.0",
        "evidence_level": "A", "implementation_mode": "verified_core",
        "project_operationalization": 0, "approval_id": "approval-1",
        "plan_revision_id": "plan-1",
    }


@pytest.mark.parametrize("capital", ["900000", "1000000", "1.00", "1234567.89"])
def test_three_budgets_sum_exactly_to_total(capital):
    plan = build_three_tranche_plan(capital, triggers(), approval=approval())
    budgets = [Decimal(entry["capital_budget"]) for entry in plan["entries"]]
    weights = [Decimal(entry["weight"]) for entry in plan["entries"]]
    assert sum(budgets) == Decimal(capital).quantize(Decimal("0.01"))
    assert sum(weights) == Decimal("1")
    assert plan["status"] == "available"
    assert [entry["stage"] for entry in plan["entries"]] == [1, 2, 3]
    assert plan["rule_trace"]["approval_id"] == "approval-1"
    assert plan["entries"][2]["remaining_entries_after_stage"] == 0
    assert plan["automatic_order"] is False
    assert plan["simulation_only"] is False


def test_missing_triggers_are_not_filled_with_legacy_rules():
    plan = build_three_tranche_plan("900000", triggers(1))
    assert plan["status"] == "needs_human_input"
    assert plan["reason"] == "deployment_triggers_required"
    assert plan["entries"][1]["trigger"] is None
    assert plan["entries"][2]["trigger"] is None
    assert plan["rule_trace"] is None


def test_complete_unapproved_plan_needs_human_input():
    plan = build_three_tranche_plan("900000", triggers())
    assert plan["reason"] == "approved_deployment_plan_required"
    assert plan["pending_rule_id"] == "ENT-02"


def test_fourth_entry_and_stage_skip_are_rejected():
    with pytest.raises(ValueError, match="fourth"):
        build_three_tranche_plan("900000", triggers() + [{"stage": 4}])
    with pytest.raises(ValueError, match="ordered"):
        build_three_tranche_plan("900000", [{"stage": 2}])


@pytest.mark.parametrize("capital", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_invalid_capital_is_rejected(capital):
    with pytest.raises(ValueError, match="finite and greater than zero"):
        build_three_tranche_plan(capital, triggers())
