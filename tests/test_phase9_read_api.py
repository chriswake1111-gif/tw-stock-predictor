import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes.v2_valuation import _compose_analysis_status
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.liquidity import M1BMonthlyObservation, MarketTurnoverObservation
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.liquidity_repository import LiquidityRepository
from src.repositories.performance_validation_repository import (
    PerformanceValidationRepository,
)


client = TestClient(app)


def add_snapshot(
    db_path,
    *,
    symbol: str,
    mode: CaptureMode,
    created_at: str,
    suffix: str,
    status: str = "partial",
):
    return AnalysisSnapshotRepository(str(db_path)).add(
        AnalysisSnapshot(
            symbol=symbol,
            knowledge_cutoff_at="2026-08-01T00:00:00Z",
            capture_mode=mode,
            model_version="2.0.0",
            used_rule_versions={},
            source_resource_versions=[],
            manual_approval_ids=[],
            output={"status": status, "marker": suffix},
            created_at=created_at,
        ),
        f"phase9-snapshot-{suffix}",
    )


def add_stored_run(db_path, *, run_id: str, created_at: str):
    PerformanceValidationRepository(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO evaluation_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                "profile-for-read-contract",
                "phase8_mvp_v1",
                "separate_by_evaluation_origin",
                f"snapshot-hash-{run_id}",
                "manifest-for-read-contract",
                f"manifest-hash-{run_id}",
                "phase2_fixed_14_symbol_research_cohort",
                created_at,
                f"fingerprint-{run_id}",
                "completed",
            ),
        )


def add_m1b(repo: LiquidityRepository):
    repo.add_m1b(
        M1BMonthlyObservation(
            period="2026-06",
            value_raw=30_000,
            raw_unit="TWD_million",
            data_date="2026-06-30",
            available_at="2026-07-20T08:00:00Z",
            fetched_at="2026-07-20T08:01:00Z",
            source="CBC",
            source_dataset="CBC EF15M01",
        ),
        ingested_at="2026-07-20T08:02:00Z",
    )


def add_turnover(
    repo: LiquidityRepository,
    *,
    trade_date: str,
    twse: int | None,
    tpex: int | None,
    available_at: str,
):
    repo.add_turnover(
        MarketTurnoverObservation(
            trade_date=trade_date,
            twse_turnover_twd=twse,
            tpex_turnover_twd=tpex,
            twse_source="TWSE" if twse is not None else None,
            tpex_source="TPEx" if tpex is not None else None,
            twse_dataset="exchangeReport/FMTQIK" if twse is not None else None,
            tpex_dataset="tpex_daily_trading_index" if tpex is not None else None,
            available_at=available_at,
            fetched_at=available_at,
        ),
        ingested_at=available_at,
    )


def test_snapshot_list_filters_orders_pages_and_does_not_create(monkeypatch, tmp_path):
    db_path = tmp_path / "snapshots.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    created_at = "2026-08-10T00:00:00Z"
    for suffix, symbol, mode in (
        ("one", "2330.TW", CaptureMode.LIVE_REFRESH),
        ("two", "2330.TW", CaptureMode.HISTORICAL_RECONSTRUCTION),
        ("three", "2317.TW", CaptureMode.LIVE_REFRESH),
    ):
        add_snapshot(
            db_path, symbol=symbol, mode=mode, created_at=created_at, suffix=suffix
        )
    with sqlite3.connect(db_path) as conn:
        before_count = conn.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0]

    first = client.get("/api/v2/analysis/snapshots", params={"limit": 2})
    first.raise_for_status()
    body = first.json()
    assert len(body["snapshots"]) == 2
    assert body["snapshots"] == sorted(
        body["snapshots"], key=lambda item: (item["created_at"], item["snapshot_id"]),
        reverse=True,
    )
    assert body["next_before"]
    second = client.get(
        "/api/v2/analysis/snapshots",
        params={"limit": 2, "before": body["next_before"]},
    )
    second.raise_for_status()
    assert len(second.json()["snapshots"]) == 1

    filtered = client.get(
        "/api/v2/analysis/snapshots",
        params={"symbol": "2330", "capture_mode": "historical_reconstruction"},
    )
    filtered.raise_for_status()
    assert [item["symbol"] for item in filtered.json()["snapshots"]] == ["2330.TW"]
    assert filtered.json()["snapshots"][0]["capture_mode"] == "historical_reconstruction"
    assert "output" not in filtered.json()["snapshots"][0]
    assert "payload_fingerprint" not in str(filtered.json())

    with sqlite3.connect(db_path) as conn:
        after_count = conn.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0]
    assert after_count == before_count


def test_snapshot_list_empty_and_rejects_invalid_cursor(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "empty-snapshots.db"))
    empty = client.get("/api/v2/analysis/snapshots")
    empty.raise_for_status()
    assert empty.json()["snapshots"] == []
    assert empty.json()["next_before"] is None
    invalid = client.get("/api/v2/analysis/snapshots", params={"before": "timestamp-only"})
    assert invalid.status_code == 422


