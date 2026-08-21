from __future__ import annotations

import hashlib
import sqlite3

import pytest

from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseIdempotencyConflict, UniverseRepository
from src.services.universe_write_guard import UniverseIngestionWritesDisabled, UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import seed_raw_provenance


def _repo(tmp_path, enabled=True):
    db = tmp_path / "universe.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(enabled))


def _anchor(repo, context):
    return repo.allocate_instrument(venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
                                    first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context)


def _context(suffix="default"):
    return UniverseOperatorContext(
        "operator", f"run-{suffix}", f"lock-{suffix}", f"audit-{suffix}"
    )


def _payload(**overrides):
    value = {"venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
             "fetched_at": "2026-08-21T00:01:00Z", "received_at": "2026-08-21T00:01:00Z",
             "ingested_at": "2026-08-21T00:02:00Z", "available_at": "2026-08-21T00:01:00Z",
             "source_reference": "fixture", "status": "accepted", "freshness_status": "current", "freshness_mode": "official_cadence_window",
             "current_complete": True, "coverage_complete": True,
             "raw_resource_revision_id": "raw-phase13-twse_universe_master",
             "raw_payload_sha256": hashlib.sha256(b"phase13:twse-universe-master").hexdigest()}
    value.update(overrides)
    return value


def test_write_guard_default_disabled_has_zero_mutation(tmp_path):
    db, repo = _repo(tmp_path, enabled=False)
    before = db.read_bytes()
    with pytest.raises(UniverseIngestionWritesDisabled):
        _anchor(repo, _context("disabled"))
    assert db.read_bytes() == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_instruments").fetchone()[0] == 0


def test_identity_retry_cross_venue_and_code_reuse_epoch(tmp_path):
    db, repo = _repo(tmp_path)
    context = _context("identity")
    first = _anchor(repo, context)
    retry = _anchor(repo, context)
    assert retry["instrument_id"] == first["instrument_id"]
    other = repo.allocate_instrument(venue="TPEX", official_code="2330", source_identity="tpex:2330:v1",
                                      first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context)
    assert other["instrument_id"] != first["instrument_id"]
    repo.add_lifecycle_event(instrument_id=first["instrument_id"], event_type="terminated",
                             available_at="2026-08-21T01:00:00Z", ingested_at="2026-08-21T01:00:00Z",
                             source_reference="official", reason="termination", context=context)
    reused = repo.allocate_instrument(venue="TWSE", official_code="2330", source_identity="twse:2330:v2",
                                      first_observed_at="2026-08-22T00:00:00Z", source_reference="fixture", context=context)
    assert reused["identity_epoch"] == 2
    assert repo.allocate_instrument(venue="TWSE", official_code="2330", source_identity="new",
                                   first_observed_at="2026-08-23T00:00:00Z", source_reference="fixture",
                                   context=context, continuity_proven=True)["instrument_id"] == reused["instrument_id"]


def test_idempotency_keys_bind_without_duplicate_and_conflict_before_mutation(tmp_path):
    db, repo = _repo(tmp_path)
    context = _context("idempotency")
    anchor = _anchor(repo, context)
    first = repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
                              logical_revision_key="master", revision_number=1, payload=_payload(), context=context,
                              idempotency_key="key-a")
    retry = repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
                              logical_revision_key="master", revision_number=1, payload=_payload(), context=context,
                              idempotency_key="key-a")
    key_b = repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
                              logical_revision_key="master", revision_number=1, payload=_payload(), context=context,
                              idempotency_key="key-b")
    assert retry["universe_revision_id"] == first["universe_revision_id"] == key_b["universe_revision_id"]
    with pytest.raises(UniverseIdempotencyConflict):
        repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
                          logical_revision_key="master", revision_number=2, payload=_payload(status="partial", reason="partial"),
                          context=context, idempotency_key="key-b")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_revisions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM universe_ingestion_idempotency").fetchone()[0] == 2


def test_current_latest_block_does_not_fallback_and_dto_has_no_sensitive_fields(tmp_path):
    _, repo = _repo(tmp_path)
    context = _context("latest")
    anchor = _anchor(repo, context)
    first = repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
                              revision_number=1, payload=_payload(), context=context, idempotency_key="one")
    repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
                      revision_number=2, payload=_payload(status="revoked", reason="revoked", available_at="2026-08-22T00:00:00Z", ingested_at="2026-08-22T00:01:00Z", current_complete=False, freshness_status="blocked", supersedes_revision_id=first["universe_revision_id"]), context=context, idempotency_key="two")
    result = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-23T00:00:00Z", current=True)
    assert result["status"] == "needs_human_input"
    assert result["operational_freshness"]["latest_visible_state"] == "revoked"
    text = str(result)
    assert "idempotency_key" not in text and "payload_fingerprint" not in text and "payload_sha256" not in text


def test_resource_scope_and_partial_membership_fail_closed(tmp_path):
    _, repo = _repo(tmp_path)
    context = _context("scope")
    anchor = _anchor(repo, context)
    with pytest.raises(ValueError, match="universe_resource_not_registered"):
        repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="non-universe-resource",
                          logical_revision_key="master", revision_number=1, payload=_payload(), context=context,
                          idempotency_key="invalid-resource")
    partial = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_payload(status="partial", reason="provider_partial", listing_status="listed",
                         current_complete=True), context=context, idempotency_key="partial",
    )
    assert partial["status"] == "partial"
    current = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:03:00Z", current=True)
    assert current["status"] == "partial"
    assert current["identity_reference"]["listing_status"] == "unknown"
