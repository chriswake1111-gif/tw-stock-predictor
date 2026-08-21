from __future__ import annotations

import hashlib
import sqlite3

import pytest

from src.collectors.universe_collectors import (
    UniverseCollector,
    UniverseSourceRejected,
    parse_universe_payload,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import (
    UniverseRawProvenanceRequired,
    UniverseRepository,
)
from src.services.evidence_backup_service import EvidenceBackupService
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import raw_for, seed_raw_provenance


def _ctx(label: str) -> UniverseOperatorContext:
    return UniverseOperatorContext("operator", f"run-{label}", f"lock-{label}", f"audit-{label}")


def _repo(tmp_path):
    db = tmp_path / "phase13-third.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(True))


def _instrument_payload(**overrides):
    value = {
        "venue": "TWSE",
        "official_code": "2330",
        "canonical_symbol": "2330.TW",
        "fetched_at": "2026-08-21T00:01:00Z",
        "received_at": "2026-08-21T00:01:00Z",
        "ingested_at": "2026-08-21T00:02:00Z",
        "available_at": "2026-08-21T00:01:00Z",
        "source_reference": "fixture",
        "status": "accepted",
        "freshness_status": "current",
        "freshness_mode": "official_cadence_window",
        "current_complete": True,
        "coverage_complete": True,
    }
    value.update(overrides)
    return value


def _resource_payload(db, **overrides):
    raw_id, raw_hash = raw_for(db, "twse-universe-master")
    value = {
        "fetched_at": "2026-08-21T00:03:00Z",
        "received_at": "2026-08-21T00:03:00Z",
        "ingested_at": "2026-08-21T00:04:00Z",
        "available_at": "2026-08-21T00:03:00Z",
        "source_reference": "fixture-resource",
        "status": "provider_error",
        "reason": "provider unavailable",
        "freshness_status": "blocked",
        "freshness_mode": "event_observation",
        "current_complete": False,
        "coverage_complete": False,
        "venue": "TWSE",
        "raw_resource_revision_id": raw_id,
        "raw_payload_sha256": raw_hash,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("failure_status", ["provider_error", "partial", "schema_changed"])
def test_latest_failure_without_available_at_is_visible_only_after_ingested_cutoff(tmp_path, failure_status):
    db, repo = _repo(tmp_path)
    context = _ctx(f"dual-{failure_status}")
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    first = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(
            raw_resource_revision_id=raw_for(db, "twse-universe-master")[0],
            raw_payload_sha256=raw_for(db, "twse-universe-master")[1],
        ),
        context=context, idempotency_key=f"first-{failure_status}",
    )
    second = repo.add_resource_revision(
        resource_id="twse-universe-master", logical_revision_key="master", revision_number=2,
            payload=_resource_payload(db,
            status=failure_status,
            reason=f"{failure_status} fixture",
            available_at=None,
            ingested_at="2026-08-21T00:04:00Z",
            supersedes_revision_id=first["universe_revision_id"],
        ),
        context=context, idempotency_key=f"second-{failure_status}",
    )

    before = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:03:30Z")
    assert before["provenance"]["operational_revision_id"] == first["universe_revision_id"]
    assert before["status"] == "available"

    after = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert after["identity_reference"]["instrument_id"] == anchor["instrument_id"]
    assert after["provenance"]["universe_revision_id"] == first["universe_revision_id"]
    assert after["provenance"]["operational_revision_id"] == second["universe_revision_id"]
    assert after["operational_freshness"]["latest_visible_state"] == failure_status
    assert after["status"] == ("needs_human_input" if failure_status == "schema_changed" else "partial")


def test_zero_row_latest_failure_is_visible_in_list_health_without_identity_fallback(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("list-zero-row")
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(
            raw_resource_revision_id=raw_for(db, "twse-universe-master")[0],
            raw_payload_sha256=raw_for(db, "twse-universe-master")[1],
        ),
        context=context, idempotency_key="list-s1",
    )
    operational_raw_id, operational_raw_hash = raw_for(db, "tpex-universe-operational")
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.spendi.history",
        revision_number=1,
            payload=_resource_payload(db,
            raw_resource_revision_id=operational_raw_id,
            raw_payload_sha256=operational_raw_hash,
            venue="TPEX", status="provider_error", available_at=None,
            reason="TPEx provider unavailable",
        ),
        context=context, idempotency_key="list-tpex-failure",
    )
    result = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert result["items"]
    assert result["per_venue_status"]["TPEX"] == "partial"
    assert result["status"] == "partial"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda repo, anchor, context: repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
            logical_revision_key="master", revision_number=1,
            payload=_instrument_payload(raw_payload_sha256="a" * 64),
            context=context, idempotency_key="missing-raw-id",
        ),
        lambda repo, anchor, context: repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
            logical_revision_key="master", revision_number=1,
            payload=_instrument_payload(
                raw_resource_revision_id="raw-does-not-exist",
                raw_payload_sha256="a" * 64,
            ),
            context=context, idempotency_key="unknown-raw-id",
        ),
    ],
)
def test_raw_provenance_missing_or_unknown_fails_before_universe_mutation(tmp_path, mutation):
    db, repo = _repo(tmp_path)
    context = _ctx("raw-required")
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    with pytest.raises(UniverseRawProvenanceRequired):
        mutation(repo, anchor, context)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_revisions").fetchone()[0] == 0


