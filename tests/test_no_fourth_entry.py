import pandas as pd
import pytest

from src.strategy.signal_driven_backtester import SignalDrivenBacktester, SignalEvent
from src.strategy.three_tranche_planner import build_three_tranche_plan


def test_planner_and_backtester_both_forbid_stage_four():
    with pytest.raises(ValueError, match="fourth"):
        build_three_tranche_plan("900000", [
            {"stage": 1}, {"stage": 2}, {"stage": 3}, {"stage": 4},
        ])
    plan = build_three_tranche_plan("900000", [
        {"stage": 1}, {"stage": 2}, {"stage": 3},
    ], approval={
        "decision": "approved", "rule_id": "ENT-02", "rule_version": "2.0.0",
        "evidence_level": "A", "implementation_mode": "verified_core",
        "project_operationalization": 0, "approval_id": "approval", "plan_revision_id": "campaign",
    })
    plan.update({"symbol": "2330"})
    bars = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "open": [100, 101]})
    stage_four = SignalEvent("four", "campaign", "2330", "entry", "2026-01-01", stage=4)
    with pytest.raises(ValueError, match="fourth"):
        SignalDrivenBacktester().run(bars, {"campaign": plan}, [stage_four])
