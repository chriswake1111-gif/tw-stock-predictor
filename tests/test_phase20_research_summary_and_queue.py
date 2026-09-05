"""Tests for Phase 20 Research Summary and Human Decision Queue (WP05)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.paths import RuntimePaths
from src.runtime.settings import RuntimeSettings
from src.services.current_research_service import CurrentResearchService


def _setup_db(tmp_path: Path) -> tuple[Path, CurrentResearchService]:
    db_file = tmp_path / "test_research_summary.db"
    apply_valuation_migration(str(db_file))
    service = CurrentResearchService(str(db_file))
    return db_file, service


def _insert_universe_instrument(
    conn: sqlite3.Connection,
    *,
    venue: str = "TWSE",
    official_code: str = "2330",
    display_name: str = "台灣積體電路製造股份有限公司",
    short_name: str = "台積電",
):
    conn.execute(
        """
        INSERT OR REPLACE INTO universe_instruments (
            instrument_id, venue, official_code, identity_epoch, identity_binding_fingerprint,
            first_observed_at, first_source_reference, source_identity, display_name, created_at
        ) VALUES (?, ?, ?, 1, ?, '2026-09-01T00:00:00Z', 'twse', '2330', ?, '2026-09-01T00:00:00Z')
        """,
        (f"inst_{venue}_{official_code}", venue, official_code, f"fp_{venue}_{official_code}", display_name),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO universe_revisions (
            universe_revision_id, resource_id, logical_revision_key, revision_number,
            fetched_at, received_at, available_at, ingested_at, status
        ) VALUES ('rev_univ_1', 'twse-universe-master', 'twse:master', 1,
                  '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', 'accepted')
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO universe_instrument_revisions (
            instrument_revision_id, instrument_id, universe_revision_id, resource_id,
            revision_number, venue, official_code, canonical_symbol, security_type,
            display_name, short_name, listing_status, trading_state, membership_state,
            received_at, fetched_at, available_at, ingested_at, availability_mode, freshness_mode,
            freshness_status, current_complete, coverage_complete, status
        ) VALUES (
            ?, ?, 'rev_univ_1', 'twse-universe-master',
            1, ?, ?, ?, '股票',
            ?, ?, 'listed', 'normal', 'active',
            '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z',
            'official_timestamp', 'official_cadence_window',
            'current', 1, 1, 'accepted'
        )
        """,
        (
            f"ir_{venue}_{official_code}",
            f"inst_{venue}_{official_code}",
            venue,
            official_code,
            f"{official_code}.TW",
            display_name,
            short_name,
        ),
    )


