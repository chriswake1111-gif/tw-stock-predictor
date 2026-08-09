import hashlib
import sqlite3

import pytest

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.performance_validation import (
    EVALUATOR_VERSION,
    EvaluationOrigin,
    EvaluationProfileApproval,
    EvaluationProfileRevision,
    EvaluationRun,
    EvaluationSubjectType,
    OutcomeResourceManifest,
    ScenarioEvaluation,
)
from src.domain.valuation import ApprovalStatus
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.performance_validation_repository import PerformanceValidationRepository


HASH_A = hashlib.sha256(b"a").hexdigest()
HASH_B = hashlib.sha256(b"b").hexdigest()


def profile(*, revision=1, previous=None):
    return EvaluationProfileRevision(
        logical_profile_id="phase8-mvp",
        revision_number=revision,
        previous_revision_id=previous,
        horizons_sessions=(20, 60),
        available_at="2026-01-01T00:00:00Z",
        created_by="admin",
        rationale="approved constrained research profile",
    )


def approval(profile_id, decision, approved_at, approval_id):
    return EvaluationProfileApproval(
        approval_id=approval_id,
        profile_revision_id=profile_id,
        decision=decision,
        approved_by="server-admin",
        rationale="controlled Phase 8 profile decision",
        approved_at=approved_at,
    )


def manifest():
    return OutcomeResourceManifest(
        manifest_id="phase2-fixed-manifest",
        manifest_version=1,
        dataset_name="phase2-gap-adjusted-v1",
        provider="hybrid immutable adjusted parent + TWSE official raw gap rows",
        dataset_hash=HASH_A,
        date_start="2014-01-02",
        date_end="2026-07-31",
        universe_definition="phase2_fixed_14_symbol_research_cohort",
        calendar_resource={"path": "calendar.csv"},
        calendar_hash=HASH_B,
        benchmark_resource={"symbol": "^TWII", "path": "index-TWII.csv"},
        benchmark_hash=HASH_B,
        ohlc_adjustment_contract="provider_compatible_adjusted_v1",
        corporate_action_contract="fixed_snapshot_adjustment_provenance",
        symbol_resources=({"symbol": "2330.TW", "path": "2330-TW.csv", "sha256": HASH_A, "quality_status": "available"},),
        ingested_at="2026-08-10T00:00:00Z",
        created_at="2026-08-10T00:00:00Z",
    )


def add_snapshot(db_path):
    return AnalysisSnapshotRepository(str(db_path)).add(
        AnalysisSnapshot(
            symbol="2330.TW",
            knowledge_cutoff_at="2025-01-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={"VAL-01": "2.0.1"},
            source_resource_versions=[],
            manual_approval_ids=[],
            output={"status": "available", "target_confluence": {}},
            created_at="2026-08-10T00:00:00Z",
        ),
        "snapshot-key",
    )


def evaluation(snapshot_id):
    return ScenarioEvaluation(
        snapshot_id=snapshot_id,
        symbol="2330.TW",
        evaluation_origin=EvaluationOrigin.HISTORICAL_RECONSTRUCTION,
        subject_type=EvaluationSubjectType.TARGET_CANDIDATE,
        subject_id="VAL-01:test",
        method_family="VAL-01",
        semantic_role="target",
        subject_metadata={"candidate_ids": ["VAL-01:test"]},
        knowledge_cutoff_at="2025-01-01T00:00:00Z",
        horizon_sessions=20,
        terminal_outcome="expired",
        quality_status="available",
        benchmark_status="available",
        outcome_resource_manifest_id="phase2-fixed-manifest",
        created_at="2026-08-10T00:00:00Z",
        evaluation_start_session="2025-01-02",
        evaluation_end_session="2025-01-30",
        start_price="100",
        target_low="120",
        target_high="125",
        target_position_at_start="above_start",
        target_reached=False,
        maximum_upside_excursion="0.1",
        maximum_downside_excursion="-0.05",
        directional_mfe="0.1",
        directional_mae="-0.05",
        forward_return="0.03",
        benchmark_return="0.02",
        excess_return="0.01",
    )


