from datetime import date, timedelta
from hashlib import sha256

from src.domain.performance_validation import OutcomeResourceManifest
from src.engine.historical_scenario_evaluator import HistoricalScenarioEvaluator
from src.services.fixed_outcome_dataset import OutcomeBar, VerifiedOutcomeDataset


HASH = sha256(b"fixed").hexdigest()


def sessions(count=80):
    values = []
    current = date(2025, 1, 1)
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def dataset(*, symbol="2330.TW", warning=False, missing=None, benchmark_missing=None, count=80):
    dates = sessions(count)
    missing = set(missing or [])
    benchmark_missing = set(benchmark_missing or [])
    bars = tuple(
        OutcomeBar(day, 100, 121 if index == 5 else 102, 80 if index == 7 else 99, 101)
        for index, day in enumerate(dates) if day not in missing
    )
    benchmark = tuple(
        OutcomeBar(day, 1000, 1010, 990, 1005)
        for day in dates if day not in benchmark_missing
    )
    manifest = OutcomeResourceManifest(
        manifest_id="fixed-manifest", manifest_version=1, dataset_name="fixed",
        provider="test", dataset_hash=HASH, date_start=dates[0], date_end=dates[-1],
        universe_definition="phase2_fixed_14_symbol_research_cohort",
        calendar_resource={"policy": "fixed"}, calendar_hash=HASH,
        benchmark_resource={"symbol": "^TWII"}, benchmark_hash=HASH,
        ohlc_adjustment_contract="provider_compatible_adjusted_v1",
        corporate_action_contract="frozen", symbol_resources=({
            "symbol": symbol, "path": "fixed.csv", "sha256": HASH,
            "quality_status": "quality_warning" if warning else "available",
        },), ingested_at="2026-08-10T00:00:00Z", created_at="2026-08-10T00:00:00Z",
    )
    return VerifiedOutcomeDataset(manifest, tuple(dates), {symbol: bars}, benchmark)


def snapshot(*, cutoff="2025-01-01T02:30:00Z", capture_mode="historical_reconstruction"):
    return {
        "snapshot_id": "snapshot-1", "symbol": "2330.TW",
        "knowledge_cutoff_at": cutoff, "capture_mode": capture_mode,
        "output": {"target_confluence": {
            "supporting_methods": [
                {"candidate_id": "val", "method_family": "VAL-01", "rule_id": "VAL-01",
                 "rule_version": "2", "semantic_role": "target", "price_low": "120",
                 "price_high": "125", "source_resource_ids": ["eps"], "approval_ids": ["a"]},
                {"candidate_id": "fb03", "method_family": "FB-03", "rule_id": "FB-03",
                 "rule_version": "2", "semantic_role": "target", "price_low": "80",
                 "price_high": "80", "source_resource_ids": ["anchor"], "approval_ids": ["b"]},
                {"candidate_id": "fb04", "method_family": "FB-04", "rule_id": "FB-04",
                 "rule_version": "2", "semantic_role": "support", "price_low": "95",
                 "price_high": "98", "source_resource_ids": ["anchor2"], "approval_ids": ["c"]},
            ],
            "overlap_ranges": [{
                "cluster_id": "cluster", "price_low": "120", "price_high": "125",
                "candidate_ids": ["val", "fb03"], "target_method_families": ["VAL-01", "FB-03"],
                "shared_dependencies": [], "independent_method_count": 2,
                "evidence_strength": "moderate",
            }],
        }},
    }


PROFILE = {
    "verified_approval_id": "profile-approval", "horizons_sessions": [20, 60],
    "calculation_quantum": "0.00000001",
}


def evaluate(data=None, snap=None):
    return HistoricalScenarioEvaluator().evaluate(
        snapshot=snap or snapshot(), profile=PROFILE, dataset=data or dataset(),
        created_at="2026-08-10T00:00:00Z",
    )


def find(results, subject_id, horizon=20):
    return next(item for item in results if item.subject_id == subject_id and item.horizon_sessions == horizon)


def test_timing_is_strictly_after_cutoff_local_date_and_origin_is_preserved():
    result = find(evaluate(), "val")
    assert result.evaluation_start_session == "2025-01-02"
    assert result.evaluation_end_session == sessions()[21]
    assert result.market_sessions_skipped == 0
    assert result.evaluation_origin.value == "historical_reconstruction"
    prospective = find(evaluate(snap=snapshot(capture_mode="live_refresh")), "val")
    assert prospective.evaluation_origin.value == "prospective_snapshot"


