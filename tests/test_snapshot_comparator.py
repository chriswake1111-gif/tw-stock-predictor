from copy import deepcopy

from src.domain.snapshot_comparison import canonical_value
from src.engine.snapshot_comparator import (
    SnapshotComparator,
    compatibility_reason,
    supports_snapshot_contract,
)


def snapshot(snapshot_id="base"):
    return {
        "snapshot_id": snapshot_id,
        "symbol": "2330.TW",
        "model_version": "2.0.0",
        "capture_mode": "historical_reconstruction",
        "knowledge_cutoff_at": "2026-08-01T00:00:00Z",
        "output_sha256": f"sha-{snapshot_id}",
        "synthesis_profile_revision_id": "profile-1",
        "synthesis_profile_approval_id": "profile-approval-1",
        "used_rule_versions": {"VAL-01": "2.0.0"},
        "source_resource_versions": [{
            "section": "valuation",
            "resource_type": "forward_eps_revision",
            "resource_id": "eps-r1",
            "logical_resource_id": "eps-series",
            "revision_number": 1,
            "approval_ids": ["eps-approval-1"],
        }],
        "output": {
            "symbol": "2330.TW",
            "knowledge_cutoff_at": "2026-08-01T00:00:00Z",
            "model": {"version": "2.0.0"},
            "data_quality": {"status": "available"},
            "valuation": {"status": "available", "target_matrix": [{
                "observation_id": "eps-r1", "pe_scenario_id": "pe-r1",
                "observation_logical_series_id": "eps-series",
                "pe_logical_series_id": "pe-series",
                "eps_scenario": "base", "fiscal_year": 2026, "target_price": 150,
            }]},
            "liquidity": {"status": "available", "turnover_m1b_ratio_pct": 10.0},
            "technical_support": {"status": "available", "scenarios": [{
                "scenario_type": "equal_amplitude", "semantic_role": "target",
                "calculated_level": 180, "anchor_set_revision_id": "anchor-r1",
                "rule_trace": {"rule_id": "FB-03", "approval_id": "anchor-a1"},
            }]},
            "target_confluence": {"status": "available", "overlap_ranges": [{
                "cluster_id": "cluster-1", "price_low": "145", "price_high": "155",
            }]},
            "deployment_plan": {"status": "available", "plans": [{
                "logical_campaign_id": "campaign-1", "plan_revision_id": "plan-r1",
                "entries": [{"stage": 1, "weight": "0.3333"}],
            }]},
            "screening": {"status": "available", "research_result": "meets_approved_profile"},
        },
    }


def change_types(deltas):
    return [item["change_type"] for item in deltas]


def test_same_snapshot_has_zero_deltas():
    item = snapshot()
    assert SnapshotComparator().compare(item, deepcopy(item)) == []


def test_resource_revision_approval_profile_and_rule_changes_are_explicit():
    base = snapshot()
    after = deepcopy(base)
    after["source_resource_versions"][0].update({
        "resource_id": "eps-r2", "revision_number": 2,
        "approval_ids": ["eps-approval-2"],
    })
    after["synthesis_profile_revision_id"] = "profile-2"
    after["synthesis_profile_approval_id"] = "profile-approval-2"
    after["used_rule_versions"]["VAL-01"] = "2.1.0"
    types = change_types(SnapshotComparator().compare(base, after))
    assert "resource_revision_changed" in types
    assert "approval_reference_changed" in types
    assert "profile_revision_changed" in types
    assert "rule_version_reference_changed" in types


def test_valuation_target_support_screening_liquidity_confluence_and_deployment():
    base = snapshot()
    after = deepcopy(base)
    after["output"]["valuation"]["target_matrix"][0]["target_price"] = 160
    after["output"]["technical_support"]["scenarios"][0]["calculated_level"] = 190
    after["output"]["technical_support"]["scenarios"].append({
        "scenario_type": "retracement_0382", "semantic_role": "support",
        "calculated_level": 120, "anchor_set_revision_id": "anchor-r2",
        "rule_trace": {"rule_id": "FB-04", "approval_id": "anchor-a2"},
    })
    after["output"]["screening"]["research_result"] = "does_not_meet_approved_profile"
    after["output"]["liquidity"]["turnover_m1b_ratio_pct"] = 11
    after["output"]["target_confluence"]["overlap_ranges"][0]["price_high"] = "160"
    after["output"]["target_confluence"]["overlap_ranges"].append({
        "cluster_id": "cluster-2", "price_low": "170", "price_high": "180",
    })
    after["output"]["deployment_plan"]["plans"][0]["plan_revision_id"] = "plan-r2"
    types = change_types(SnapshotComparator().compare(base, after))
    assert "valuation_range_changed" in types
    assert "target_range_changed" in types
    assert "support_range_changed" in types
    assert "screening_result_changed" in types
    assert "liquidity_context_changed" in types
    assert "confluence_cluster_changed" in types
    assert "confluence_cluster_added" in types
    assert "deployment_scenario_changed" in types


