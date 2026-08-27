from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from src.domain.data_foundation import (
    DataHealthStatus,
    EligibilityStatus,
    RawResourceRevision,
    StoragePolicy,
)
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.eod_close_repository import EodCloseRepository, EodEvidenceConflict
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository
from src.services.evidence_backup_service import EvidenceBackupService
from src.services.eod_close_service import EodCloseService
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import raw_for, seed_raw_provenance


def _ctx(name: str) -> UniverseOperatorContext:
    return UniverseOperatorContext("operator", f"run-{name}", f"lock-{name}", f"audit-{name}")


def _identity_payload(**overrides):
    value = {
        "venue": "TWSE",
        "official_code": "2330",
        "canonical_symbol": "2330.TW",
        "display_name": "台積電",
        "security_type": "股票",
        "fetched_at": "2026-08-21T00:01:00Z",
        "received_at": "2026-08-21T00:01:00Z",
        "ingested_at": "2026-08-21T00:02:00Z",
        "available_at": "2026-08-21T00:01:00Z",
        "source_reference": "phase14 fixture",
        "status": "accepted",
        "freshness_status": "current",
        "freshness_mode": "official_cadence_window",
        "current_complete": True,
        "coverage_complete": True,
        "raw_resource_revision_id": "raw-phase13-twse_universe_master",
        "raw_payload_sha256": hashlib.sha256(b"phase13:twse-universe-master").hexdigest(),
    }
    value.update(overrides)
    return value


def _db_with_identity(tmp_path):
    db = tmp_path / "phase14.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = _ctx("identity")
    anchor = universe.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture",
        context=context,
    )
    universe.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key="master",
        revision_number=1,
        payload=_identity_payload(),
        context=context,
        idempotency_key="identity-revision",
    )
    return db, anchor


