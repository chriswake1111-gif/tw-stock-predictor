"""Phase 20 WP03: Tests for Latest-Settled Resolver, Two-Stage CTE, and Fail-Closed Anti-Fallback (BC-IP-1, P1-1)."""

from __future__ import annotations

import hashlib
import sqlite3
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.current_research_repository import CurrentResearchRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.settings import RuntimePaths, RuntimeSettings
from tests.phase13_test_support import seed_raw_provenance


def _setup_db(tmp_path):
    db = tmp_path / "phase20_research.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    repo = CurrentResearchRepository(str(db))
    return db, repo


def _insert_raw(conn: sqlite3.Connection, raw_id: str, resource_id: str) -> None:
    raw_hash = hashlib.sha256(raw_id.encode()).hexdigest()
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_resource_revisions VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            raw_id, f"identity-{raw_id}", "twse-official", resource_id, "fixture",
            "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z",
            "2026-09-01T00:00:00Z", raw_hash, "1", "schema_hash", "hash_only",
            None, "fresh", "eligible", None, "fixture",
        ),
    )


def _insert_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
    *,
    resource_id: str = "twse.eod.stock_day_all",
    raw_id: str = "raw_snap",
    date: str = "2026-09-04",
    revision_number: int = 1,
    status: str = "available",
    available_at: str = "2026-09-04T13:35:00Z",
    ingested_at: str = "2026-09-04T13:36:00Z",
) -> None:
    _insert_raw(conn, raw_id, resource_id)
    identity_fp = hashlib.sha256(f"{resource_id}:{date}:{revision_number}:{snapshot_id}".encode()).hexdigest()
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
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            snapshot_id, resource_id, raw_id, f"{resource_id}:{date}",
            revision_number, date, "valid", status,
            "complete", None, None, 1,
            date, date, None, available_at, available_at,
            available_at, ingested_at, "http://twse.gov.tw", "GET", "json", "eod_close_v1",
            "1", "schema_fp", "raw_hash", "norm_hash",
            "{}", "ref", "twse_whole_market", None,
            None, None, identity_fp,
        ),
    )


def _insert_observation(
    conn: sqlite3.Connection,
    obs_id: str,
    snapshot_id: str,
    *,
    official_code: str = "2330",
    venue: str = "TWSE",
    date: str = "2026-09-04",
    close_value: str = "980.0",
    revision_number: int = 1,
    observation_status: str = "available",
    public_eligibility_status: str = "eligible",
    available_at: str = "2026-09-04T13:35:00Z",
    ingested_at: str = "2026-09-04T13:36:00Z",
) -> None:
    identity_fp = hashlib.sha256(f"{venue}:{official_code}:{date}:{revision_number}:{obs_id}".encode()).hexdigest()
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
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            obs_id, "twse.eod.stock_day_all", "raw_obs", snapshot_id,
            None, None, None,
            venue, official_code, date, "valid", revision_number,
            None, close_value, close_value, "1000", "1000",
            "+", "up", "TWD", "TWD_per_share",
            "phase14_v1", "supported_stock", observation_status, public_eligibility_status,
            "fresh", "[]", "rfp", "raw_hash",
            "norm_hash", "regular", available_at, ingested_at,
            "ref", None, identity_fp,
        ),
    )


def test_bc_ip_1_same_date_snapshot_revision_determinism(tmp_path):
    """BC-IP-1: When date D has multiple snapshot revisions, date_rev_rank=1 selects the latest revision."""
    db, repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        # Revision 1 on date 2026-09-04
        _insert_snapshot(
            conn, "snap_v1", date="2026-09-04", revision_number=1,
            ingested_at="2026-09-04T14:00:00Z",
        )
        _insert_observation(
            conn, "obs_v1", "snap_v1", official_code="2330", date="2026-09-04",
            close_value="980.0", revision_number=1,
        )

        # Revision 2 on SAME date 2026-09-04 with corrected close price 985.0
        _insert_snapshot(
            conn, "snap_v2", date="2026-09-04", revision_number=2,
            ingested_at="2026-09-04T15:00:00Z",
        )
        _insert_observation(
            conn, "obs_v2", "snap_v2", official_code="2330", date="2026-09-04",
            close_value="985.0", revision_number=2,
        )

    context = repo.resolve_latest_settled_context("2330.TW", cutoff="2026-09-05T00:00:00Z")
    assert context["settled_trade_date"] == "2026-09-04"
    assert context["official_close"]["status"] == "available"
    assert context["official_close"]["value"] == 985.0
    assert context["official_close"]["snapshot_id"] == "snap_v2"
    assert context["official_close"]["observation_id"] == "obs_v2"


