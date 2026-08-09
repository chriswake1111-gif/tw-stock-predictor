from dataclasses import replace

import pytest

from src.domain.screening import (
    ScreeningProfileApproval,
    ScreeningProfileRevision,
    ScreeningProfileScope,
    ScreeningRecordStatus,
    SecurityValuationObservation,
    ValuationBasis,
)
from src.domain.valuation import ApprovalStatus, ForwardEPSSourceType
from src.repositories.screening_repository import ScreeningRepository
from src.repositories.security_valuation_repository import SecurityValuationRepository


def valuation_revision(
    number=1,
    revision_of=None,
    *,
    pe=12.0,
    available_at=None,
    status=ScreeningRecordStatus.AVAILABLE,
    symbol="2330.TW",
    metric_date="2026-01-02",
):
    return SecurityValuationObservation(
        logical_observation_id="2330-2026-01-02-finmind",
        revision_number=number,
        revision_of=revision_of,
        symbol=symbol,
        metric_date=metric_date,
        pe=pe,
        pb=2.0,
        dividend_yield_ratio=0.03,
        source_name="authorized-research",
        source_dataset="daily-valuation-v1",
        available_at=available_at or f"2026-01-0{number}T10:00:00Z",
        status=status,
    )


def profile_revision(number=1, revision_of=None, **changes):
    values = {
        "logical_profile_id": "global-value-profile",
        "revision_number": number,
        "revision_of": revision_of,
        "scope": ScreeningProfileScope.GLOBAL,
        "valuation_basis": ValuationBasis.SELF_HISTORY,
        "valuation_source_name": "authorized-research",
        "valuation_source_dataset": "daily-valuation-v1",
        "pe_percentile_max": 30,
        "pb_percentile_max": 30,
        "dividend_yield_percentile_min": 70,
        "history_years": 5,
        "minimum_observations": 20,
        "forward_eps_source_name": "broker-a",
        "forward_eps_source_type": ForwardEPSSourceType.BROKER_REPORT,
        "technical_component": "wv03_confirmation",
        "available_at": f"2026-02-0{number}T10:00:00Z",
        "created_by": "reviewer",
        "rationale": "project-operated percentile research profile",
    }
    values.update(changes)
    return ScreeningProfileRevision(**values)


def approval(profile_id, approval_id, decision, approved_at):
    return ScreeningProfileApproval(
        approval_id=approval_id,
        profile_revision_id=profile_id,
        decision=decision,
        rule_id="SEL-01",
        rule_version="2.0.0",
        evidence_level="C",
        implementation_mode="project_operationalization",
        project_operationalization=True,
        approved_by="reviewer",
        rationale="reviewed research thresholds",
        approved_at=approved_at,
    )


def visible(repo, cutoff):
    return repo.observations_as_of(
        "2330.TW",
        cutoff,
        source_name="authorized-research",
        source_dataset="daily-valuation-v1",
        window_start="2021-01-01",
        window_end="2026-12-31",
    )


def test_corrected_revision_is_used_after_both_cutoffs(tmp_path):
    repo = SecurityValuationRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_observation(
        valuation_revision(), "valuation-1", ingested_at="2026-01-01T11:00:00Z"
    )
    second = repo.add_observation(
        valuation_revision(2, first["id"], pe=10.0),
        "valuation-2",
        ingested_at="2026-01-02T11:00:00Z",
    )

    assert visible(repo, "2026-01-01T12:00:00Z")[0]["id"] == first["id"]
    after = visible(repo, "2026-01-02T12:00:00Z")
    assert after[0]["id"] == second["id"]
    assert after[0]["pe"] == 10.0


def test_revoked_latest_revision_does_not_fallback(tmp_path):
    repo = SecurityValuationRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_observation(
        valuation_revision(), "valuation-1", ingested_at="2026-01-01T11:00:00Z"
    )
    repo.add_observation(
        valuation_revision(2, first["id"], status=ScreeningRecordStatus.REVOKED),
        "valuation-2",
        ingested_at="2026-01-02T11:00:00Z",
    )

    assert visible(repo, "2026-01-02T12:00:00Z") == []


