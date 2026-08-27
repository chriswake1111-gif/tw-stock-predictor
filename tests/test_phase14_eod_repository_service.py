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


def _source(repo: EodCloseRepository, raw: dict, raw_hash: str, *, date: str, at: str, status: str = "available"):
    return repo.add_source_snapshot({
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": f"twse.eod.stock_day_all:{date}",
        "source_trade_date": date,
        "source_trade_date_status": "valid",
        "status": status,
        "coverage_state": "complete",
        "row_count": 1,
        "source_date_min": date,
        "source_date_max": date,
        "fetched_at": at,
        "received_at": at,
        "available_at": at,
        "ingested_at": at,
        "source_url": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "contract_version": "eod_close_v1",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"eod-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(("normalized:" + date).encode()).hexdigest(),
        "source_record_reference": f"twse.eod.stock_day_all:{date}",
        "source_scope": "twse_whole_market_daily_close",
        "reason": "schema fixture" if status != "available" else None,
    })


def _classification(repo: EodCloseRepository, db, *, at: str, state: str = "accepted"):
    raw, raw_hash = _raw(db, "twse.isin.security_classification", "twse-isin-official", f"class:{at}:{state}", at)
    return repo.add_classification_evidence({
        "resource_id": "twse.isin.security_classification",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": "twse.isin.security_classification:2330",
        "official_code": "2330",
        "market_raw": "上市",
        "security_type_raw": "股票",
        "listing_date": "2000-09-05",
        "currency_raw": "新台幣",
        "raw_cells_json": "[]",
        "classification_decision": "supported_stock" if state == "accepted" else "needs_human_input",
        "classification_state": state,
        "reason": None if state == "accepted" else "classifier blocker",
        "contract_version": "eod_product_scope_v1",
        "fetched_at": at,
        "received_at": at,
        "available_at": at,
        "ingested_at": at,
        "source_url": "https://isin.twse.com.tw/isin/single_main.jsp?owncode=2330&stockname=",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"class-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(("class-normalized:" + at).encode()).hexdigest(),
        "source_record_reference": "twse.isin.security_classification:2330",
    })


def _observation(repo: EodCloseRepository, *, raw: dict, raw_hash: str, source: dict, classification: dict, anchor: dict, at: str, close: str = "1005", volume: str = "123456"):
    return repo.add_observation({
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "source_snapshot_id": source["source_snapshot_id"],
        "classification_evidence_id": classification["classification_evidence_id"],
        "instrument_id": anchor["instrument_id"],
        "instrument_revision_id": None,
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
        "product_scope": "supported_stock",
        "observation_status": "available",
        "public_eligibility_status": "eligible",
        "quality_status": "fresh",
        "quality_flags_json": "[]",
        "row_fingerprint": hashlib.sha256(b"row").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(b"normalized-row").hexdigest(),
        "source_trading_scope": "twse_whole_market_daily_close",
        "available_at": at,
        "ingested_at": at,
        "source_record_reference": "twse.eod.stock_day_all:2026-08-27:2330",
    })


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