def _identity_revision_id(db, instrument_id: str) -> str:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            """
            SELECT instrument_revision_id
            FROM universe_instrument_revisions
            WHERE instrument_id=? AND status='accepted'
            ORDER BY revision_number ASC, instrument_revision_id ASC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()[0]


def _add_reused_identity(db, first_anchor: dict):
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = _ctx("identity-reuse")
    universe.add_lifecycle_event(
        instrument_id=first_anchor["instrument_id"],
        event_type="terminated",
        available_at="2026-08-25T12:00:00Z",
        ingested_at="2026-08-25T12:00:00Z",
        effective_at="2026-08-25T12:00:00Z",
        event_date="2026-08-25",
        source_reference="phase14 identity reuse fixture",
        reason="old listing epoch terminated before code reuse",
        context=context,
    )
    second_anchor = universe.allocate_instrument(
        venue="TWSE",
        official_code="2330",
        source_identity="twse:2330:v2",
        first_observed_at="2026-08-26T04:00:00Z",
        source_reference="phase14 identity reuse fixture",
        context=context,
    )
    universe.add_revision(
        instrument_id=second_anchor["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key="master:epoch2",
        revision_number=1,
        payload=_identity_payload(
            first_observed_at="2026-08-26T04:00:00Z",
            available_at="2026-08-26T04:00:00Z",
            ingested_at="2026-08-26T04:00:00Z",
            source_reference="phase14 identity epoch 2 fixture",
        ),
        context=context,
        idempotency_key="identity-epoch-2",
    )
    return (
        first_anchor,
        second_anchor,
        _identity_revision_id(db, first_anchor["instrument_id"]),
        _identity_revision_id(db, second_anchor["instrument_id"]),
    )


def _raw(db, resource_id: str, provider_id: str, token: str, at: str):
    payload_hash = hashlib.sha256(token.encode()).hexdigest()
    schema_hash = hashlib.sha256(("schema:" + resource_id).encode()).hexdigest()
    foundation = DataFoundationRepository(str(db))
    revision = RawResourceRevision(
        raw_resource_revision_id="pending",
        provider_id=provider_id,
        resource_id=resource_id,
        logical_revision_key=token,
        received_at=at,
        ingested_at=at,
        available_at=at,
        raw_payload_sha256=payload_hash,
        parser_version="1",
        schema_fingerprint=schema_hash,
        storage_policy=StoragePolicy.HASH_ONLY,
        quality_status=DataHealthStatus.FRESH,
        eligibility_status=EligibilityStatus.ELIGIBLE,
    )
    revision = RawResourceRevision(
        **{**revision.__dict__, "raw_resource_revision_id": revision.deterministic_identity()}
    )
    return foundation.add_raw_revision(revision), payload_hash


def _source(
    repo: EodCloseRepository,
    raw: dict,
    raw_hash: str,
    *,
    date: str,
    at: str,
    status: str = "available",
    revision_number: int | None = None,
    supersedes_source_snapshot_id: str | None = None,
    available_at: str | None = None,
    ingested_at: str | None = None,
    received_at: str | None = None,
    row_count: int = 1,
    coverage_state: str = "complete",
    coverage_proof_type: str | None = None,
    coverage_proof_reference: str | None = None,
):
    payload = {
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": f"twse.eod.stock_day_all:{date}",
        "source_trade_date": date,
        "source_trade_date_status": "valid",
        "status": status,
        "coverage_state": coverage_state,
        "coverage_proof_type": coverage_proof_type,
        "coverage_proof_reference": coverage_proof_reference,
        "row_count": row_count,
        "source_date_min": date,
        "source_date_max": date,
        "fetched_at": at,
        "received_at": received_at or at,
        "available_at": available_at or at,
        "ingested_at": ingested_at or at,
        "source_url": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "contract_version": "eod_close_v1",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"eod-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(("normalized:" + date).encode()).hexdigest(),
        "source_record_reference": f"twse.eod.stock_day_all:{date}",
        "source_scope": "twse_whole_market_daily_close",
        "reason": "schema fixture" if status != "available" else None,
    }
    if revision_number is not None:
        payload["revision_number"] = revision_number
    if supersedes_source_snapshot_id is not None:
        payload["supersedes_source_snapshot_id"] = supersedes_source_snapshot_id
    return repo.add_source_snapshot(payload)


def _classification(
    repo: EodCloseRepository,
    db,
    *,
    at: str,
    state: str = "accepted",
    available_at: str | None = None,
    ingested_at: str | None = None,
    received_at: str | None = None,
    supersedes_classification_evidence_id: str | None = None,
    revision_number: int | None = None,
    market_raw: str = "上市",
    security_type_raw: str = "股票",
    currency_raw: str = "新台幣",
):
    raw, raw_hash = _raw(
        db,
        "twse.isin.security_classification",
        "twse-isin-official",
        f"class:{at}:{state}:{available_at}:{ingested_at}:{supersedes_classification_evidence_id}",
        at,
    )
    payload = {
        "resource_id": "twse.isin.security_classification",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": "twse.isin.security_classification:2330",
        "official_code": "2330",
        "market_raw": market_raw,
        "security_type_raw": security_type_raw,
        "listing_date": "2000-09-05",
        "currency_raw": currency_raw,
        "raw_cells_json": "[]",
        "classification_decision": "supported_stock" if state == "accepted" else "needs_human_input",
        "classification_state": state,
        "reason": None if state == "accepted" else "classifier blocker",
        "contract_version": "eod_product_scope_v1",
        "fetched_at": at,
        "received_at": received_at or at,
        "available_at": available_at or at,
        "ingested_at": ingested_at or at,
        "source_url": "https://isin.twse.com.tw/isin/single_main.jsp?owncode=2330&stockname=",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"class-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(("class-normalized:" + at).encode()).hexdigest(),
        "source_record_reference": "twse.isin.security_classification:2330",
    }
    if revision_number is not None:
        payload["revision_number"] = revision_number
    if supersedes_classification_evidence_id is not None:
        payload["supersedes_classification_evidence_id"] = supersedes_classification_evidence_id
    return repo.add_classification_evidence(payload)


def _observation(
    repo: EodCloseRepository,
    *,
    raw: dict,
    raw_hash: str,
    source: dict,
    classification: dict | None,
    anchor: dict | None,
    at: str,
    close: str = "1005",
    volume: str = "123456",
    instrument_revision_id: str | None = None,
    product_scope: str = "supported_stock",
    observation_status: str = "available",
    public_eligibility_status: str = "eligible",
    available_at: str | None = None,
    ingested_at: str | None = None,
    supersedes_observation_id: str | None = None,
    revision_number: int | None = None,
):
    payload = {
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "source_snapshot_id": source["source_snapshot_id"],
        "classification_evidence_id": classification["classification_evidence_id"] if classification else None,
        "instrument_id": anchor["instrument_id"] if anchor else None,
        "instrument_revision_id": instrument_revision_id,
        "venue": "TWSE",
        "official_code": "2330",
        "trade_date": source["source_trade_date"],
        "trade_date_status": "valid",
        "raw_close_text": close,
        "close_value": close,
        "raw_volume_text": volume,
        "volume_value": volume,
        "raw_trade_indication_text": "123456789",
        "trade_indication_value": "123456789",
        "currency": "TWD",
        "unit": "TWD_per_share",
        "price_semantics_version": "official_reported_close_v1",
        "product_scope": product_scope,
        "observation_status": observation_status,
        "public_eligibility_status": public_eligibility_status,
        "quality_status": "fresh",
        "quality_flags_json": "[]",
        "row_fingerprint": hashlib.sha256(
            f"row:{source['source_snapshot_id']}:{close}:{volume}".encode()
        ).hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(
            f"normalized-row:{source['source_snapshot_id']}".encode()
        ).hexdigest(),
        "source_trading_scope": "twse_whole_market_daily_close",
        "available_at": available_at or at,
        "ingested_at": ingested_at or at,
        "source_record_reference": "twse.eod.stock_day_all:2026-08-27:2330",
    }
    if revision_number is not None:
        payload["revision_number"] = revision_number
    if supersedes_observation_id is not None:
        payload["supersedes_observation_id"] = supersedes_observation_id
    return repo.add_observation(payload)


def test_phase14_migration_is_rerunnable_and_registry_is_seeded(tmp_path) -> None:
    db = tmp_path / "migration.sqlite"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))

    assert "20260827_18_phase14_eod_close_context" in first["applied_additive_migration_ids"]
    assert second["additive_migration_ids"] == first["additive_migration_ids"]
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT resource_type FROM data_resources WHERE resource_id=?",
            ("twse.eod.stock_day_all",),
        ).fetchone()[0] == "eod_close"
        assert conn.execute(
            "SELECT write_enabled FROM eod_price_resource_policies WHERE resource_id=?",
            ("twse.eod.stock_day_all",),
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_eod_repository_appends_and_reuses_exact_raw_payload(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    raw, raw_hash = _raw(db, "twse.eod.stock_day_all", "twse-universe-official", "price:2026-08-27", "2026-08-27T05:00:00Z")
    source = _source(repo, raw, raw_hash, date="2026-08-27", at="2026-08-27T05:00:00Z")
    classification = _classification(repo, db, at="2026-08-27T05:01:00Z")
    observation = _observation(repo, raw=raw, raw_hash=raw_hash, source=source, classification=classification, anchor=anchor, at="2026-08-27T05:02:00Z")

    reused = repo.add_source_snapshot({
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": "twse.eod.stock_day_all:2026-08-27",
        "source_trade_date": "2026-08-27",
        "source_trade_date_status": "valid",
        "status": "available",
        "coverage_state": "complete",
        "row_count": 1,
        "fetched_at": "2026-08-27T05:00:00Z",
        "received_at": "2026-08-27T05:00:00Z",
        "available_at": "2026-08-27T05:00:00Z",
        "ingested_at": "2026-08-27T05:00:00Z",
        "source_url": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "contract_version": "eod_close_v1",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"eod-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(b"normalized:2026-08-27").hexdigest(),
        "source_record_reference": "twse.eod.stock_day_all:2026-08-27",
        "source_scope": "twse_whole_market_daily_close",
    })
    assert source["created"] is True
    assert observation["created"] is True
    assert reused["created"] is False
    with pytest.raises(ValueError, match="source snapshot raw revision mismatch"):
        repo.add_source_snapshot({
            "resource_id": "twse.eod.stock_day_all",
            "raw_resource_revision_id": raw["raw_resource_revision_id"],
            "logical_revision_key": "twse.eod.stock_day_all:2026-08-27",
            "raw_payload_sha256": "f" * 64,
            "source_trade_date": "2026-08-27",
            "source_trade_date_status": "valid",
            "status": "available",
            "coverage_state": "complete",
            "row_count": 1,
            "fetched_at": "2026-08-27T05:00:00Z",
            "received_at": "2026-08-27T05:00:00Z",
            "available_at": "2026-08-27T05:00:00Z",
            "ingested_at": "2026-08-27T05:00:00Z",
            "source_url": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            "contract_version": "eod_close_v1",
            "parser_version": "1",
            "schema_fingerprint": hashlib.sha256(b"eod-schema").hexdigest(),
            "normalized_payload_sha256": hashlib.sha256(b"changed").hexdigest(),
            "source_record_reference": "changed",
            "source_scope": "twse_whole_market_daily_close",
        })


def test_current_service_returns_available_only_for_same_day_complete_evidence(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    raw, raw_hash = _raw(db, "twse.eod.stock_day_all", "twse-universe-official", "price:current", "2026-08-27T05:00:00Z")
    source = _source(repo, raw, raw_hash, date="2026-08-27", at="2026-08-27T05:00:00Z")
    classification = _classification(repo, db, at="2026-08-27T05:01:00Z")
    _observation(repo, raw=raw, raw_hash=raw_hash, source=source, classification=classification, anchor=anchor, at="2026-08-27T05:02:00Z")

    result = EodCloseService(str(db)).current(
        "2330.TW", evaluated_at="2026-08-27T08:00:00Z"
    )

    assert result["status"] == "available"
    assert result["close_value"] == "1005"
    assert result["current_complete"] is True
    assert result["price_semantics"] == "official_reported_close_v1"


def test_as_of_is_date_first_and_does_not_fallback_from_latest_blocker(tmp_path) -> None:
    db, _ = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    raw1, hash1 = _raw(db, "twse.eod.stock_day_all", "twse-universe-official", "price:d1", "2026-08-25T05:00:00Z")
    _source(repo, raw1, hash1, date="2026-08-25", at="2026-08-25T05:00:00Z")
    raw2, hash2 = _raw(db, "twse.eod.stock_day_all", "twse-universe-official", "price:d2", "2026-08-26T05:00:00Z")
    _source(repo, raw2, hash2, date="2026-08-26", at="2026-08-26T05:00:00Z", status="schema_changed")

    result = EodCloseService(str(db)).as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-27T00:00:00Z"
    )

    assert result["selected_trade_date"] == "2026-08-26"
    assert result["status"] == "blocked"
    assert result["close_value"] is None
    assert "schema_changed" in result["reason_codes"]


def test_ingestion_writes_are_disabled_without_explicit_operator_gate(tmp_path) -> None:
    from src.services.eod_close_ingestion_service import EodCloseIngestionService, EodIngestionDisabled

    db = tmp_path / "disabled.sqlite"
    apply_valuation_migration(str(db))
    with pytest.raises(EodIngestionDisabled):
        EodCloseIngestionService(str(db), enabled=False).ingest_price_payload("TWSE", [])


def test_operator_ingestion_reuses_phase10_raw_identity_and_eod_lineage(tmp_path) -> None:
    from src.services.eod_close_ingestion_service import EodCloseIngestionService

    db = tmp_path / "ingestion.sqlite"
    apply_valuation_migration(str(db))
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "eod_twse_stock_day_all.json").read_text(encoding="utf-8")
    )
    service = EodCloseIngestionService(str(db), enabled=True)
    first = service.ingest_price_payload(
        "TWSE", payload, received_at="2026-08-27T05:00:00Z", idempotency_key="operator-command-1"
    )
    second = service.ingest_price_payload(
        "TWSE", payload, received_at="2026-08-27T05:00:00Z", idempotency_key="operator-command-1"
    )

    assert first["resource_id"] == "twse.eod.stock_day_all"
    assert first["observation_ids"]
    assert second["created"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_resource_revisions WHERE resource_id=?", ("twse.eod.stock_day_all",)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM eod_close_source_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM eod_close_observations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM eod_ingestion_idempotency").fetchone()[0] == 1


def test_phase14_eod_evidence_round_trips_through_backup_and_restore(tmp_path) -> None:
    from src.services.eod_close_ingestion_service import EodCloseIngestionService

    db = tmp_path / "backup-source.sqlite"
    apply_valuation_migration(str(db))
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "eod_twse_stock_day_all.json").read_text(encoding="utf-8")
    )
    EodCloseIngestionService(str(db), enabled=True).ingest_price_payload(
        "TWSE", payload, received_at="2026-08-27T05:00:00Z", idempotency_key="backup-command-1"
    )

    backup = tmp_path / "backup.sqlite"
    restored = tmp_path / "restored.sqlite"
    backed_up = EvidenceBackupService.backup(str(db), str(backup))
    restored_result = EvidenceBackupService.restore(str(backup), str(restored))

    assert backed_up["status"] == "valid"
    assert restored_result["status"] == "valid"
    assert restored_result["eod_foundation_counts"] == backed_up["eod_foundation_counts"]
    assert restored_result["eod_foundation_counts"]["eod_close_source_snapshots"] == 1
    assert restored_result["eod_foundation_counts"]["eod_close_observations"] == 1
    assert restored_result["eod_foundation_counts"]["eod_ingestion_idempotency"] == 1


def test_current_selection_requires_available_and_ingested_cutoff_visibility(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    raw_source, source_hash = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "current-visibility-source-1", "2026-08-27T05:00:00Z",
    )
    source = _source(
        repo, raw_source, source_hash, date="2026-08-27",
        at="2026-08-27T05:00:00Z",
    )
    classification = _classification(
        repo, db, at="2026-08-27T05:01:00Z",
    )
    _observation(
        repo, raw=raw_source, raw_hash=source_hash, source=source,
        classification=classification, anchor=anchor, at="2026-08-27T05:02:00Z",
    )

    raw_future, future_hash = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "current-visibility-source-2", "2026-08-27T06:00:00Z",
    )
    future_source = _source(
        repo, raw_future, future_hash, date="2026-08-27",
        at="2026-08-27T10:00:00Z", revision_number=2,
        supersedes_source_snapshot_id=source["source_snapshot_id"],
        available_at="2026-08-27T09:00:00Z",
        ingested_at="2026-08-27T06:00:00Z",
        received_at="2026-08-27T10:00:00Z",
    )
    future_classification = _classification(
        repo, db, at="2026-08-27T10:00:00Z", state="blocked",
        supersedes_classification_evidence_id=classification["classification_evidence_id"],
        available_at="2026-08-27T09:00:00Z",
        ingested_at="2026-08-27T06:00:00Z",
        received_at="2026-08-27T10:00:00Z",
    )

    with repo.read_transaction() as conn:
        bundle = repo.current_bundle(
            conn, canonical_symbol="2330.TW",
            evaluated_at="2026-08-27T08:00:00Z",
        )
    assert bundle["snapshot"]["source_snapshot_id"] == source["source_snapshot_id"]
    assert bundle["classification"]["classification_evidence_id"] == classification["classification_evidence_id"]
    assert future_source["source_snapshot_id"] != source["source_snapshot_id"]
    assert future_classification["classification_evidence_id"] != classification["classification_evidence_id"]

    result = EodCloseService(str(db)).current(
        "2330.TW", evaluated_at="2026-08-27T08:00:00Z"
    )
    assert result["status"] == "available"
    assert result["close_value"] == "1005"


def test_as_of_correction_visibility_changes_only_after_cutoff(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    classification = _classification(repo, db, at="2026-08-25T04:00:00Z")
    raw_first, hash_first = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "correction-d1-v1", "2026-08-25T05:00:00Z",
    )
    source_first = _source(
        repo, raw_first, hash_first, date="2026-08-25",
        at="2026-08-25T05:00:00Z",
    )
    observation_first = _observation(
        repo, raw=raw_first, raw_hash=hash_first, source=source_first,
        classification=classification, anchor=anchor,
        at="2026-08-25T05:02:00Z", close="100",
    )

    raw_corrected, hash_corrected = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "correction-d1-v2", "2026-08-25T09:00:00Z",
    )
    source_corrected = _source(
        repo, raw_corrected, hash_corrected, date="2026-08-25",
        at="2026-08-25T09:00:00Z", revision_number=2,
        supersedes_source_snapshot_id=source_first["source_snapshot_id"],
    )
    _observation(
        repo, raw=raw_corrected, raw_hash=hash_corrected,
        source=source_corrected, classification=classification, anchor=anchor,
        at="2026-08-25T09:02:00Z", close="101",
        revision_number=2,
        supersedes_observation_id=observation_first["close_observation_id"],
    )

    with repo.read_transaction() as conn:
        before_bundle = repo.as_of_bundle(
            conn, canonical_symbol="2330.TW",
            knowledge_cutoff_at="2026-08-25T08:00:00Z",
        )
        after_bundle = repo.as_of_bundle(
            conn, canonical_symbol="2330.TW",
            knowledge_cutoff_at="2026-08-25T10:00:00Z",
        )
    assert before_bundle["snapshot"]["source_snapshot_id"] == source_first["source_snapshot_id"]
    assert after_bundle["snapshot"]["source_snapshot_id"] == source_corrected["source_snapshot_id"]

    service = EodCloseService(str(db))
    before = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-25T08:00:00Z"
    )
    after = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-25T10:00:00Z"
    )
    assert before["selected_trade_date"] == "2026-08-25"
    assert before["status"] == "available"
    assert before["close_value"] == "100"
    assert after["selected_trade_date"] == "2026-08-25"
    assert after["status"] == "available"
    assert after["close_value"] == "101"


def test_late_d1_correction_cannot_replace_newer_d2(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    classification = _classification(repo, db, at="2026-08-25T04:00:00Z")

    raw_d1, hash_d1 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "late-d1-v1", "2026-08-25T05:00:00Z",
    )
    source_d1 = _source(repo, raw_d1, hash_d1, date="2026-08-25", at="2026-08-25T05:00:00Z")
    observation_d1 = _observation(
        repo, raw=raw_d1, raw_hash=hash_d1, source=source_d1,
        classification=classification, anchor=anchor,
        at="2026-08-25T05:02:00Z", close="100",
    )

    raw_d2, hash_d2 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "late-d2-v1", "2026-08-26T05:00:00Z",
    )
    source_d2 = _source(repo, raw_d2, hash_d2, date="2026-08-26", at="2026-08-26T05:00:00Z")
    _observation(
        repo, raw=raw_d2, raw_hash=hash_d2, source=source_d2,
        classification=classification, anchor=anchor,
        at="2026-08-26T05:02:00Z", close="200",
    )

    raw_late, hash_late = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "late-d1-v2", "2026-08-27T05:00:00Z",
    )
    source_late = _source(
        repo, raw_late, hash_late, date="2026-08-25",
        at="2026-08-27T05:00:00Z", revision_number=2,
        supersedes_source_snapshot_id=source_d1["source_snapshot_id"],
    )
    _observation(
        repo, raw=raw_late, raw_hash=hash_late, source=source_late,
        classification=classification, anchor=anchor,
        at="2026-08-27T05:02:00Z", close="101", revision_number=2,
        supersedes_observation_id=observation_d1["close_observation_id"],
    )

    result = EodCloseService(str(db)).as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-28T00:00:00Z"
    )
    assert result["selected_trade_date"] == "2026-08-26"
    assert result["status"] == "available"
    assert result["close_value"] == "200"


def test_classifier_correction_blocker_has_no_fallback_and_is_deterministic(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    accepted = _classification(repo, db, at="2026-08-25T04:00:00Z")
    raw, raw_hash = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "classifier-blocker-price", "2026-08-25T05:00:00Z",
    )
    source = _source(repo, raw, raw_hash, date="2026-08-25", at="2026-08-25T05:00:00Z")
    _observation(
        repo, raw=raw, raw_hash=raw_hash, source=source,
        classification=accepted, anchor=anchor, at="2026-08-25T05:02:00Z",
        close="100",
    )
    blocked = _classification(
        repo, db, at="2026-08-25T09:00:00Z", state="blocked",
        supersedes_classification_evidence_id=accepted["classification_evidence_id"],
    )

    service = EodCloseService(str(db))
    before = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-25T08:00:00Z"
    )
    after = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-25T10:00:00Z"
    )
    repeated = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-25T10:00:00Z"
    )
    with repo.read_transaction() as conn:
        before_bundle = repo.as_of_bundle(
            conn, canonical_symbol="2330.TW",
            knowledge_cutoff_at="2026-08-25T08:00:00Z",
        )
        after_bundle = repo.as_of_bundle(
            conn, canonical_symbol="2330.TW",
            knowledge_cutoff_at="2026-08-25T10:00:00Z",
        )
    assert before["status"] == "available"
    assert before["close_value"] == "100"
    assert after["status"] == "blocked"
    assert after["close_value"] is None
    assert "latest_revision_blocked" in after["reason_codes"]
    assert blocked["classification_evidence_id"] != accepted["classification_evidence_id"]
    assert before_bundle["classification"]["classification_evidence_id"] == accepted["classification_evidence_id"]
    assert after_bundle["classification"]["classification_evidence_id"] == blocked["classification_evidence_id"]
    assert repeated == after


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_reason"),
    [
        ("blocked", "blocked", "schema_changed"),
        ("revoked", "blocked", "source_revoke_without_replacement"),
        ("missing", "insufficient_data", "row_missing"),
        ("ineligible", "insufficient_data", "official_zero_volume_not_public_eligible"),
    ],
)
def test_as_of_d2_states_never_fallback_to_d1(
    tmp_path, scenario: str, expected_status: str, expected_reason: str,
) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    classification = _classification(repo, db, at="2026-08-25T04:00:00Z")

    raw_d1, hash_d1 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        f"d2-state-{scenario}-d1", "2026-08-25T05:00:00Z",
    )
    source_d1 = _source(repo, raw_d1, hash_d1, date="2026-08-25", at="2026-08-25T05:00:00Z")
    _observation(
        repo, raw=raw_d1, raw_hash=hash_d1, source=source_d1,
        classification=classification, anchor=anchor, at="2026-08-25T05:02:00Z",
        close="100",
    )

    raw_d2, hash_d2 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        f"d2-state-{scenario}-d2", "2026-08-26T05:00:00Z",
    )
    source_d2 = _source(repo, raw_d2, hash_d2, date="2026-08-26", at="2026-08-26T05:00:00Z")
    selected_source = source_d2
    if scenario in {"blocked", "revoked"}:
        raw_latest, hash_latest = _raw(
            db, "twse.eod.stock_day_all", "twse-universe-official",
            f"d2-state-{scenario}-latest", "2026-08-26T06:00:00Z",
        )
        selected_source = _source(
            repo, raw_latest, hash_latest, date="2026-08-26",
            at="2026-08-26T06:00:00Z",
            status="schema_changed" if scenario == "blocked" else "revoked",
            revision_number=2,
            supersedes_source_snapshot_id=source_d2["source_snapshot_id"],
        )
    elif scenario == "ineligible":
        _observation(
            repo, raw=raw_d2, raw_hash=hash_d2, source=source_d2,
            classification=classification, anchor=anchor, at="2026-08-26T05:02:00Z",
            close="200", volume="0", product_scope="supported_stock",
            observation_status="insufficient_data",
            public_eligibility_status="ineligible",
        )

    result = EodCloseService(str(db)).as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-27T00:00:00Z"
    )
    with repo.read_transaction() as conn:
        bundle = repo.as_of_bundle(
            conn, canonical_symbol="2330.TW",
            knowledge_cutoff_at="2026-08-27T00:00:00Z",
        )
    assert selected_source["source_trade_date"] == "2026-08-26"
    assert result["selected_trade_date"] == "2026-08-26"
    assert bundle["snapshot"]["source_snapshot_id"] == selected_source["source_snapshot_id"]
    assert result["status"] == expected_status
    assert result["close_value"] is None
    assert expected_reason in result["reason_codes"]


def test_identity_epoch_and_code_reuse_are_cutoff_correct_for_eod(tmp_path) -> None:
    db, first_anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    first_anchor, second_anchor, first_revision_id, second_revision_id = _add_reused_identity(
        db, first_anchor
    )
    classification = _classification(repo, db, at="2026-08-25T04:00:00Z")

    raw_d1, hash_d1 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "identity-epoch-d1", "2026-08-25T05:00:00Z",
    )
    source_d1 = _source(repo, raw_d1, hash_d1, date="2026-08-25", at="2026-08-25T05:00:00Z")
    _observation(
        repo, raw=raw_d1, raw_hash=hash_d1, source=source_d1,
        classification=classification, anchor=first_anchor, at="2026-08-25T05:02:00Z",
        close="100", instrument_revision_id=first_revision_id,
    )

    raw_d2, hash_d2 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "identity-epoch-d2", "2026-08-26T05:00:00Z",
    )
    source_d2 = _source(repo, raw_d2, hash_d2, date="2026-08-26", at="2026-08-26T05:00:00Z")
    _observation(
        repo, raw=raw_d2, raw_hash=hash_d2, source=source_d2,
        classification=classification, anchor=second_anchor, at="2026-08-26T05:02:00Z",
        close="200", instrument_revision_id=second_revision_id,
    )

    service = EodCloseService(str(db))
    before = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-25T10:00:00Z"
    )
    after = service.as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-26T08:00:00Z"
    )
    assert before["selected_trade_date"] == "2026-08-25"
    assert before["instrument_id"] == first_anchor["instrument_id"]
    assert before["close_value"] == "100"
    assert after["selected_trade_date"] == "2026-08-26"
    assert after["instrument_id"] == second_anchor["instrument_id"]
    assert after["close_value"] == "200"

    with repo.read_transaction() as conn:
        before_identity = repo.identity_for_eod(
            conn, canonical_symbol="2330.TW", cutoff="2026-08-25T10:00:00Z"
        )
        after_identity = repo.identity_for_eod(
            conn, canonical_symbol="2330.TW", cutoff="2026-08-26T08:00:00Z"
        )
    assert before_identity["identity_epoch"] == 1
    assert before_identity["instrument_revision_id"] == first_revision_id
    assert after_identity["identity_epoch"] == 2
    assert after_identity["instrument_revision_id"] == second_revision_id


def test_backup_restore_preserves_phase14_semantic_lineage_and_public_eligibility(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    first_anchor, second_anchor, first_revision_id, second_revision_id = _add_reused_identity(
        db, anchor
    )
    repo = EodCloseRepository(str(db))
    classification = _classification(repo, db, at="2026-08-25T04:00:00Z")

    raw_d1, hash_d1 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "backup-semantic-d1-v1", "2026-08-25T05:00:00Z",
    )
    source_d1 = _source(repo, raw_d1, hash_d1, date="2026-08-25", at="2026-08-25T05:00:00Z")
    observation_d1 = _observation(
        repo, raw=raw_d1, raw_hash=hash_d1, source=source_d1,
        classification=classification, anchor=first_anchor, at="2026-08-25T05:02:00Z",
        close="100", instrument_revision_id=first_revision_id,
    )
    raw_d1_fix, hash_d1_fix = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "backup-semantic-d1-v2", "2026-08-25T09:00:00Z",
    )
    source_d1_fix = _source(
        repo, raw_d1_fix, hash_d1_fix, date="2026-08-25", at="2026-08-25T09:00:00Z",
        revision_number=2,
        supersedes_source_snapshot_id=source_d1["source_snapshot_id"],
    )
    _observation(
        repo, raw=raw_d1_fix, raw_hash=hash_d1_fix, source=source_d1_fix,
        classification=classification, anchor=first_anchor, at="2026-08-25T09:02:00Z",
        close="101", revision_number=2, instrument_revision_id=first_revision_id,
        supersedes_observation_id=observation_d1["close_observation_id"],
    )

    raw_d2, hash_d2 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "backup-semantic-d2-v1", "2026-08-26T05:00:00Z",
    )
    source_d2 = _source(repo, raw_d2, hash_d2, date="2026-08-26", at="2026-08-26T05:00:00Z")
    raw_d2_blocked, hash_d2_blocked = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "backup-semantic-d2-v2", "2026-08-26T06:00:00Z",
    )
    source_d2_blocked = _source(
        repo, raw_d2_blocked, hash_d2_blocked, date="2026-08-26",
        at="2026-08-26T06:00:00Z", status="revoked", revision_number=2,
        supersedes_source_snapshot_id=source_d2["source_snapshot_id"],
    )

    raw_d3, hash_d3 = _raw(
        db, "twse.eod.stock_day_all", "twse-universe-official",
        "backup-semantic-d3-zero-volume", "2026-08-27T05:00:00Z",
    )
    source_d3 = _source(repo, raw_d3, hash_d3, date="2026-08-27", at="2026-08-27T05:00:00Z")
    _observation(
        repo, raw=raw_d3, raw_hash=hash_d3, source=source_d3,
        classification=classification, anchor=second_anchor, at="2026-08-27T05:02:00Z",
        close="300", volume="0", observation_status="insufficient_data",
        public_eligibility_status="ineligible", instrument_revision_id=second_revision_id,
    )

    source_service = EodCloseService(str(db))
    as_of_cutoffs = (
        "2026-08-25T08:00:00Z",
        "2026-08-25T10:00:00Z",
        "2026-08-27T00:00:00Z",
        "2026-08-28T00:00:00Z",
    )
    source_as_of = {
        cutoff: source_service.as_of("2330.TW", knowledge_cutoff_at=cutoff)
        for cutoff in as_of_cutoffs
    }
    source_current = source_service.current(
        "2330.TW", evaluated_at="2026-08-26T08:00:00Z"
    )

    backup = tmp_path / "semantic-backup.sqlite"
    restored = tmp_path / "semantic-restored.sqlite"
    backed_up = EvidenceBackupService.backup(str(db), str(backup))
    restored_result = EvidenceBackupService.restore(str(backup), str(restored))
    assert backed_up["status"] == restored_result["status"] == "valid"
    assert restored_result["eod_foundation_counts"] == backed_up["eod_foundation_counts"]

    restored_service = EodCloseService(str(restored))
    for cutoff, expected in source_as_of.items():
        assert restored_service.as_of(
            "2330.TW", knowledge_cutoff_at=cutoff
        ) == expected
    assert restored_service.current(
        "2330.TW", evaluated_at="2026-08-26T08:00:00Z"
    ) == source_current
    assert source_current["status"] == "blocked"
    assert source_current["close_value"] is None
    assert source_current["instrument_id"] == second_anchor["instrument_id"]
    assert source_as_of["2026-08-25T08:00:00Z"]["close_value"] == "100"
    assert source_as_of["2026-08-25T08:00:00Z"]["instrument_id"] == first_anchor["instrument_id"]
    assert source_as_of["2026-08-25T10:00:00Z"]["close_value"] == "101"
    assert source_as_of["2026-08-27T00:00:00Z"]["selected_trade_date"] == "2026-08-26"
    assert source_as_of["2026-08-27T00:00:00Z"]["status"] == "blocked"
    assert source_as_of["2026-08-27T00:00:00Z"]["close_value"] is None
    assert source_as_of["2026-08-27T00:00:00Z"]["instrument_id"] == second_anchor["instrument_id"]
    assert "source_revoke_without_replacement" in source_as_of["2026-08-27T00:00:00Z"]["reason_codes"]
    assert source_as_of["2026-08-28T00:00:00Z"]["status"] == "insufficient_data"
    assert source_as_of["2026-08-28T00:00:00Z"]["close_value"] is None
    assert "official_zero_volume_not_public_eligible" in source_as_of["2026-08-28T00:00:00Z"]["reason_codes"]
    assert source_as_of["2026-08-28T00:00:00Z"]["instrument_id"] == second_anchor["instrument_id"]
    assert source_d2_blocked["source_trade_date"] == "2026-08-26"

    with sqlite3.connect(restored) as conn:
        zero_volume = conn.execute(
            """
            SELECT raw_volume_text, public_eligibility_status
            FROM eod_close_observations
            WHERE source_snapshot_id=?
            """,
            (source_d3["source_snapshot_id"],),
        ).fetchone()
    assert zero_volume == ("0", "ineligible")
    with restored_service.repository.read_transaction() as conn:
        restored_before_identity = restored_service.repository.identity_for_eod(
            conn, canonical_symbol="2330.TW", cutoff="2026-08-25T08:00:00Z"
        )
        restored_after_identity = restored_service.repository.identity_for_eod(
            conn, canonical_symbol="2330.TW", cutoff="2026-08-27T00:00:00Z"
        )
    assert restored_before_identity["identity_epoch"] == 1
    assert restored_before_identity["instrument_revision_id"] == first_revision_id
    assert restored_after_identity["identity_epoch"] == 2
    assert restored_after_identity["instrument_revision_id"] == second_revision_id
