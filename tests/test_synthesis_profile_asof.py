from dataclasses import replace

import pytest

from src.domain.analysis_snapshot import (
    SynthesisProfileApproval,
    SynthesisProfileRevision,
    SynthesisProfileScope,
)
from src.domain.valuation import ApprovalStatus
from src.repositories.synthesis_profile_repository import SynthesisProfileRepository
from src.services.evidence_analysis_service import EvidenceAnalysisService


def profile(revision=1, revision_of=None, status="available"):
    from src.domain.analysis_snapshot import SynthesisRecordStatus

    return SynthesisProfileRevision(
        logical_profile_id="default-target-profile",
        revision_number=revision,
        revision_of=revision_of,
        scope=SynthesisProfileScope.GLOBAL,
        allowed_method_families=("VAL-01", "FB-03", "FB-04"),
        overlap_tolerance="0.05",
        evidence_strength_policy=(
            {"minimum_independent_target_components": 2, "label": "moderate"},
            {"minimum_independent_target_components": 3, "label": "high"},
        ),
        available_at=f"2026-0{revision}-01T00:00:00Z",
        created_by="admin",
        rationale="reviewed synthesis policy",
        status=SynthesisRecordStatus(status),
    )


def approval(profile_id, decision=ApprovalStatus.APPROVED, at="2026-01-02T00:00:00Z"):
    return SynthesisProfileApproval(
        approval_id=f"approval-{profile_id}-{decision.value}-{at}",
        profile_revision_id=profile_id,
        decision=decision,
        rule_id="TGT-01",
        rule_version="2.0.0",
        evidence_level="C",
        implementation_mode="project_operationalization",
        project_operationalization=True,
        approved_by="admin",
        rationale="reviewed TGT-01 policy",
        approved_at=at,
    )


