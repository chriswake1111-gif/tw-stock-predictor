from __future__ import annotations

from tests.test_phase13_sixth_review_remediation import (
    _add_tpex_anchor,
    _ctx,
    _instrument_payload,
    _repo,
)
from tests.test_phase13_tenth_review_remediation import _master


def test_code_reuse_selects_effective_epoch_as_of_and_never_duplicates_list(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("eleventh-epoch")
    old = _add_tpex_anchor(repo, context, "0010")
    old_master = _master(
        repo,
        db,
        anchor=old,
        context=context,
        code="0010",
        logical_key="master-0010-epoch1",
        revision_number=5,
        ingested_at="2026-08-21T00:02:00Z",
        available_at="2026-08-21T00:01:00Z",
        display_name="Epoch 1",
    )
    repo.add_lifecycle_event(
        instrument_id=old["instrument_id"],
        event_type="terminated",
        event_date="2026-08-21",
        effective_at="2026-08-21T01:00:00Z",
        available_at="2026-08-21T01:01:00Z",
        ingested_at="2026-08-21T01:01:00Z",
        source_reference="official termination",
        reason="explicit termination",
        context=context,
    )
    new = repo.allocate_instrument(
        venue="TPEX",
        official_code="0010",
        source_identity="tpex:0010:v2",
        first_observed_at="2026-08-21T02:00:00Z",
        source_reference="official reuse",
        context=context,
    )
    new_master = _master(
        repo,
        db,
        anchor=new,
        context=context,
        code="0010",
        logical_key="master-0010-epoch2",
        revision_number=1,
        ingested_at="2026-08-21T02:02:00Z",
        available_at="2026-08-21T02:01:00Z",
        display_name="Epoch 2",
    )

    before = repo.get_by_canonical("0010.TWO", knowledge_cutoff_at="2026-08-21T00:30:00Z")
    before_resolve = repo.resolve(
        official_code="0010", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:30:00Z",
    )
    after = repo.get_by_canonical("0010.TWO", knowledge_cutoff_at="2026-08-21T03:00:00Z")
    after_resolve = repo.resolve(
        official_code="0010", venue="TPEX", knowledge_cutoff_at="2026-08-21T03:00:00Z",
    )
    listed = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T03:00:00Z", venue="TPEX", limit=25,
    )

    assert before["identity_reference"]["instrument_id"] == old["instrument_id"]
    assert before["identity_reference"]["display_name"] == "Epoch 1"
    assert before_resolve["identity_reference"]["instrument_id"] == old["instrument_id"]
    assert after["identity_reference"]["instrument_id"] == new["instrument_id"]
    assert after["identity_reference"]["display_name"] == "Epoch 2"
    assert after_resolve["identity_reference"]["instrument_id"] == new["instrument_id"]
    assert [item["identity_reference"]["instrument_id"] for item in listed["items"]] == [new["instrument_id"]]
    assert listed["items"][0]["identity_reference"]["canonical_symbol"] == "0010.TWO"


