import pytest

from src.domain.analysis_snapshot import (
    SynthesisProfileApproval,
    SynthesisProfileRevision,
    SynthesisProfileScope,
)
from src.domain.valuation import ApprovalStatus
from src.repositories.synthesis_profile_repository import SynthesisProfileRepository


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
