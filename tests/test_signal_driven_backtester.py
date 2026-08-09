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
        {"stage": 3, "trigger_type": "atr_multiple", "value": "2",
         "reference_id": "atr-14-v1"},
    ], approval={
        "decision": "approved", "rule_id": "ENT-02", "rule_version": "2.0.0",
        "evidence_level": "A", "implementation_mode": "verified_core",
        "project_operationalization": 0, "approval_id": f"approval-{plan_id}",
        "plan_revision_id": plan_id,
    })
    plan.update({"symbol": symbol, "logical_campaign_id": plan_id, "plan_revision_id": plan_id})
    return plan


def event(event_id, campaign="plan-1", event_type="entry", signal_date="2026-03-02",
          stage=1, trigger_reference_id="default", trigger_source="approved_timeline"):
    if trigger_reference_id == "default":
        trigger_reference_id = "atr-14-v1" if stage == 3 else None
    return SignalEvent(event_id=event_id, campaign_id=campaign, symbol="2330",
                       event_type=event_type, signal_date=signal_date, stage=stage,
                       trigger_source=trigger_source,
                       trigger_reference_id=trigger_reference_id)


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
    assert all(item["signal_event_source"] == "approved_timeline" for item in result["executions"])
    assert result["executions"][2]["approved_trigger"]["reference_id"] == "atr-14-v1"


def test_stage_four_is_forbidden_and_draft_plan_is_rejected():
    with pytest.raises(ValueError, match="fourth"):
        event("e4", stage=4).validate()
    draft = approved_plan()
    draft.update({"status": "needs_human_input", "rule_trace": None})
    with pytest.raises(ValueError, match="approved"):
        SignalDrivenBacktester().run(bars(), {"plan-1": draft}, [])


def test_skip_duplicate_and_post_invalidation_entries_are_explicitly_rejected():
    events = [event("skip", stage=2, signal_date="2026-03-01"), event("e1"), event("duplicate", stage=1,
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


def test_atr_reference_mismatch_is_rejected():
    events = [event("e1"), event("e2", signal_date="2026-03-03", stage=2),
              event("e3", signal_date="2026-03-04", stage=3,
                    trigger_reference_id="atr-other")]
    result = SignalDrivenBacktester().run(bars(), {"plan-1": approved_plan()}, events)
    assert [item["stage"] for item in result["executions"]] == [1, 2]
    assert result["rejected_events"] == [
        {"event_id": "e3", "reason": "trigger_reference_mismatch"}
    ]


def test_fb04_reference_mismatch_is_rejected():
    plan = approved_plan()
    plan["entries"][2]["trigger"] = {
        "stage": 3, "trigger_type": "approved_fb04_scenario",
        "reference_id": "fb04-scenario-A", "anchor_revision_id": "anchor-2",
        "approval_id": "anchor-approval-2", "rule_id": "FB-04",
        "source_classification": "approved_rule_scenario",
    }
    events = [event("e1"), event("e2", signal_date="2026-03-03", stage=2),
              event("e3", signal_date="2026-03-04", stage=3,
                    trigger_reference_id="fb04-scenario-B")]
    result = SignalDrivenBacktester().run(bars(), {"plan-1": plan}, events)
    assert result["rejected_events"][-1]["reason"] == "trigger_reference_mismatch"


def test_execution_provenance_comes_from_plan_not_event():
    result = SignalDrivenBacktester().run(
        bars(), {"plan-1": approved_plan()},
        [event("e1", trigger_source="client_claims_fb04")],
    )
    execution = result["executions"][0]
    assert execution["signal_event_source"] == "client_claims_fb04"
    assert execution["approved_trigger"]["trigger_type"] == "manual_price"
    assert execution["approved_trigger"]["value"] == "100"
    assert "trigger_source" not in execution


def test_missing_approved_trigger_fails_closed():
    plan = approved_plan()
    plan["entries"][0]["trigger"] = None
    with pytest.raises(ValueError, match="retain their approved triggers"):
        SignalDrivenBacktester().run(bars(), {"plan-1": plan}, [event("e1")])


def test_same_time_same_campaign_is_order_independent_and_rejected():
    stage1 = event("stage-1", stage=1)
    stage2 = event("stage-2", stage=2)
    first = SignalDrivenBacktester().run(
        bars(), {"plan-1": approved_plan()}, [stage1, stage2]
    )
    reversed_input = SignalDrivenBacktester().run(
        bars(), {"plan-1": approved_plan()}, [stage2, stage1]
    )
    assert first == reversed_input
    assert first["executions"] == []
    assert [item["reason"] for item in first["rejected_events"]] == [
        "ambiguous_same_time_events", "ambiguous_same_time_events"
    ]


def test_same_time_entry_and_invalidation_are_ambiguous():
    result = SignalDrivenBacktester().run(
        bars(), {"plan-1": approved_plan()},
        [event("entry"), event("stop", event_type="invalidation", stage=None)],
    )
    assert result["executions"] == []
    assert {item["reason"] for item in result["rejected_events"]} == {
        "ambiguous_same_time_events"
    }


def test_plan_dictionary_key_must_match_logical_campaign_id():
    with pytest.raises(ValueError, match="dictionary key"):
        SignalDrivenBacktester().run(
            bars(), {"campaign-A": approved_plan(plan_id="campaign-B")}, []
        )