def test_future_available_revision_leaves_rev1_visible(tmp_path):
    repo = SecurityValuationRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_observation(
        valuation_revision(), "valuation-1", ingested_at="2026-01-01T11:00:00Z"
    )
    repo.add_observation(
        valuation_revision(2, first["id"], available_at="2026-01-10T10:00:00Z"),
        "valuation-2",
        ingested_at="2026-01-10T11:00:00Z",
    )

    assert visible(repo, "2026-01-05T12:00:00Z")[0]["id"] == first["id"]


def test_future_ingested_revision_leaves_rev1_visible(tmp_path):
    repo = SecurityValuationRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_observation(
        valuation_revision(), "valuation-1", ingested_at="2026-01-01T11:00:00Z"
    )
    repo.add_observation(
        valuation_revision(2, first["id"], available_at="2026-01-02T10:00:00Z"),
        "valuation-2",
        ingested_at="2026-01-10T11:00:00Z",
    )

    assert visible(repo, "2026-01-05T12:00:00Z")[0]["id"] == first["id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "2317.TW"),
        ("metric_date", "2026-01-03"),
        ("source_name", "other-source"),
        ("source_dataset", "other-dataset"),
    ],
)
def test_valuation_revision_cannot_change_logical_identity(tmp_path, field, value):
    repo = SecurityValuationRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_observation(
        valuation_revision(), "valuation-1", ingested_at="2026-01-01T11:00:00Z"
    )
    changed = replace(
        valuation_revision(2, first["id"]),
        **{field: value},
    )
    with pytest.raises(ValueError, match="identity field"):
        repo.add_observation(
            changed, "valuation-2", ingested_at="2026-01-02T11:00:00Z"
        )


def test_profile_revision_requires_new_approval_and_revoke_has_no_fallback(tmp_path):
    repo = ScreeningRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_revision(
        profile_revision(), "profile-1", ingested_at="2026-02-01T11:00:00Z"
    )
    repo.add_approval(
        approval(
            first["id"], "approval-1", ApprovalStatus.APPROVED,
            "2026-02-01T12:00:00Z",
        ),
        "approve-1",
        ingested_at="2026-02-01T13:00:00Z",
    )
    assert repo.effective_profile_states_as_of("2026-02-01T14:00:00Z")[0][
        "effective_approval_status"
    ] == "approved"

    second = repo.add_revision(
        profile_revision(2, first["id"], pe_percentile_max=25),
        "profile-2",
        ingested_at="2026-02-02T11:00:00Z",
    )
    state = repo.effective_profile_states_as_of("2026-02-02T12:00:00Z")[0]
    assert state["id"] == second["id"]
    assert state["effective_approval_status"] is None

    repo.add_approval(
        approval(
            second["id"], "approval-2", ApprovalStatus.APPROVED,
            "2026-02-02T12:00:00Z",
        ),
        "approve-2",
        ingested_at="2026-02-02T13:00:00Z",
    )
    repo.add_approval(
        approval(
            second["id"], "approval-3", ApprovalStatus.REVOKED,
            "2026-02-02T14:00:00Z",
        ),
        "revoke-2",
        ingested_at="2026-02-02T15:00:00Z",
    )
    revoked = repo.effective_profile_states_as_of("2026-02-02T16:00:00Z")[0]
    assert revoked["id"] == second["id"]
    assert revoked["effective_approval_status"] == "revoked"


def test_profile_scope_is_immutable_across_revisions(tmp_path):
    repo = ScreeningRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_revision(
        profile_revision(), "profile-1", ingested_at="2026-02-01T11:00:00Z"
    )
    changed = profile_revision(
        2,
        first["id"],
        scope=ScreeningProfileScope.SYMBOL,
        scope_value="2330.TW",
    )
    with pytest.raises(ValueError, match="cannot change scope"):
        repo.add_revision(
            changed, "profile-2", ingested_at="2026-02-02T11:00:00Z"
        )
