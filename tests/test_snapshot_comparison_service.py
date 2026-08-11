import sqlite3

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.services.snapshot_comparison_service import SnapshotComparisonService


def add_snapshot(repo, *, suffix, symbol="2330.TW", status="partial"):
    minute = 1 if suffix == "base" else 2
    return repo.add(
        AnalysisSnapshot(
            symbol=symbol,
            knowledge_cutoff_at="2026-08-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={"VAL-01": "2.0.0"},
            source_resource_versions=[],
            manual_approval_ids=[],
            output={
                "status": status,
                "data_quality": {"status": status},
                "valuation": {"status": status, "target_matrix": []},
                "liquidity": {"status": "insufficient_data"},
                "technical_support": {"status": "needs_human_input", "scenarios": []},
                "target_confluence": {"status": "insufficient_data", "overlap_ranges": []},
                "deployment_plan": {"status": "needs_human_input", "plans": []},
                "screening": {"status": "insufficient_data", "research_result": None},
                "marker": suffix,
            },
            created_at=f"2026-08-01T00:{minute:02d}:00Z",
        ),
        f"comparison-{suffix}",
    )


def table_counts(db_path):
    protected = (
        "analysis_snapshots", "valuation_approvals", "technical_anchor_approvals",
        "deployment_plan_approvals", "synthesis_profile_approvals",
        "screening_profile_approvals", "ingestion_runs", "scenario_evaluations",
        "schema_migrations",
    )
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in protected
        }


def test_service_comparison_is_deterministic_read_only_and_has_no_public_hash(tmp_path):
    db_path = str(tmp_path / "service.db")
    repo = AnalysisSnapshotRepository(db_path)
    base = add_snapshot(repo, suffix="base")
    comparison = add_snapshot(repo, suffix="comparison", status="available")
    before = table_counts(db_path)
    service = SnapshotComparisonService(db_path)
    first = service.compare(
        base_snapshot_id=base["snapshot_id"],
        comparison_snapshot_id=comparison["snapshot_id"],
        comparison_cutoff="2026-08-02T08:00:00+08:00",
    )
    second = service.compare(
        base_snapshot_id=base["snapshot_id"],
        comparison_snapshot_id=comparison["snapshot_id"],
        comparison_cutoff="2026-08-02T00:00:00Z",
    )
    assert first == second
    assert first["comparison_cutoff"] == "2026-08-02T00:00:00Z"
    assert first["comparison_policy_version"] == "1.0"
    assert first["comparison_snapshot_contract"] == "analysis_snapshot_v1"
    assert first["direction"]["absolute_delta_formula"] == "comparison_minus_base"
    assert "comparison_result_sha256" not in str(first)
    assert table_counts(db_path) == before


def test_same_snapshot_has_zero_stored_and_current_context_deltas(tmp_path):
    db_path = str(tmp_path / "same.db")
    stored = add_snapshot(AnalysisSnapshotRepository(db_path), suffix="same")
    result = SnapshotComparisonService(db_path).compare(
        base_snapshot_id=stored["snapshot_id"],
        comparison_snapshot_id=stored["snapshot_id"],
        comparison_cutoff="2026-08-02T00:00:00Z",
    )
    assert result["stored_deltas"] == []
    assert result["current_context_deltas"] == []
    assert result["base_current_context"]["comparison_cutoff"] == result["comparison_current_context"]["comparison_cutoff"]


def test_incompatible_contract_returns_no_deltas_or_current_context(tmp_path):
    db_path = str(tmp_path / "incompatible.db")
    repo = AnalysisSnapshotRepository(db_path)
    base = add_snapshot(repo, suffix="base")
    comparison = add_snapshot(repo, suffix="other", symbol="2317.TW")
    result = SnapshotComparisonService(db_path).compare(
        base_snapshot_id=base["snapshot_id"],
        comparison_snapshot_id=comparison["snapshot_id"],
        comparison_cutoff="2026-08-02T00:00:00Z",
    )
    assert result["status"] == "incomparable_contract"
    assert result["compatibility"] == {"compatible": False, "reasons": ["different_symbol"]}
    assert result["stored_deltas"] == []
    assert result["base_current_context"] is None


def test_current_context_taxonomy_is_neutral_and_explicit():
    service = object.__new__(SnapshotComparisonService)
    base = {
        "freshness_status": "current",
        "checked_dependencies": [{
            "section": "valuation", "resource_type": "forward_eps_revision",
            "logical_resource_id": "eps-series", "status": "current",
            "candidate_awaiting_review": False,
            "effective_approval_status": "approved",
        }],
    }
    comparison = {
        "freshness_status": "blocked",
        "checked_dependencies": [{
            "section": "valuation", "resource_type": "forward_eps_revision",
            "logical_resource_id": "eps-series", "status": "blocked",
            "candidate_awaiting_review": True,
            "effective_approval_status": "revoked",
        }],
    }
    types = [item["change_type"] for item in service._compare_contexts(base, comparison)]
    assert "approval_revoked" in types
    assert "dependency_blocked" in types
    assert "approval_eligibility_changed" in types
    assert not any(word in str(types) for word in ("better", "worse", "bullish", "bearish"))