def test_evaluation_run_list_is_stored_metadata_only_and_deterministic(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "runs.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    created_at = "2026-08-10T01:00:00.000000Z"
    for suffix in ("a", "b", "c"):
        add_stored_run(db_path, run_id=f"evaluation_run_{suffix}", created_at=created_at)
    first = client.get("/api/v2/evaluations/runs", params={"limit": 2})
    first.raise_for_status()
    body = first.json()
    assert [item["evaluation_run_id"] for item in body["evaluation_runs"]] == [
        "evaluation_run_c",
        "evaluation_run_b",
    ]
    assert body["next_before"]
    assert "results" not in str(body)
    assert "run_fingerprint" not in str(body)
    second = client.get(
        "/api/v2/evaluations/runs",
        params={"limit": 2, "before": body["next_before"]},
    )
    second.raise_for_status()
    assert [item["evaluation_run_id"] for item in second.json()["evaluation_runs"]] == [
        "evaluation_run_a"
    ]
    completed = client.get(
        "/api/v2/evaluations/runs", params={"status": "completed"}
    )
    completed.raise_for_status()
    assert len(completed.json()["evaluation_runs"]) == 3
    assert client.get(
        "/api/v2/evaluations/runs", params={"status": "ranked"}
    ).status_code == 422


def test_evaluation_run_list_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "empty-runs.db"))
    response = client.get("/api/v2/evaluations/runs")
    response.raise_for_status()
    assert response.json() == {
        "status": "available",
        "evaluation_runs": [],
        "next_before": None,
    }


def test_market_overview_available_is_symbol_independent_and_not_a_signal(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "market.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    repo = LiquidityRepository(str(db_path))
    add_m1b(repo)
    add_turnover(
        repo,
        trade_date="2026-07-21",
        twse=800_000_000_000,
        tpex=100_000_000_000,
        available_at="2026-07-21T09:00:00Z",
    )
    response = client.get(
        "/api/v2/market-overview",
        params={"knowledge_cutoff_at": "2026-07-22T00:00:00Z"},
    )
    response.raise_for_status()
    body = response.json()
    assert body["status"] == "available"
    assert body["market_overview"]["turnover_twd"]["total"] == 900_000_000_000
    assert body["market_overview"]["reference_case"]["range_pct"] == [3.3, 3.4]
    serialized = str(body).lower()
    assert "buy_signal" not in serialized
    assert "sell_signal" not in serialized
    assert "symbol" not in body


def test_market_overview_latest_partial_preserves_complete_reference(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "partial-market.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    repo = LiquidityRepository(str(db_path))
    add_m1b(repo)
    add_turnover(
        repo,
        trade_date="2026-07-21",
        twse=800_000_000_000,
        tpex=100_000_000_000,
        available_at="2026-07-21T09:00:00Z",
    )
    add_turnover(
        repo,
        trade_date="2026-07-22",
        twse=820_000_000_000,
        tpex=None,
        available_at="2026-07-22T09:00:00Z",
    )
    response = client.get(
        "/api/v2/market-overview",
        params={"knowledge_cutoff_at": "2026-07-23T00:00:00Z"},
    )
    response.raise_for_status()
    market = response.json()["market_overview"]
    assert market["status"] == "partial"
    assert market["turnover_m1b_ratio_pct"] is None
    assert market["latest_complete_observation"]["trade_date"] == "2026-07-21"
    assert market["turnover_twd"]["total"] is None


def test_market_overview_missing_and_cutoff_safe(monkeypatch, tmp_path):
    db_path = tmp_path / "missing-market.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    repo = LiquidityRepository(str(db_path))
    add_m1b(repo)
    add_turnover(
        repo,
        trade_date="2026-07-21",
        twse=800_000_000_000,
        tpex=100_000_000_000,
        available_at="2026-07-21T09:00:00Z",
    )
    response = client.get(
        "/api/v2/market-overview",
        params={"knowledge_cutoff_at": "2026-07-19T00:00:00Z"},
    )
    response.raise_for_status()
    assert response.json()["status"] == "insufficient_data"
    assert response.json()["market_overview"]["turnover_m1b_ratio_pct"] is None


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["available"] * 5, "available"),
        (["not_applicable", "available", "available", "available", "available"], "available"),
        (["available", "partial", "available", "unsupported", "available"], "partial"),
        (["quality_warning", "insufficient_data", "unsupported", "pending", "needs_human_input"], "partial"),
        (["needs_human_input", "insufficient_data", "unsupported", "needs_human_input", "insufficient_data"], "needs_human_input"),
        (["insufficient_data", "unsupported", "pending", "unsupported", "insufficient_data"], "insufficient_data"),
    ],
)
def test_analysis_status_composition_is_deterministic_without_scoring(
    statuses, expected
):
    assert _compose_analysis_status(dict(zip(
        ("valuation", "liquidity", "technical_support", "screening", "target_confluence"),
        statuses,
    ))) == expected


def test_analysis_contract_has_authoritative_section_states_and_no_stale_phase4_reason(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "analysis-status.db"))
    response = client.get(
        "/api/v2/analysis/2330",
        params={"knowledge_cutoff_at": "2026-07-22T00:00:00Z"},
    )
    response.raise_for_status()
    body = response.json()
    assert body["status"] == body["data_quality"]["status"]
    assert set(body["data_quality"]["section_statuses"]) == {
        "valuation", "liquidity", "technical_support", "screening", "target_confluence"
    }
    assert body["wave_scenarios"] == {
        "status": "unsupported",
        "reason": "automatic_wave_scenarios_not_supported_in_v2",
    }
    assert "phase_4_not_implemented" not in str(body)