def test_code_reuse_keeps_same_code_independent_across_venues(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("eleventh-cross-venue")
    tpex = _add_tpex_anchor(repo, context, "0011")
    repo.add_revision(
        instrument_id=tpex["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key="master-0011-tpex",
        revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code="0011"),
        context=context,
        idempotency_key="master-0011-tpex",
    )
    twse = repo.allocate_instrument(
        venue="TWSE",
        official_code="0011",
        source_identity="twse:0011:v1",
        first_observed_at="2026-08-21T00:00:00Z",
        source_reference="official TWSE",
        context=context,
    )
    repo.add_revision(
        instrument_id=twse["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key="master-0011-twse",
        revision_number=1,
        payload=_instrument_payload(db, venue="TWSE", code="0011"),
        context=context,
        idempotency_key="master-0011-twse",
    )

    assert repo.resolve(
        official_code="0011", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:05:00Z",
    )["identity_reference"]["canonical_symbol"] == "0011.TWO"
    assert repo.resolve(
        official_code="0011", venue="TWSE", knowledge_cutoff_at="2026-08-21T00:05:00Z",
    )["identity_reference"]["canonical_symbol"] == "0011.TW"
    items = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25,
    )["items"]
    assert {(item["identity_reference"]["venue"], item["identity_reference"]["official_code"]) for item in items} >= {
        ("TPEX", "0011"),
        ("TWSE", "0011"),
    }


def test_lifecycle_events_compose_listing_status_at_cutoff_without_changing_trading_state(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("eleventh-lifecycle")
    anchor = _add_tpex_anchor(repo, context, "0012")
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key="master-0012",
        revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code="0012"),
        context=context,
        idempotency_key="master-0012",
    )
    repo.add_lifecycle_event(
        instrument_id=anchor["instrument_id"],
        event_type="terminated",
        event_date="2026-08-21",
        available_at="2026-08-21T01:00:00Z",
        ingested_at="2026-08-21T01:00:00Z",
        source_reference="official termination",
        reason="delisted",
        context=context,
    )

    before = repo.resolve(
        official_code="0012", venue="TPEX", knowledge_cutoff_at="2026-08-21T01:30:00Z",
    )
    after = repo.resolve(
        official_code="0012", venue="TPEX", knowledge_cutoff_at="2026-08-22T00:00:00Z",
    )
    listed = repo.list_instruments(
        knowledge_cutoff_at="2026-08-22T00:00:00Z", venue="TPEX", limit=25,
    )["items"]
    filtered = repo.list_instruments(
        knowledge_cutoff_at="2026-08-22T00:00:00Z",
        venue="TPEX",
        listing_status="delisted",
        limit=25,
    )["items"]

    assert before["identity_reference"]["listing_status"] == "listed"
    assert before["identity_reference"]["trading_state"] == "unknown"
    assert after["identity_reference"]["listing_status"] == "delisted"
    assert after["identity_reference"]["trading_state"] == "unknown"
    assert after["provenance"]["lifecycle_event_type"] == "terminated"
    assert listed[0]["identity_reference"]["listing_status"] == "delisted"
    assert [item["identity_reference"]["instrument_id"] for item in filtered] == [anchor["instrument_id"]]


def test_operational_events_compose_trading_state_only_and_resume_is_source_backed(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("eleventh-operational")
    anchor = _add_tpex_anchor(repo, context, "0013")
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key="master-0013",
        revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code="0013"),
        context=context,
        idempotency_key="master-0013",
    )
    repo.add_operational_event(
        instrument_id=anchor["instrument_id"],
        trading_state="suspended",
        effective_at="2026-08-21T01:00:00Z",
        available_at="2026-08-21T01:01:00Z",
        ingested_at="2026-08-21T01:01:00Z",
        source_reference="official trading state",
        reason="suspended",
        context=context,
    )
    suspended = repo.get_by_canonical("0013.TWO", knowledge_cutoff_at="2026-08-21T02:00:00Z")
    assert suspended["identity_reference"]["listing_status"] == "listed"
    assert suspended["identity_reference"]["trading_state"] == "suspended"
    assert suspended["provenance"]["operational_event_source"] == "operational"

    repo.add_lifecycle_event(
        instrument_id=anchor["instrument_id"],
        event_type="resumed",
        effective_at="2026-08-21T03:00:00Z",
        available_at="2026-08-21T03:01:00Z",
        ingested_at="2026-08-21T03:01:00Z",
        source_reference="official resume",
        reason="resumed",
        context=context,
    )
    resumed = repo.resolve(
        official_code="0013", venue="TPEX", knowledge_cutoff_at="2026-08-21T04:00:00Z",
    )
    assert resumed["identity_reference"]["listing_status"] == "listed"
    assert resumed["identity_reference"]["trading_state"] == "normal"
    assert resumed["provenance"]["operational_event_source"] == "lifecycle"