def test_profile_approval_is_cutoff_safe_and_latest_revoke_does_not_fall_back(tmp_path):
    repo = PerformanceValidationRepository(str(tmp_path / "profile.db"))
    stored = repo.add_profile_revision(
        profile(), "profile-key", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_profile_approval(
        approval(stored["id"], ApprovalStatus.APPROVED, "2026-01-02T00:00:00Z", "approval-1"),
        "approval-key",
        ingested_at="2026-01-02T00:00:00Z",
    )
    assert repo.approved_profile_as_of(stored["id"], "2026-01-01T12:00:00Z")["verified_approval_id"] is None
    assert repo.approved_profile_as_of(stored["id"], "2026-01-02T12:00:00Z")["verified_approval_id"] == "approval-1"
    repo.add_profile_approval(
        approval(stored["id"], ApprovalStatus.REVOKED, "2026-01-03T00:00:00Z", "revoke-1"),
        "revoke-key",
        ingested_at="2026-01-03T00:00:00Z",
    )
    revoked = repo.approved_profile_as_of(stored["id"], "2026-01-04T00:00:00Z")
    assert revoked["effective_approval_status"] == "revoked"
    assert revoked["verified_approval_id"] is None


def test_manifest_and_run_are_permanently_idempotent(tmp_path):
    db_path = tmp_path / "run.db"
    repo = PerformanceValidationRepository(str(db_path))
    stored_profile = repo.add_profile_revision(profile(), "profile-key")
    stored_manifest = repo.add_manifest(manifest(), "manifest-a")
    assert repo.add_manifest(manifest(), "manifest-b")["created"] is False
    snapshot = add_snapshot(db_path)
    run = EvaluationRun(
        evaluation_profile_revision_id=stored_profile["id"],
        evaluator_version=EVALUATOR_VERSION,
        evaluation_origin_policy="separate_by_evaluation_origin",
        snapshot_set_hash=HASH_B,
        outcome_resource_manifest_id=stored_manifest["manifest_id"],
        outcome_manifest_hash=stored_manifest["dataset_hash"],
        universe_definition="phase2_fixed_14_symbol_research_cohort",
        created_at="2026-08-10T00:00:00Z",
    )
    first = repo.add_run(run, [evaluation(snapshot["snapshot_id"])], "run-a")
    second = repo.add_run(run, [evaluation(snapshot["snapshot_id"])], "run-b")
    assert first["created"] is True
    assert second["created"] is False
    assert second["evaluation_run_id"] == first["evaluation_run_id"]
    assert repo.get_run(first["evaluation_run_id"])["results"][0]["target_reached"] is False
    changed = EvaluationRun(**{**run.__dict__, "evaluator_version": "phase8_historical_scenario_v2"})
    with pytest.raises(ValueError, match="different payload"):
        repo.add_run(changed, [evaluation(snapshot["snapshot_id"])], "run-b")
    newer = repo.add_run(changed, [evaluation(snapshot["snapshot_id"])], "run-c")
    assert newer["evaluation_run_id"] != first["evaluation_run_id"]


def test_phase8_persisted_resources_are_database_immutable(tmp_path):
    db_path = tmp_path / "immutable.db"
    repo = PerformanceValidationRepository(str(db_path))
    stored_profile = repo.add_profile_revision(profile(), "profile-key")
    stored_manifest = repo.add_manifest(manifest(), "manifest-key")
    snapshot = add_snapshot(db_path)
    run = EvaluationRun(
        evaluation_profile_revision_id=stored_profile["id"], evaluator_version=EVALUATOR_VERSION,
        evaluation_origin_policy="separate_by_evaluation_origin", snapshot_set_hash=HASH_B,
        outcome_resource_manifest_id=stored_manifest["manifest_id"],
        outcome_manifest_hash=stored_manifest["dataset_hash"],
        universe_definition="phase2_fixed_14_symbol_research_cohort",
        created_at="2026-08-10T00:00:00Z",
    )
    created = repo.add_run(run, [evaluation(snapshot["snapshot_id"])], "run-key")
    evaluation_id = created["results"][0]["evaluation_id"]
    with sqlite3.connect(db_path) as conn:
        for sql, value in (
            ("UPDATE evaluation_runs SET status='tampered' WHERE evaluation_run_id=?", created["evaluation_run_id"]),
            ("DELETE FROM evaluation_runs WHERE evaluation_run_id=?", created["evaluation_run_id"]),
            ("UPDATE scenario_evaluations SET quality_status='tampered' WHERE evaluation_id=?", evaluation_id),
            ("DELETE FROM scenario_evaluations WHERE evaluation_id=?", evaluation_id),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(sql, (value,))
