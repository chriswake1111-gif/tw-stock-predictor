import pandas as pd
import pytest

from src.strategy.signal_driven_backtester import SignalDrivenBacktester, SignalEvent
from src.strategy.three_tranche_planner import build_three_tranche_plan


def bars():
    return pd.DataFrame({
        "date": ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"],
        "open": [100, 95, 90, 105, 110],
        "high": [101, 96, 91, 106, 111], "low": [99, 94, 89, 104, 109],
        "close": [100, 95, 90, 105, 110], "volume": [1, 2, 3, 4, 5],
    })


def approved_plan(symbol="2330", plan_id="plan-1"):
    plan = build_three_tranche_plan("900000", [
        {"stage": 1, "trigger_type": "manual_price", "value": "100"},
        {"stage": 2, "trigger_type": "user_percentage", "value": "0.05"},
        {"stage": 3, "trigger_type": "atr_multiple", "value": "2"},
    ], approval={
        "decision": "approved", "rule_id": "ENT-02", "rule_version": "2.0.0",
        "evidence_level": "A", "implementation_mode": "verified_core",
        "project_operationalization": 0, "approval_id": f"approval-{plan_id}",
        "plan_revision_id": plan_id,
    })
    plan.update({"symbol": symbol, "logical_campaign_id": plan_id, "plan_revision_id": plan_id})
    return plan


def event(event_id, campaign="plan-1", event_type="entry", signal_date="2026-03-02", stage=1):
    return SignalEvent(event_id=event_id, campaign_id=campaign, symbol="2330",
                       event_type=event_type, signal_date=signal_date, stage=stage,
                       trigger_source="approved_timeline", trigger_reference_id=f"ref-{event_id}")


def test_no_external_signals_means_no_entries_despite_price_patterns():
    result = SignalDrivenBacktester().run(bars(), {"plan-1": approved_plan()}, [])
    assert result["executions"] == []
    assert result["simulation_only"] is True
    assert result["automatic_order"] is False
    assert result["signal_source"] == "external_timeline_only"


def test_entries_execute_only_on_next_available_bar_and_use_plan_budgets():
    events = [event("e1"), event("e2", signal_date="2026-03-03", stage=2),
              event("e3", signal_date="2026-03-04", stage=3)]
    result = SignalDrivenBacktester().run(bars(), {"plan-1": approved_plan()}, events)
    assert [item["execution_date"] for item in result["executions"]] == [
        "2026-03-03", "2026-03-04", "2026-03-05"
    ]
    assert [item["capital_budget"] for item in result["executions"]] == [
        "300000.00", "300000.00", "300000.00"
    ]
    assert all(item["trigger_source"] == "approved_timeline" for item in result["executions"])


def test_stage_four_is_forbidden_and_draft_plan_is_rejected():
    with pytest.raises(ValueError, match="fourth"):
        event("e4", stage=4).validate()
    draft = approved_plan()
    draft.update({"status": "needs_human_input", "rule_trace": None})
    with pytest.raises(ValueError, match="approved"):
        SignalDrivenBacktester().run(bars(), {"plan-1": draft}, [])


def test_skip_duplicate_and_post_invalidation_entries_are_explicitly_rejected():
    events = [event("skip", stage=2), event("e1"), event("duplicate", stage=1,
              signal_date="2026-03-03"), event("stop", event_type="invalidation",
              signal_date="2026-03-04", stage=None), event("after", signal_date="2026-03-05", stage=2)]
    result = SignalDrivenBacktester().run(bars(), {"plan-1": approved_plan()}, events)
    reasons = {item["event_id"]: item["reason"] for item in result["rejected_events"]}
    assert reasons["skip"] == "stage_sequence_violation"
    assert reasons["duplicate"] == "stage_sequence_violation"
    assert reasons["after"] == "campaign_already_invalidated"
    exit_record = next(item for item in result["executions"] if item["action"] == "simulated_exit")
    assert exit_record["reason"] == "external_invalidation"
    assert exit_record["realized_pnl"] is not None


def test_campaign_state_is_isolated():
    plans = {"plan-1": approved_plan(), "plan-2": approved_plan(plan_id="plan-2")}
    events = [event("a1"), event("b1", campaign="plan-2")]
    result = SignalDrivenBacktester().run(bars(), plans, events)
    assert result["campaign_states"]["plan-1"]["next_stage"] == 2
    assert result["campaign_states"]["plan-2"]["next_stage"] == 2


def test_future_signal_does_not_execute_early():
    result = SignalDrivenBacktester().run(
        bars(), {"plan-1": approved_plan()}, [event("future", signal_date="2026-06-10")]
    )
    assert result["executions"] == []
    assert result["rejected_events"] == [{"event_id": "future", "reason": "no_next_available_bar"}]