def test_closed_boundary_target_touch_and_directional_excursions():
    results = evaluate()
    above = find(results, "val")
    below = find(results, "fb03")
    assert above.target_reached is True
    assert above.first_target_reached_at == sessions()[5]
    assert above.trading_sessions_to_target == 4
    assert above.directional_mfe == "0.21"
    assert above.directional_mae == "-0.2"
    assert below.target_reached is True
    assert below.target_position_at_start == "below_start"
    assert below.directional_mfe == "0.2"
    assert below.directional_mae == "-0.21"


def test_fb04_remains_support_context_and_never_becomes_target_hit():
    support = find(evaluate(), "fb04")
    assert support.semantic_role == "support"
    assert support.terminal_outcome == "not_applicable"
    assert support.target_reached is None
    assert support.subject_metadata["support_breached"] is True


def test_missing_symbol_bar_is_insufficient_and_short_dataset_is_pending():
    dates = sessions()
    missing = find(evaluate(dataset(missing={dates[10]})), "val")
    assert missing.terminal_outcome == "insufficient_future_data"
    pending = find(evaluate(dataset(count=15)), "val")
    assert pending.terminal_outcome == "pending"
    assert pending.target_reached is None


def test_missing_exact_benchmark_endpoint_does_not_use_nearest_date():
    end = sessions()[21]
    result = find(evaluate(dataset(benchmark_missing={end})), "val")
    assert result.benchmark_status == "insufficient_data"
    assert result.benchmark_return is None
    assert result.excess_return is None


def test_already_in_range_is_not_a_future_target_hit():
    snap = snapshot()
    snap["output"]["target_confluence"]["supporting_methods"][0]["price_low"] = "99"
    snap["output"]["target_confluence"]["supporting_methods"][0]["price_high"] = "101"
    result = find(evaluate(snap=snap), "val")
    assert result.terminal_outcome == "already_in_range"
    assert result.target_reached is False
    assert result.first_target_reached_at is None


def test_quality_warning_preserves_observation_without_counting_a_model_miss_or_hit():
    result = find(evaluate(dataset(warning=True)), "val")
    assert result.quality_status == "quality_warning"
    assert result.terminal_outcome == "quality_warning"
    assert result.target_reached is None
    assert result.subject_metadata["observed_outcome_excluded_from_rate"] == "target_reached"


def test_evaluator_never_calls_current_model_or_approval_services(monkeypatch):
    from src.services.evidence_analysis_service import EvidenceAnalysisService
    from src.services.forward_eps_service import ForwardEPSService
    from src.services.security_screening_service import SecurityScreeningService
    from src.services.technical_scenario_service import TechnicalScenarioService

    def forbidden(*args, **kwargs):
        raise AssertionError("Phase 8 attempted to rebuild current model state")

    monkeypatch.setattr(EvidenceAnalysisService, "synthesize", forbidden)
    monkeypatch.setattr(ForwardEPSService, "analyze", forbidden)
    monkeypatch.setattr(SecurityScreeningService, "analyze", forbidden)
    monkeypatch.setattr(TechnicalScenarioService, "analyze", forbidden)
    first = [item.canonical_payload() for item in evaluate()]
    second = [item.canonical_payload() for item in evaluate()]
    assert first == second


def test_first_missing_symbol_bar_is_skipped_but_horizon_uses_frozen_calendar():
    dates = sessions()
    result = find(evaluate(dataset(missing={dates[1]})), "val")
    assert result.evaluation_start_session == dates[2]
    assert result.market_sessions_skipped == 1
    assert result.evaluation_end_session == dates[22]


def test_exact_target_boundaries_are_closed_and_weekend_cutoff_never_uses_same_day():
    snap = snapshot(cutoff="2025-01-04T04:00:00Z")  # Saturday in Asia/Taipei
    snap["output"]["target_confluence"]["supporting_methods"][0]["price_low"] = "121"
    snap["output"]["target_confluence"]["supporting_methods"][0]["price_high"] = "130"
    result = find(evaluate(snap=snap), "val")
    assert result.evaluation_start_session == "2025-01-06"
    assert result.target_reached is True


def test_adjusted_price_series_is_used_without_runtime_corporate_action_rewrite():
    result = find(evaluate(), "val")
    assert result.start_price == "100"
    assert result.forward_return == "0.01"
    assert result.maximum_upside_excursion == "0.21"