def test_p1_1_fail_closed_anti_fallback_rule(tmp_path):
    """P1-1: If symbol observation is missing on latest settled date D, return insufficient_data; do NOT fall back to D-1."""
    db, repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        # Date D-1 (2026-09-03): observation for 2330 exists (970.0)
        _insert_snapshot(conn, "snap_d_minus_1", date="2026-09-03", revision_number=1)
        _insert_observation(
            conn, "obs_d_minus_1", "snap_d_minus_1", official_code="2330",
            date="2026-09-03", close_value="970.0",
        )

        # Date D (2026-09-04): latest settled session snapshot exists, BUT 2330 is NOT present
        _insert_snapshot(conn, "snap_d", date="2026-09-04", revision_number=1)

    context = repo.resolve_latest_settled_context("2330.TW", cutoff="2026-09-05T00:00:00Z")
    # Must report settled session as D (2026-09-04)
    assert context["settled_trade_date"] == "2026-09-04"
    # Official close must be insufficient_data, NOT 970.0!
    assert context["official_close"]["status"] == "insufficient_data"
    assert context["official_close"]["value"] is None
    assert context["official_close"]["reason"] == "symbol_observation_not_yet_materialized_for_settled_session"
    assert context["official_close"]["snapshot_id"] == "snap_d"


def test_sc_05_weekend_market_status(tmp_path):
    """SC-05: Weekend cutoff derives is_market_closed=true and label '休市中（週末非交易日）'."""
    db, repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot(conn, "snap_fri", date="2026-09-04", revision_number=1)
        _insert_observation(conn, "obs_fri", "snap_fri", official_code="2330", date="2026-09-04", close_value="980.0")

    # 2026-09-06 is a Sunday
    context = repo.resolve_latest_settled_context("2330.TW", cutoff="2026-09-06T10:00:00Z")
    assert context["market_context"]["settled_trade_date"] == "2026-09-04"
    assert context["market_context"]["is_market_closed"] is True
    assert context["market_context"]["market_status_label"] == "休市中（週末非交易日）"


def test_calendar_holiday_market_status(tmp_path):
    """Calendar holiday derives is_market_closed=true and holiday name in label."""
    db, repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot(conn, "snap_prev", date="2026-10-08", revision_number=1)
        _insert_observation(conn, "obs_prev", "snap_prev", official_code="2330", date="2026-10-08", close_value="990.0")

        # 2026-10-09 (Friday) is a designated holiday (e.g. National Day observed)
        conn.execute(
            """
            INSERT INTO trading_calendar_revisions VALUES (
                'cal_1009', 'raw_cal', 'TW', '2026-10-09', 'holiday',
                '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1, 'available', '國慶日'
            )
            """
        )

    # Cutoff on 2026-10-09 (Taipei time, Friday)
    context = repo.resolve_latest_settled_context("2330.TW", cutoff="2026-10-09T02:00:00Z")
    assert context["market_context"]["is_market_closed"] is True
    assert context["market_context"]["market_status_label"] == "休市中（國慶日）"


def test_market_turnover_aligned_to_settled_date(tmp_path):
    """Market turnover is joined for the resolved settled date."""
    db, repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot(conn, "snap_d", date="2026-09-04", revision_number=1)
        _insert_observation(conn, "obs_d", "snap_d", official_code="2330", date="2026-09-04", close_value="980.0")

        conn.execute(
            """
            INSERT INTO market_turnover_daily (
                id, trade_date, twse_turnover_twd, tpex_turnover_twd, total_turnover_twd,
                twse_source, tpex_source, twse_dataset, tpex_dataset, twse_payload_hash,
                tpex_payload_hash, available_at, fetched_at, ingested_at, revision, status,
                quality_note
            ) VALUES (
                'to_0904', '2026-09-04', 350000000000.0, 100000000000.0, 450000000000.0,
                'twse', 'tpex', 'set', 'set', 'h1', 'h2',
                '2026-09-04T15:00:00Z', '2026-09-04T15:00:00Z', '2026-09-04T15:00:00Z',
                1, 'available', 'normal'
            )
            """
        )

    context = repo.resolve_latest_settled_context("2330.TW", cutoff="2026-09-05T00:00:00Z")
    assert context["market_context"]["market_turnover_total"] == 450000000000.0


def test_api_v2_research_context_current(tmp_path, monkeypatch):
    """API endpoint GET /api/v2/research/context/current/{canonical_symbol} returns 200 with full payload."""
    db, repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot(conn, "snap_api", date="2026-09-04", revision_number=1)
        _insert_observation(conn, "obs_api", "snap_api", official_code="2330", date="2026-09-04", close_value="980.0")

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

    resp = client.get("/api/v2/research/context/current/2330.TW?knowledge_cutoff_at=2026-09-05T00:00:00Z")
    assert resp.status_code == 200
    data = resp.json()
    assert data["canonical_symbol"] == "2330.TW"
    assert data["settled_trade_date"] == "2026-09-04"
    assert data["official_close"]["status"] == "available"
    assert data["official_close"]["value"] == 980.0
    assert "market_context" in data
