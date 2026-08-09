from copy import deepcopy

from src.engine.target_confluence import TargetConfluenceEngine
from src.services.evidence_analysis_service import EvidenceAnalysisService


def profile():
    return {
        "id": "profile-rev-1",
        "verified_approval_id": "tgt-approval-1",
        "allowed_method_families": ["VAL-01", "FB-03", "FB-04"],
        "overlap_tolerance": "0.05",
        "calculation_quantum": "0.0001",
        "evidence_strength_policy": [
            {"minimum_independent_target_components": 2, "label": "moderate"},
            {"minimum_independent_target_components": 3, "label": "high"},
        ],
    }


def trace():
    return {
        "rule_id": "TGT-01",
        "rule_version": "2.0.0",
        "evidence_level": "C",
        "implementation_mode": "project_operationalization",
        "project_operationalization": True,
        "approval_ids": ["tgt-approval-1"],
    }


def candidate(
    candidate_id,
    family,
    price,
    dependency,
    *,
    role="target",
):
    return {
        "candidate_id": candidate_id,
        "method_family": family,
        "rule_id": family,
        "rule_version": "2.0.0",
        "evidence_level": "A",
        "implementation_mode": "verified_core",
        "semantic_role": role,
        "price": str(price),
        "price_unit": "TWD_per_share",
        "source_resource_ids": [dependency],
        "dependency_keys": [dependency],
        "approval_ids": [f"approval-{candidate_id}"],
        "source_data_as_of": "2026-06-01T00:00:00Z",
    }


def evaluate(candidates):
    return TargetConfluenceEngine().evaluate(
        candidates=candidates, profile=profile(), rule_trace=trace()
    )


def test_single_family_matrix_cells_have_no_tgt01_strength():
    result = evaluate([
        candidate("val-1", "VAL-01", 100, "eps-1"),
        candidate("val-2", "VAL-01", 101, "eps-2"),
        candidate("val-3", "VAL-01", 102, "eps-3"),
    ])
    assert result["status"] == "insufficient_data"
    assert result["support_count"] == 0
    assert result["independent_method_count"] == 0
    assert result["evidence_strength"] is None
    assert result["rules_used"] == []
    assert result["rule_trace"] is None


def test_target_support_and_independence_guardrails():
    result = evaluate([
        candidate("val-1", "VAL-01", 100, "eps-1"),
        candidate("val-2", "VAL-01", 101, "eps-2"),
        candidate("fb03", "FB-03", 100, "anchor-target"),
        candidate("fb04", "FB-04", 100, "anchor-support", role="support"),
    ])
    cluster = result["overlap_ranges"][0]
    assert cluster["candidate_count"] == 3
    assert cluster["support_count"] == 2
    assert cluster["independent_method_count"] == 2
    assert cluster["evidence_strength"] == "moderate"
    assert result["support_count"] == 2
    assert result["cross_role_alignment"][0]["support_candidate_id"] == "fb04"
    assert result["rules_used"][0]["rule_id"] == "TGT-01"


def test_shared_dependency_collapses_different_target_families():
    result = evaluate([
        candidate("val", "VAL-01", 100, "shared-resource"),
        candidate("fb03", "FB-03", 100, "shared-resource"),
    ])
    assert result["overlap_ranges"] == []
    assert result["evidence_strength"] is None
    assert result["rules_used"] == []
    assert result["dependency_collapsed_candidates"][0]["shared_dependencies"] == [
        "shared-resource"
    ]


def test_all_disjoint_clusters_are_retained_and_summary_selects_no_target():
    result = evaluate([
        candidate("v-low", "VAL-01", 100, "eps-low"),
        candidate("f-low", "FB-03", 100, "anchor-low"),
        candidate("v-high", "VAL-01", 200, "eps-high"),
        candidate("f-high", "FB-03", 200, "anchor-high"),
    ])
    assert len(result["overlap_ranges"]) == 2
    assert result["summary_policy"] == "maximum_cluster_strength"
    assert result["max_independent_method_count"] == 2
    assert "recommended_cluster" not in result
    assert "primary_target" not in result


def test_input_order_and_duplicate_candidate_are_invariant():
    values = [
        candidate("val", "VAL-01", 100, "eps"),
        candidate("fb", "FB-03", 100, "anchor"),
    ]
    baseline = evaluate(values)
    reordered = evaluate([deepcopy(values[1]), deepcopy(values[0]), deepcopy(values[0])])
    assert baseline == reordered


def test_trusted_adapters_fix_phase2_and_phase4_roles(tmp_path):
    service = EvidenceAnalysisService(str(tmp_path / "adapter.db"))
    candidates = service.candidates_from_outputs(
        valuation={
            "status": "available",
            "target_matrix": [{
                "status": "available",
                "target_price": 100.0,
                "observation_id": "eps-1",
                "eps_scenario": "base",
                "pe_scenario_id": "pe-1",
                "approval_ids": {"VAL-02": "eps-approval", "VAL-04": "pe-approval"},
            }],
        },
        technical_support={
            "status": "available",
            "scenarios": [
                {
                    "anchor_set_revision_id": "anchor-target",
                    "calculated_level": 100.0,
                    "price_unit": "TWD_per_share",
                    "rule_trace": {"rule_id": "FB-03", "approval_id": "fb03-approval"},
                },
                {
                    "anchor_set_revision_id": "anchor-support",
                    "calculated_level": 95.0,
                    "price_unit": "TWD_per_share",
                    "rule_trace": {"rule_id": "FB-04", "approval_id": "fb04-approval"},
                },
            ],
        },
        knowledge_cutoff_at="2026-06-01T00:00:00Z",
    )
    roles = {item["method_family"]: item["semantic_role"] for item in candidates}
    assert roles == {"VAL-01": "target", "FB-03": "target", "FB-04": "support"}
    assert candidates[0]["approval_ids"] == ["eps-approval", "pe-approval"]