def test_profile_revision_requires_new_approval_and_no_old_fallback(tmp_path):
    repo = SynthesisProfileRepository(str(tmp_path / "profiles.db"))
    rev1 = repo.add_revision(
        profile(), "profile-1", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_approval(
        approval(rev1["id"]), "approval-1", ingested_at="2026-01-02T00:00:00Z"
    )
    rev2 = repo.add_revision(
        profile(2, rev1["id"]), "profile-2", ingested_at="2026-02-01T00:00:00Z"
    )
    state = repo.effective_states_as_of("2026-02-02T00:00:00Z")[0]
    assert state["id"] == rev2["id"]
    assert state["effective_approval_status"] is None


def test_latest_profile_approval_revoke_has_no_fallback(tmp_path):
    repo = SynthesisProfileRepository(str(tmp_path / "revoke.db"))
    rev1 = repo.add_revision(
        profile(), "profile", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_approval(
        approval(rev1["id"]), "approve", ingested_at="2026-01-02T00:00:00Z"
    )
    repo.add_approval(
        approval(
            rev1["id"], ApprovalStatus.REVOKED, "2026-01-03T00:00:00Z"
        ),
        "revoke",
        ingested_at="2026-01-03T00:00:00Z",
    )
    state = repo.effective_states_as_of("2026-01-04T00:00:00Z")[0]
    assert state["effective_approval_status"] == "revoked"


def test_single_method_strength_threshold_is_rejected():
    invalid = SynthesisProfileRevision(
        logical_profile_id="invalid",
        revision_number=1,
        scope=SynthesisProfileScope.GLOBAL,
        allowed_method_families=("VAL-01",),
        overlap_tolerance="0.05",
        evidence_strength_policy=(
            {"minimum_independent_target_components": 1, "label": "low"},
        ),
        available_at="2026-01-01T00:00:00Z",
        created_by="admin",
        rationale="invalid policy",
    )
    with pytest.raises(ValueError, match="start at two"):
        invalid.canonical_payload()


def test_evidence_strength_labels_must_increase_with_thresholds():
    invalid = replace(
        profile(),
        evidence_strength_policy=(
            {"minimum_independent_target_components": 2, "label": "high"},
            {"minimum_independent_target_components": 3, "label": "moderate"},
        ),
    )
    with pytest.raises(ValueError, match="labels must increase"):
        invalid.canonical_payload()


def test_profile_selectors_are_mutually_exclusive_at_service_boundary(tmp_path):
    service = EvidenceAnalysisService(str(tmp_path / "selector-conflict.db"))
    with pytest.raises(
        ValueError, match="synthesis_profile_selectors_are_mutually_exclusive"
    ):
        service.select_profile(
            "2330.TW",
            "2026-06-01T00:00:00Z",
            logical_profile_id="profile-a",
            profile_revision_id="profile-b-revision",
        )


def test_future_available_revision_is_not_visible_and_does_not_hide_rev1(tmp_path):
    db_path = tmp_path / "future-available.db"
    repo = SynthesisProfileRepository(str(db_path))
    rev1 = repo.add_revision(
        profile(), "rev1", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_approval(
        approval(rev1["id"]), "approve-rev1", ingested_at="2026-01-02T00:00:00Z"
    )
    rev2 = repo.add_revision(
        profile(2, rev1["id"]), "rev2", ingested_at="2026-02-01T00:00:00Z"
    )
    service = EvidenceAnalysisService(str(db_path))
    selected, hidden = service.select_profile(
        "2330.TW",
        "2026-01-15T00:00:00Z",
        profile_revision_id=rev2["id"],
    )
    assert selected is None
    assert hidden == {
        "status": "needs_human_input",
        "reason": "synthesis_profile_revision_not_visible_at_cutoff",
    }
    historical, error = service.select_profile(
        "2330.TW",
        "2026-01-15T00:00:00Z",
        profile_revision_id=rev1["id"],
    )
    assert error is None
    assert historical["id"] == rev1["id"]


def test_future_ingested_revision_is_not_visible_and_does_not_hide_rev1(tmp_path):
    db_path = tmp_path / "future-ingested.db"
    repo = SynthesisProfileRepository(str(db_path))
    rev1 = repo.add_revision(
        profile(), "rev1", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_approval(
        approval(rev1["id"]), "approve-rev1", ingested_at="2026-01-02T00:00:00Z"
    )
    rev2 = repo.add_revision(
        profile(2, rev1["id"]), "rev2", ingested_at="2026-09-01T00:00:00Z"
    )
    service = EvidenceAnalysisService(str(db_path))
    selected, hidden = service.select_profile(
        "2330.TW",
        "2026-06-01T00:00:00Z",
        profile_revision_id=rev2["id"],
    )
    assert selected is None
    assert hidden == {
        "status": "needs_human_input",
        "reason": "synthesis_profile_revision_not_visible_at_cutoff",
    }
    historical, error = service.select_profile(
        "2330.TW",
        "2026-06-01T00:00:00Z",
        profile_revision_id=rev1["id"],
    )
    assert error is None
    assert historical["id"] == rev1["id"]


def test_explicit_profile_cannot_resurrect_superseded_revision(tmp_path):
    db_path = tmp_path / "superseded.db"
    repo = SynthesisProfileRepository(str(db_path))
    rev1 = repo.add_revision(
        profile(), "rev1", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_approval(
        approval(rev1["id"]), "rev1-approval", ingested_at="2026-01-02T00:00:00Z"
    )
    rev2 = repo.add_revision(
        profile(2, rev1["id"]), "rev2", ingested_at="2026-02-01T00:00:00Z"
    )
    selected, error = EvidenceAnalysisService(str(db_path)).select_profile(
        "2330.TW",
        "2026-02-02T00:00:00Z",
        profile_revision_id=rev1["id"],
    )
    assert selected is None
    assert error["reason"] == "synthesis_profile_revision_superseded"
    assert error["effective_profile_revision_id"] == rev2["id"]


def test_multiple_applicable_profiles_require_explicit_selection(tmp_path):
    db_path = tmp_path / "multiple.db"
    repo = SynthesisProfileRepository(str(db_path))
    first = repo.add_revision(
        profile(), "first", ingested_at="2026-01-01T00:00:00Z"
    )
    second_profile = replace(profile(), logical_profile_id="second-profile")
    second = repo.add_revision(
        second_profile, "second", ingested_at="2026-01-01T00:00:00Z"
    )
    for index, item in enumerate((first, second), start=1):
        repo.add_approval(
            approval(item["id"]),
            f"approval-{index}",
            ingested_at="2026-01-02T00:00:00Z",
        )
    selected, error = EvidenceAnalysisService(str(db_path)).select_profile(
        "2330.TW", "2026-01-03T00:00:00Z"
    )
    assert selected is None
    assert error["reason"] == "synthesis_profile_selection_required"


def test_explicit_old_profile_cannot_bypass_revoked_latest_revision(tmp_path):
    db_path = tmp_path / "revoked-latest.db"
    repo = SynthesisProfileRepository(str(db_path))
    rev1 = repo.add_revision(
        profile(), "rev1", ingested_at="2026-01-01T00:00:00Z"
    )
    repo.add_approval(
        approval(rev1["id"]), "approve-rev1", ingested_at="2026-01-02T00:00:00Z"
    )
    rev2 = repo.add_revision(
        profile(2, rev1["id"], status="revoked"),
        "rev2-revoked",
        ingested_at="2026-02-01T00:00:00Z",
    )
    selected, error = EvidenceAnalysisService(str(db_path)).select_profile(
        "2330.TW",
        "2026-02-02T00:00:00Z",
        profile_revision_id=rev1["id"],
    )
    assert selected is None
    assert error["reason"] == "synthesis_profile_revision_superseded"
    assert error["effective_profile_revision_id"] == rev2["id"]
