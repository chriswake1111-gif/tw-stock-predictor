from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.services.eod_close_ingestion_service import EodCloseIngestionService
from tests.test_phase14_eod_repository_service import _raw


FIXTURES = Path(__file__).parent / "fixtures"


def _price_payload() -> list[dict[str, str]]:
    return json.loads(
        (FIXTURES / "eod_twse_stock_day_all.json").read_text(encoding="utf-8")
    )


def test_migration_20_removes_global_payload_ownership_and_is_rerunnable(tmp_path) -> None:
    db = tmp_path / "migration-20.sqlite"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))

    assert first["additive_migration_ids"][-1] == (
        "20260828_20_phase14_third_code_review_remediation"
    )
    assert second["additive_migration_ids"] == first["additive_migration_ids"]

    with sqlite3.connect(db) as conn:
        for table in (
            "eod_ingestion_command_reservations",
            "eod_ingestion_idempotency",
        ):
            for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
                index_name = index[1]
                is_unique = index[2]
                columns = {
                    row[2]
                    for row in conn.execute(f"PRAGMA index_info({index_name})")
                }
                assert not (is_unique and "payload_fingerprint" in columns)


def test_command_reservation_identity_is_key_scoped(tmp_path) -> None:
    db = tmp_path / "reservation-scope.sqlite"
    apply_valuation_migration(str(db))
    repo = EodCloseRepository(str(db))
    fingerprint = "b" * 64

    for key, resource in (
        ("command-key-1", "twse.eod.stock_day_all"),
        ("command-key-2", "twse.eod.stock_day_all"),
        ("command-key-3", "tpex.eod.daily_close_quotes"),
    ):
        repo.reserve_ingestion_command(
            idempotency_key=key,
            payload_fingerprint=fingerprint,
            resource_id=resource,
            actor_id="test-operator",
            command_received_at="2026-08-28T05:00:00Z",
            source_published_at=None,
        )

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_ingestion_command_reservations "
            "WHERE payload_fingerprint=?",
            (fingerprint,),
        ).fetchone()[0] == 3


def test_different_command_key_reuses_existing_price_content(tmp_path) -> None:
    db = tmp_path / "price-content-reuse.sqlite"
    apply_valuation_migration(str(db))
    service = EodCloseIngestionService(str(db), enabled=True)
    payload = _price_payload()

    first = service.ingest_price_payload(
        "TWSE",
        payload,
        received_at="2026-08-28T05:00:00Z",
        idempotency_key="price-content-key-1",
    )
    second = service.ingest_price_payload(
        "TWSE",
        payload,
        received_at="2026-08-28T06:00:00Z",
        idempotency_key="price-content-key-2",
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["raw_resource_revision_id"] == first["raw_resource_revision_id"]
    assert second["source_snapshot_id"] == first["source_snapshot_id"]
    assert second["observation_ids"] == first["observation_ids"]
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM raw_resource_revisions "
            "WHERE resource_id='twse.eod.stock_day_all'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_close_source_snapshots"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_close_observations"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT MAX(revision_number) FROM eod_close_observations"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_ingestion_idempotency"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_ingestion_command_reservations"
        ).fetchone()[0] == 2


def test_different_command_key_reuses_existing_classification_content(tmp_path) -> None:
    db = tmp_path / "classification-content-reuse.sqlite"
    apply_valuation_migration(str(db))
    service = EodCloseIngestionService(str(db), enabled=True)
    html = (FIXTURES / "eod_isin_supported.html").read_text(encoding="utf-8")

    first = service.ingest_classification_html(
        "2330",
        html,
        venue="TWSE",
        trade_date="2026-08-28",
        received_at="2026-08-28T05:00:00Z",
        idempotency_key="classification-content-key-1",
    )
    second = service.ingest_classification_html(
        "2330",
        html,
        venue="TWSE",
        trade_date="2026-08-28",
        received_at="2026-08-28T06:00:00Z",
        idempotency_key="classification-content-key-2",
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["raw_resource_revision_id"] == first["raw_resource_revision_id"]
    assert second["classification_evidence_id"] == first["classification_evidence_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_product_classification_evidence"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT MAX(revision_number) FROM eod_product_classification_evidence"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_ingestion_idempotency"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_ingestion_command_reservations"
        ).fetchone()[0] == 2


def test_content_idempotency_is_scoped_to_resource_and_raw_revision(tmp_path) -> None:
    db = tmp_path / "content-scope.sqlite"
    apply_valuation_migration(str(db))
    repo = EodCloseRepository(str(db))
    fingerprint_token = "same-content-in-independent-scopes"
    twse_raw, fingerprint = _raw(
        db,
        "twse.eod.stock_day_all",
        "twse-universe-official",
        fingerprint_token,
        "2026-08-28T05:00:00Z",
    )
    tpex_raw, same_fingerprint = _raw(
        db,
        "tpex.eod.daily_close_quotes",
        "tpex-universe-official",
        fingerprint_token,
        "2026-08-28T05:00:00Z",
    )
    assert same_fingerprint == fingerprint

    first = repo.add_ingestion_idempotency({
        "idempotency_key": "scope-twse",
        "payload_fingerprint": fingerprint,
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": twse_raw["raw_resource_revision_id"],
        "actor_id": "test-operator",
        "created_at": "2026-08-28T05:01:00Z",
    })
    second = repo.add_ingestion_idempotency({
        "idempotency_key": "scope-tpex",
        "payload_fingerprint": fingerprint,
        "resource_id": "tpex.eod.daily_close_quotes",
        "raw_resource_revision_id": tpex_raw["raw_resource_revision_id"],
        "actor_id": "test-operator",
        "created_at": "2026-08-28T05:01:00Z",
    })

    assert first["created"] is True
    assert second["created"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM eod_ingestion_idempotency "
            "WHERE payload_fingerprint=?",
            (fingerprint,),
        ).fetchone()[0] == 2
