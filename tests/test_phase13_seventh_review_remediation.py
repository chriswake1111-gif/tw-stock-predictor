from __future__ import annotations

import pytest

import src.repositories.universe_repository as universe_repository_module
from src.repositories.universe_repository import UniverseRepository
from tests.test_phase13_sixth_review_remediation import (
    _add_tpex_anchor,
    _ctx,
    _instrument_payload,
    _repo,
    _resource_payload,
)


def _add_master(repo: UniverseRepository, db, *, code: str = "6488", key: str = "master"):
    context = _ctx(key)
    anchor = _add_tpex_anchor(repo, context, code)
    revision = repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key=f"master-{code}",
        revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code=code),
        context=context,
        idempotency_key=key,
    )
    return context, anchor, revision


def test_operational_event_cannot_store_or_establish_master_completeness(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("event-completeness")
    created = repo.add_resource_revision(
        resource_id="tpex-universe-operational",
        logical_revision_key="cmode",
        revision_number=1,
        payload=_resource_payload(
            db,
            "tpex-universe-operational",
            venue="TPEX",
            status="accepted",
            ingested_at="2026-08-21T00:03:00Z",
            current_complete=True,
            coverage_complete=True,
        ),
        context=context,
        idempotency_key="event-completeness",
    )

    assert created["current_complete"] == 0
    result = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:04:00Z", venue="TPEX", limit=25,
    )
    assert result["per_venue_status"]["TPEX"] == "insufficient_data"


def test_eligible_master_plus_accepted_event_is_available(tmp_path):
    db, repo = _repo(tmp_path)
    context, _, _ = _add_master(repo, db, code="6488", key="master-with-event")
    event = repo.add_resource_revision(
        resource_id="tpex-universe-operational",
        logical_revision_key="cmode",
        revision_number=1,
        payload=_resource_payload(
            db,
            "tpex-universe-operational",
            venue="TPEX",
            status="accepted",
            ingested_at="2026-08-21T00:03:00Z",
            current_complete=True,
        ),
        context=context,
        idempotency_key="accepted-event",
    )

    assert event["current_complete"] == 0
    result = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:04:00Z", venue="TPEX", limit=25,
    )
    assert result["per_venue_status"]["TPEX"] == "available"


@pytest.mark.parametrize(
    ("event_status", "expected"),
    (("provider_error", "partial"), ("schema_changed", "needs_human_input"),
     ("awaiting_review", "needs_human_input"), ("revoked", "needs_human_input")),
)
def test_event_blocker_changes_health_without_removing_safe_master_identity(
    tmp_path, event_status: str, expected: str,
):
    db, repo = _repo(tmp_path)
    context, _, _ = _add_master(repo, db, code="6490", key=f"master-{event_status}")
    repo.add_resource_revision(
        resource_id="tpex-universe-operational",
        logical_revision_key="cmode",
        revision_number=1,
        payload=_resource_payload(
            db,
            "tpex-universe-operational",
            venue="TPEX",
            status=event_status,
            ingested_at="2026-08-21T00:03:00Z",
        ),
        context=context,
        idempotency_key=f"event-{event_status}",
    )

    result = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:04:00Z", venue="TPEX", limit=25,
    )
    assert result["per_venue_status"]["TPEX"] == expected
    assert result["items"][0]["identity_reference"]["official_code"] == "6490"


def test_optional_corroborating_source_is_neutral_without_explicit_dependency():
    assert UniverseRepository._resource_status_from_row(
        {
            "resource_role": "corroborating_identity_observation",
            "status": "accepted",
            "available_at": "2026-08-21T00:01:00Z",
            "freshness_status": "current",
            "current_complete": True,
        },
        cutoff="2026-08-21T00:02:00Z",
    ) is None
    assert UniverseRepository._resource_status_from_row(
        {
            "resource_role": "corroborating_identity_observation",
            "status": "awaiting_review",
            "available_at": None,
            "freshness_status": "blocked",
            "current_complete": False,
        },
        cutoff="2026-08-21T00:02:00Z",
    ) is None


def test_revision_sequence_precedes_timestamps_across_exact_resolve_and_list(tmp_path):
    db, repo = _repo(tmp_path)
    context, anchor, first = _add_master(repo, db, code="6488", key="ordering-first")
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key="master-6488",
        revision_number=2,
        payload=_instrument_payload(
            db,
            venue="TPEX",
            code="6488",
            display_name="Higher authoritative revision",
            available_at="2026-08-20T00:01:00Z",
            ingested_at="2026-08-20T00:02:00Z",
            supersedes_revision_id=first["universe_revision_id"],
        ),
        context=context,
        idempotency_key="ordering-second",
    )
    cutoff = "2026-08-21T00:05:00Z"

    exact = repo.get_by_instrument_id(anchor["instrument_id"], knowledge_cutoff_at=cutoff)
    resolved = repo.resolve(official_code="6488", venue="TPEX", knowledge_cutoff_at=cutoff)
    listed = repo.list_instruments(knowledge_cutoff_at=cutoff, venue="TPEX", limit=25)

    assert exact["identity_reference"]["display_name"] == "Higher authoritative revision"
    assert resolved["identity_reference"]["display_name"] == "Higher authoritative revision"
    assert listed["items"][0]["identity_reference"]["display_name"] == "Higher authoritative revision"


def test_immutable_revision_id_breaks_full_ordering_ties_deterministically(tmp_path, monkeypatch):
    db, repo = _repo(tmp_path)
    context = _ctx("tie-break")

    class _Uuid:
        def __init__(self, value: str):
            self.hex = value

    values = iter(("1" * 32, "f" * 32))
    monkeypatch.setattr(universe_repository_module.uuid, "uuid4", lambda: _Uuid(next(values)))
    lower = repo.add_resource_revision(
        resource_id="tpex-universe-operational",
        logical_revision_key="tie-lower-id",
        revision_number=1,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status="accepted",
            ingested_at="2026-08-21T00:02:00Z",
        ),
        context=context,
        idempotency_key="tie-first",
    )
    higher = repo.add_resource_revision(
        resource_id="tpex-universe-operational",
        logical_revision_key="tie-higher-id",
        revision_number=1,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status="schema_changed",
            ingested_at="2026-08-21T00:02:00Z",
        ),
        context=context,
        idempotency_key="tie-second",
    )

    assert repo._select_latest([lower, higher])["universe_revision_id"] == higher["universe_revision_id"]
    listed = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TPEX", limit=25,
    )
    assert listed["per_venue_status"]["TPEX"] == "needs_human_input"
