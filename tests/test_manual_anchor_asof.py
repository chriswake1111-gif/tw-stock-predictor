from src.domain.technical_anchor import (
    AnchorPoint,
    AnchorRevisionStatus,
    AnchorRole,
    ManualAnchorSetRevision,
    TechnicalAnchorApproval,
)
from src.domain.valuation import ApprovalStatus
from src.repositories.technical_anchor_repository import TechnicalAnchorRepository


def anchor_revision(*, revision=1, revision_of=None, price=100, available_at="2026-02-15T00:00:00Z", status=AnchorRevisionStatus.AVAILABLE):
    return ManualAnchorSetRevision(
        logical_anchor_set_id="2330-fb04-1",
        revision_number=revision,
        revision_of=revision_of,
        symbol="2330",
        evidence_basis_rule_id="FB-04",
        anchors=(
            AnchorPoint(AnchorRole.ORIGIN, price, "2026-01-10"),
            AnchorPoint(AnchorRole.SWING_END, 150, "2026-01-20"),
        ),
        available_at=available_at,
        created_by="reviewer",
        source="manual_research",
        status=status,
    )


def approval(anchor_id, *, decision=ApprovalStatus.APPROVED, approved_at="2026-02-15T01:00:00Z", suffix="1"):
    return TechnicalAnchorApproval(
        approval_id=f"approval-{suffix}",
        anchor_revision_id=anchor_id,
        decision=decision,
        rule_id="FB-04",
        rule_version="2.0.0",
        evidence_level="A",
        implementation_mode="verified_core",
        project_operationalization=False,
        approved_by="reviewer",
        rationale="manual geometry reviewed",
        approved_at=approved_at,
    )


def test_market_date_is_not_mistaken_for_anchor_availability(tmp_path):
    repo = TechnicalAnchorRepository(str(tmp_path / "asof.db"))
    row = repo.add_anchor_revision(anchor_revision(), "anchor-1", ingested_at="2026-02-15T00:01:00Z")

    assert repo.states_as_of("2330", "2026-01-31T23:59:59Z") == []
    visible = repo.states_as_of("2330", "2026-02-16T00:00:00Z")
    assert visible[0]["id"] == row["id"]


def test_revision_is_historical_and_revoked_latest_does_not_fall_back(tmp_path):
    repo = TechnicalAnchorRepository(str(tmp_path / "revision.db"))
    first = repo.add_anchor_revision(anchor_revision(), "r1", ingested_at="2026-02-15T00:01:00Z")
    repo.add_approval(approval(first["id"]), "approve-r1", ingested_at="2026-02-15T01:01:00Z")
    second = repo.add_anchor_revision(
        anchor_revision(revision=2, revision_of=first["id"], price=105,
                        available_at="2026-03-01T00:00:00Z"),
        "r2", ingested_at="2026-03-01T00:01:00Z",
    )
    revoked = repo.add_anchor_revision(
        anchor_revision(revision=3, revision_of=second["id"], price=105,
                        available_at="2026-03-10T00:00:00Z", status=AnchorRevisionStatus.REVOKED),
        "r3", ingested_at="2026-03-10T00:01:00Z",
    )

    before = repo.states_as_of("2330", "2026-02-20T00:00:00Z")[0]
    assert before["id"] == first["id"]
    assert before["approval"]["decision"] == "approved"
    after = repo.states_as_of("2330", "2026-03-05T00:00:00Z")[0]
    assert after["id"] == second["id"]
    assert after["approval"] is None
    latest = repo.states_as_of("2330", "2026-03-11T00:00:00Z")[0]
    assert latest["id"] == revoked["id"]
    assert latest["status"] == "revoked"


def test_approval_is_visible_only_after_approved_and_ingested_times(tmp_path):
    repo = TechnicalAnchorRepository(str(tmp_path / "approval.db"))
    anchor = repo.add_anchor_revision(anchor_revision(), "anchor", ingested_at="2026-02-15T00:01:00Z")
    repo.add_approval(
        approval(anchor["id"], approved_at="2026-02-20T08:00:00Z"),
        "approval", ingested_at="2026-02-21T08:00:00Z",
    )

    assert repo.states_as_of("2330", "2026-02-20T12:00:00Z")[0]["approval"] is None
    assert repo.states_as_of("2330", "2026-02-22T00:00:00Z")[0]["approval"]["decision"] == "approved"


def test_revoked_approval_and_backdated_event_fail_closed(tmp_path):
    repo = TechnicalAnchorRepository(str(tmp_path / "revoked-approval.db"))
    anchor = repo.add_anchor_revision(anchor_revision(), "anchor", ingested_at="2026-02-15T00:01:00Z")
    repo.add_approval(approval(anchor["id"]), "approve", ingested_at="2026-02-15T01:01:00Z")
    repo.add_approval(
        approval(anchor["id"], decision=ApprovalStatus.REVOKED,
                 approved_at="2026-02-16T01:00:00Z", suffix="2"),
        "revoke", ingested_at="2026-02-16T01:01:00Z",
    )
    state = repo.states_as_of("2330", "2026-02-17T00:00:00Z")[0]
    assert state["approval"]["decision"] == "revoked"

    try:
        repo.add_approval(
            approval(anchor["id"], approved_at="2026-02-15T00:30:00Z", suffix="3"),
            "backdated", ingested_at="2026-02-17T01:00:00Z",
        )
    except ValueError as exc:
        assert "backdated" in str(exc)
    else:
        raise AssertionError("backdated approval event was accepted")


def test_symbol_isolation(tmp_path):
    repo = TechnicalAnchorRepository(str(tmp_path / "symbol.db"))
    repo.add_anchor_revision(anchor_revision(), "anchor", ingested_at="2026-02-15T00:01:00Z")
    assert repo.states_as_of("2317", "2026-02-16T00:00:00Z") == []