def _insert_snapshot_and_observation(
    conn: sqlite3.Connection,
    *,
    date: str = "2026-09-04",
    official_code: str = "2330",
    close: str | None = "980.0",
    turnover: float = 300000000000.0,
):
    import hashlib

    raw_id = f"raw_snap_{date}"
    raw_hash = hashlib.sha256(raw_id.encode()).hexdigest()
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_resource_revisions VALUES (
            ?, 'id-1', 'twse-official', 'twse.eod.stock_day_all', 'fix',
            '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z',
            '2026-09-01T00:00:00Z', ?, '1', 'schema_fp', 'hash_only',
            NULL, 'fresh', 'eligible', NULL, 'fixture'
        )
        """,
        (raw_id, raw_hash),
    )
    snap_id = f"snap_{date}"
    snap_fp = hashlib.sha256(f"snap:{date}:{snap_id}".encode()).hexdigest()
    conn.execute(
        """
        INSERT OR REPLACE INTO eod_close_source_snapshots (
            source_snapshot_id, resource_id, raw_resource_revision_id, logical_revision_key,
            revision_number, source_trade_date, source_trade_date_status, status,
            coverage_state, coverage_proof_type, coverage_proof_reference, row_count,
            source_date_min, source_date_max, source_published_at, fetched_at, received_at,
            available_at, ingested_at, source_url, http_method, response_format, contract_version,
            parser_version, schema_fingerprint, raw_payload_sha256, normalized_payload_sha256,
            query_dimensions_json, source_record_reference, source_scope, reason,
            supersedes_source_snapshot_id, revocation_reference, identity_fingerprint
        ) VALUES (
            ?, 'twse.eod.stock_day_all', ?, 'key',
            1, ?, 'valid', 'available',
            'complete', NULL, NULL, 1,
            ?, ?, NULL, '2026-09-04T13:35:00Z', '2026-09-04T13:35:00Z',
            '2026-09-04T13:35:00Z', '2026-09-04T13:36:00Z', 'http://url', 'GET', 'json', 'v1',
            '1', 'fp', ?, ?,
            '{}', 'ref', 'scope', NULL,
            NULL, NULL, ?
        )
        """,
        (snap_id, raw_id, date, date, date, raw_hash, raw_hash, snap_fp),
    )

    if close is not None:
        obs_id = f"obs_{date}_{official_code}"
        obs_fp = hashlib.sha256(f"obs:{official_code}:{date}".encode()).hexdigest()
        conn.execute(
            """
            INSERT OR REPLACE INTO eod_close_observations (
                close_observation_id, resource_id, raw_resource_revision_id, source_snapshot_id,
                classification_evidence_id, instrument_id, instrument_revision_id,
                venue, official_code, trade_date, trade_date_status, revision_number,
                supersedes_observation_id, raw_close_text, close_value, raw_volume_text, volume_value,
                raw_trade_indication_text, trade_indication_value, currency, unit,
                price_semantics_version, product_scope, observation_status, public_eligibility_status,
                quality_status, quality_flags_json, row_fingerprint, raw_payload_sha256,
                normalized_payload_sha256, source_trading_scope, available_at, ingested_at,
                source_record_reference, source_note, identity_fingerprint
            ) VALUES (
                ?, 'twse.eod.stock_day_all', ?, ?,
                NULL, NULL, NULL,
                'TWSE', ?, ?, 'valid', 1,
                NULL, ?, ?, '1000', '1000',
                '+', 'up', 'TWD', 'TWD_per_share',
                'phase14_v1', 'supported_stock', 'available', 'eligible',
                'fresh', '[]', 'rfp', ?,
                ?, 'regular', '2026-09-04T13:35:00Z', '2026-09-04T13:36:00Z',
                'ref', NULL, ?
            )
            """,
            (obs_id, raw_id, snap_id, official_code, date, close, float(close), raw_hash, raw_hash, obs_fp),
        )

    # Market turnover
    conn.execute(
        """
        INSERT OR REPLACE INTO market_turnover_daily (
            id, trade_date, twse_turnover_twd, tpex_turnover_twd, total_turnover_twd,
            twse_source, tpex_source, twse_dataset, tpex_dataset, twse_payload_hash,
            tpex_payload_hash, available_at, fetched_at, ingested_at, revision, status, quality_note
        ) VALUES (
            ?, ?, ?, 0, ?,
            'twse', 'tpex', 'twse_to', 'tpex_to', 'hash1',
            'hash2', '2026-09-04T13:35:00Z', '2026-09-04T13:35:00Z', '2026-09-04T13:36:00Z', 1, 'available', NULL
        )
        """,
        (f"to_{date}", date, turnover, turnover),
    )


def test_research_summary_composition(tmp_path):
    """Summary returns correct stock metadata, settled context, and non-synthetic placeholders."""
    db, service = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_universe_instrument(conn, official_code="2330", display_name="台積電公司", short_name="台積電")
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close="980.0")

    summary = service.get_summary("2330.TW", knowledge_cutoff_at="2026-09-04T16:00:00Z")
    assert summary is not None
    assert summary["canonical_symbol"] == "2330.TW"
    assert summary["official_code"] == "2330"
    assert summary["venue"] == "TWSE"
    assert summary["company_name"] == "台積電公司"
    assert summary["short_name"] == "台積電"

    m_ctx = summary["market_context"]
    assert m_ctx["settled_trade_date"] == "2026-09-04"
    assert m_ctx["official_close"] == 980.0
    assert m_ctx["close_status"] == "available"
    assert m_ctx["currency"] == "TWD"
    assert m_ctx["unit"] == "TWD_per_share"
    assert m_ctx["market_turnover_total"] == 300000000000.0
    assert m_ctx["market_turnover_status"] == "available"
    assert m_ctx["cbc_status"] == "insufficient_data"  # non-blocking


def test_human_decision_queue_and_no_fabrication(tmp_path):
    """Missing Forward EPS and wave anchors must yield needs_human_judgment and enter decision queue."""
    db, service = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_universe_instrument(conn, official_code="2330")
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close="980.0")

    summary = service.get_summary("2330.TW", knowledge_cutoff_at="2026-09-04T16:00:00Z")
    assert summary is not None

    # Objective fact remains visible
    assert summary["market_context"]["official_close"] == 980.0

    # Valuation context
    val_ctx = summary["valuation_context"]
    assert val_ctx["status"] == "needs_human_judgment"
    assert val_ctx["reason_code"] == "forward_eps_missing_at_knowledge_cutoff"
    assert val_ctx["target_matrix"] == []

    # Technical context
    tech_ctx = summary["technical_context"]
    assert tech_ctx["status"] == "needs_human_judgment"
    assert tech_ctx["reason_code"] == "manual_anchor_required"
    assert tech_ctx["targets"] is None

    # Decision Queue
    queue = summary["human_decision_queue"]
    assert len(queue) == 2
    rule_ids = {q["rule_id"] for q in queue}
    assert "VAL-02" in rule_ids
    assert "FB-03/FB-04" in rule_ids

    # Screening metrics: zero egress, unavailable status
    screening = summary["screening_context"]
    assert screening["pe"]["status"] == "unavailable"
    assert screening["pe"]["value"] is None
    assert screening["pe"]["ui_copy"] == "尚無可用資料"
    assert screening["pb"]["status"] == "unavailable"
    assert screening["dividend_yield"]["status"] == "unavailable"


def test_summary_fail_closed_anti_fallback(tmp_path):
    """If snapshot exists on date D but observation is missing, close is null with insufficient_data, never falling back."""
    db, service = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_universe_instrument(conn, official_code="2330")
        _insert_snapshot_and_observation(conn, date="2026-09-03", official_code="2330", close="970.0")
        # 2026-09-04 snapshot exists but without observation for 2330
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close=None)

    summary = service.get_summary("2330.TW", knowledge_cutoff_at="2026-09-04T16:00:00Z")
    assert summary is not None
    m_ctx = summary["market_context"]
    assert m_ctx["settled_trade_date"] == "2026-09-04"
    assert m_ctx["official_close"] is None
    assert m_ctx["close_status"] == "insufficient_data"
    assert m_ctx["close_reason"] == "symbol_observation_not_yet_materialized_for_settled_session"


def test_summary_api_endpoint(tmp_path, monkeypatch):
    """GET /api/v2/research/summary/{canonical_symbol} returns 200 with complete summary."""
    db, _ = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_universe_instrument(conn, official_code="2330", display_name="台積電", short_name="台積電")
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close="980.0")

    environ = {
        "TW_STOCK_PREDICTOR_ENV": "development",
        "DATABASE_PATH": str(db),
        "UNIVERSE_DB_PATH": str(db),
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
    }
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", "http://127.0.0.1:8000")
    paths = RuntimePaths.from_environment(environ)
    settings = RuntimeSettings.from_environment(environ, paths=paths)
    settings.paths.ensure_user_dirs()
    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    resp = client.get("/api/v2/research/summary/2330.TW?as_of=2026-09-04T16:00:00Z")
    assert resp.status_code == 200
    data = resp.json()
    assert data["canonical_symbol"] == "2330.TW"
    assert data["market_context"]["official_close"] == 980.0
    assert len(data["human_decision_queue"]) == 2

    # 404 on unlisted symbol
    resp404 = client.get("/api/v2/research/summary/9999.TW")
    assert resp404.status_code == 404