def test_raw_provenance_wrong_resource_or_hash_fails_and_valid_hash_only_round_trips(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("raw-roundtrip")
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    tpex_raw_id, tpex_raw_hash = raw_for(db, "tpex-universe-master")
    with pytest.raises(ValueError, match="raw_resource_revision_mismatch"):
        repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
            logical_revision_key="master", revision_number=1,
            payload=_instrument_payload(raw_resource_revision_id=tpex_raw_id, raw_payload_sha256=tpex_raw_hash),
            context=context, idempotency_key="wrong-resource",
        )
    valid_raw_id, valid_raw_hash = raw_for(db, "twse-universe-master")
    with pytest.raises(ValueError, match="raw_payload_sha256_mismatch"):
        repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
            logical_revision_key="master", revision_number=1,
            payload=_instrument_payload(raw_resource_revision_id=valid_raw_id, raw_payload_sha256="b" * 64),
            context=context, idempotency_key="wrong-hash",
        )
    created = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(raw_resource_revision_id=valid_raw_id, raw_payload_sha256=valid_raw_hash),
        context=context, idempotency_key="valid-raw",
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT raw_resource_revision_id FROM universe_revisions WHERE universe_revision_id=?",
            (created["universe_revision_id"],),
        ).fetchone()[0] == valid_raw_id
    backup = tmp_path / "backup" / "phase13.db"
    restored = tmp_path / "restored" / "phase13.db"
    EvidenceBackupService.backup(str(db), str(backup))
    EvidenceBackupService.restore(str(backup), str(restored))
    with sqlite3.connect(restored) as conn:
        assert conn.execute(
            "SELECT raw_resource_revision_id FROM universe_revisions WHERE universe_revision_id=?",
            (created["universe_revision_id"],),
        ).fetchone()[0] == valid_raw_id


def test_schema_fingerprint_uses_observed_envelope_and_fields_and_legacy_key_is_not_new_ingestion():
    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    valid = UniverseCollector(
        lambda *_args, **_kwargs: Response([{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電"}])
    ).fetch_official("twse.t187ap03_L", url="https://www.twse.com.tw/fixture")
    changed = UniverseCollector(
        lambda *_args, **_kwargs: Response([{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電", "industry": "semiconductor"}])
    ).fetch_official("twse.t187ap03_L", url="https://www.twse.com.tw/fixture", schema_fingerprint="caller-cannot-authorize-drift")
    assert valid.status == "accepted"
    assert changed.status == "schema_changed"
    assert valid.query_dimensions["source_envelope"] == "array"
    assert "公司代號" in valid.query_dimensions["observed_row_fields"]
    assert valid.query_dimensions["expected_schema_fingerprint"]
    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload("twse.t187ap03_L", [{"出表日期": "1150820", "公司代號": "2330", "symbol": "fixture"}])
    with pytest.raises(UniverseSourceRejected, match="legacy_source_contract_not_for_ingestion"):
        parse_universe_payload("tpex.spendi.cmode", [{"Date": "1150821"}])


def test_collector_schema_evidence_round_trips_into_universe_revision(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("schema-roundtrip")
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )

    class Response:
        status_code = 200

        def json(self):
            return [{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電"}]

    fetched = UniverseCollector(lambda *_args, **_kwargs: Response()).fetch_official(
        "twse.t187ap03_L", url="https://www.twse.com.tw/fixture",
    )
    raw_id, raw_hash = raw_for(db, "twse-universe-master")
    created = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(
            raw_resource_revision_id=raw_id, raw_payload_sha256=raw_hash,
            schema_fingerprint=fetched.schema_fingerprint,
            parser_version=fetched.parser_version,
            query_dimensions=fetched.query_dimensions,
            source_contract_key=fetched.resource_key,
        ),
        context=context, idempotency_key="schema-roundtrip",
    )
    assert created["schema_fingerprint"] == fetched.schema_fingerprint