def test_duplicate_technical_identity_is_exact_and_does_not_invent_lineage():
    base = snapshot()
    duplicate = deepcopy(base["output"]["technical_support"]["scenarios"][0])
    duplicate["anchor_set_revision_id"] = "anchor-other"
    base["output"]["technical_support"]["scenarios"].append(duplicate)
    after = deepcopy(base)
    after["output"]["technical_support"]["scenarios"].reverse()
    assert SnapshotComparator().compare(base, after) == []
    after["output"]["technical_support"]["scenarios"][0]["calculated_level"] = 999
    deltas = SnapshotComparator().compare(base, after)
    assert len([item for item in deltas if item["change_type"] == "target_range_changed"]) == 2


def test_valuation_revision_ids_do_not_change_logical_cell():
    base = snapshot()
    after = deepcopy(base)
    cell = after["output"]["valuation"]["target_matrix"][0]
    cell.update({"observation_id": "eps-r2", "pe_scenario_id": "pe-r2"})
    assert "valuation_range_changed" not in change_types(
        SnapshotComparator().compare(base, after)
    )
    cell["target_price"] = 820
    assert "valuation_range_changed" in change_types(
        SnapshotComparator().compare(base, after)
    )


def test_technical_anchor_and_price_changes_have_separate_taxonomy():
    base = snapshot()
    anchor_only = deepcopy(base)
    anchor_only["output"]["technical_support"]["scenarios"][0]["anchor_set_revision_id"] = "anchor-r2"
    types = change_types(SnapshotComparator().compare(base, anchor_only))
    assert "technical_anchor_changed" in types
    assert "target_range_changed" not in types

    price_only = deepcopy(base)
    price_only["output"]["technical_support"]["scenarios"][0]["calculated_level"] = 190
    types = change_types(SnapshotComparator().compare(base, price_only))
    assert "technical_anchor_changed" not in types
    assert "target_range_changed" in types

    both = deepcopy(anchor_only)
    both["output"]["technical_support"]["scenarios"][0]["calculated_level"] = 190
    types = change_types(SnapshotComparator().compare(base, both))
    assert "technical_anchor_changed" in types
    assert "target_range_changed" in types


def test_set_semantics_deduplicate_scalars_and_nested_values():
    assert canonical_value(["B", "A", "A"], value_kind="set") == ["A", "B"]
    nested = [{"b": [2, 1], "a": 1}, {"a": 1, "b": [2, 1]}]
    assert canonical_value(nested, value_kind="set") == [{"a": 1, "b": [2, 1]}]
    assert canonical_value(list(reversed(nested)), value_kind="set") == canonical_value(
        nested, value_kind="set"
    )


def test_missing_and_null_are_distinct_and_input_order_does_not_change_output():
    base = snapshot()
    after = deepcopy(base)
    base["output"]["screening"].pop("reason", None)
    after["output"]["screening"]["reason"] = None
    delta = next(item for item in SnapshotComparator().compare(base, after) if item["field_path"] == "screening.reason")
    assert delta["before"] == {"state": "missing"}
    assert delta["after"] is None
    after["source_resource_versions"].append({
        "section": "liquidity", "resource_type": "m1b_revision",
        "resource_id": "m1", "logical_resource_id": "2026-07",
        "revision_number": 1, "approval_ids": [],
    })
    first = SnapshotComparator().compare(base, after)
    after["source_resource_versions"].reverse()
    assert SnapshotComparator().compare(base, after) == first


def test_compatibility_gate_reasons_are_deterministic():
    base = snapshot()
    other = deepcopy(base)
    assert compatibility_reason(base, other, "2026-08-01T00:00:00Z") is None
    other["symbol"] = "2317.TW"
    other["output"]["symbol"] = "2317.TW"
    assert compatibility_reason(base, other, "2026-08-01T00:00:00Z") == "different_symbol"
    other = deepcopy(base); other["model_version"] = "3.0.0"; other["output"]["model"]["version"] = "3.0.0"
    assert compatibility_reason(base, other, "2026-08-01T00:00:00Z") == "different_model_version"
    other = deepcopy(base); other["capture_mode"] = "live_refresh"
    assert compatibility_reason(base, other, "2026-08-01T00:00:00Z") == "different_capture_mode"
    assert compatibility_reason(base, base, "2026-07-31T23:59:59Z") == "comparison_cutoff_precedes_snapshot_cutoff"


def test_snapshot_contract_validation_is_fail_closed():
    base = snapshot()
    for mutate in (
        lambda item: item["output"].pop("model"),
        lambda item: item["output"].update(symbol="2317.TW"),
        lambda item: item["output"]["model"].update(version="3.0.0"),
        lambda item: item["output"].update(technical_support=[]),
    ):
        malformed = deepcopy(base)
        mutate(malformed)
        assert compatibility_reason(
            base, malformed, "2026-08-01T00:00:00Z"
        ) == "unsupported_comparison_snapshot_contract"


def test_snapshot_contract_accepts_authoritative_capture_modes_only():
    historical = snapshot()
    assert supports_snapshot_contract(historical)

    live = deepcopy(historical)
    live["capture_mode"] = "live_refresh"
    assert supports_snapshot_contract(live)

    unsupported_base = deepcopy(historical)
    unsupported_comparison = deepcopy(historical)
    unsupported_base["capture_mode"] = "legacy_mode"
    unsupported_comparison["capture_mode"] = "legacy_mode"
    assert compatibility_reason(
        unsupported_base,
        unsupported_comparison,
        "2026-08-01T00:00:00Z",
    ) == "unsupported_comparison_snapshot_contract"
