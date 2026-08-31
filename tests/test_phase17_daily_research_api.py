from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.daily_research import router as daily_router
from src.api.routes.research_workflow import router as workflow_router
from src.api.workflow_security import ResearchBoundaryMiddleware, ResearchSecurityConfig
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.research_workflow_repository import ResearchWorkflowRepository


ORIGIN = "http://127.0.0.1:8000"


def _client(monkeypatch, tmp_path):
    db_path = str(tmp_path / "phase17.db")
    apply_valuation_migration(db_path)
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", ORIGIN)
    monkeypatch.setenv("RESEARCH_WORKFLOW_WRITES_ENABLED", "true")
    config = ResearchSecurityConfig.from_environment()
    app = FastAPI()
    app.include_router(workflow_router)
    app.include_router(daily_router)
    app.add_middleware(ResearchBoundaryMiddleware, config=config)
    return TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)), db_path


def _write_headers(client):
    bootstrap = client.get("/api/v2/research/csrf-token", headers={"origin": ORIGIN})
    assert bootstrap.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": bootstrap.json()["csrf_token"]}


def _snapshot(repo: AnalysisSnapshotRepository, *, symbol: str, created_at: str, key: str):
    return repo.add(
        AnalysisSnapshot(
            symbol=symbol,
            knowledge_cutoff_at="2026-08-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={},
            source_resource_versions=[],
            manual_approval_ids=[],
            output={"status": "available", "symbol": symbol},
            created_at=created_at,
        ),
        key,
    )


def test_daily_context_is_strict_as_of_active_only_and_schema_safe(monkeypatch, tmp_path):
    client, db_path = _client(monkeypatch, tmp_path)
    with sqlite3.connect(db_path) as conn:
        migration_state_before = conn.execute(
            "SELECT version_id, checksum_sha256 FROM schema_migrations ORDER BY version_id"
        ).fetchall()
        user_version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    with client:
        empty = client.get(
            "/api/v2/research/daily-context",
            params={
                "market_date": "2026-08-31",
                "knowledge_cutoff_at": "2026-08-31T00:00:00Z",
            },
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["status_scope"] == "page_items"
        assert empty.json()["preflight"]["status_scope"] == "full_daily_preflight"
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT version_id, checksum_sha256 FROM schema_migrations ORDER BY version_id"
            ).fetchall() == migration_state_before
            assert conn.execute("PRAGMA user_version").fetchone()[0] == user_version_before

        headers = _write_headers(client)
        first = client.post("/api/v2/research/queue", json={"symbol": "2330"}, headers=headers)
        archived = client.post(
            f"/api/v2/research/queue/{first.json()['watchlist_item_id']}/archive",
            json={},
            headers=headers,
        )
        assert first.status_code == 201 and archived.status_code == 200
        active = client.post("/api/v2/research/queue", json={"symbol": "2317"}, headers=headers)
        assert active.status_code == 201
        item_id = active.json()["watchlist_item_id"]
        baseline = _snapshot(
            AnalysisSnapshotRepository(db_path),
            symbol="2317.TW",
            created_at="2026-08-02T00:00:00Z",
            key="daily-baseline-snapshot",
        )

        response = client.get(
            "/api/v2/research/daily-context",
            params={
                "market_date": "2026-08-31",
                "knowledge_cutoff_at": "2026-08-31T00:00:00Z",
                "limit": 1,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [item["canonical_symbol"] for item in payload["items"]] == ["2317.TW"]
        assert payload["preflight"]["active_queue_total_count"] == 1
        assert payload["preflight"]["active_population_checksum"]
        assert payload["items"][0]["permitted_actions"]["acknowledge"] is True
        assert payload["items"][0]["latest_snapshot_reference"]["snapshot_id"] == baseline["snapshot_id"]
        assert "idempotency_key" not in str(payload)

        second = client.get(
            "/api/v2/research/daily-context",
            params={
                "market_date": "2026-08-31",
                "knowledge_cutoff_at": "2026-08-31T00:00:00Z",
                "limit": 1,
                "cursor": payload["next_cursor"] or "unused",
            },
        )
        assert second.status_code == 422 if payload["next_cursor"] is None else second.status_code == 200

        assert client.get(
            "/api/v2/research/daily-context",
            params={
                "market_date": "2026-09-01",
                "knowledge_cutoff_at": "2026-08-31T00:00:00Z",
            },
        ).status_code == 422
        assert client.get(
            "/api/v2/research/daily-context",
            params={
                "market_date": "2026-08-31",
                "knowledge_cutoff_at": "2026-08-31T00:00:00",
            },
        ).status_code == 422

        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM research_review_events"
            ).fetchone()[0] == 0


def test_daily_baseline_selection_is_namespaced_idempotent_and_cutoff_safe(monkeypatch, tmp_path):
    client, db_path = _client(monkeypatch, tmp_path)
    with client:
        headers = _write_headers(client)
        item = client.post("/api/v2/research/queue", json={"symbol": "2330"}, headers=headers).json()
        snapshot = _snapshot(
            AnalysisSnapshotRepository(db_path),
            symbol="2330.TW",
            created_at="2026-08-02T00:00:00Z",
            key="daily-selection-snapshot",
        )
        path = f"/api/v2/research/daily-context/{item['watchlist_item_id']}/baseline-selections"
        payload = {
            "baseline_snapshot_id": snapshot["snapshot_id"],
            "knowledge_cutoff_at": "2026-08-03T00:00:00Z",
        }
        first = client.post(path, json=payload, headers={**headers, "idempotency-key": "daily-key"})
        retry = client.post(path, json=payload, headers={**headers, "idempotency-key": "daily-key"})
        assert first.status_code == 201
        assert retry.status_code == 200
        assert retry.json()["baseline_selection_event"]["created"] is False
        assert "idempotency_key" not in str(first.json())

        with sqlite3.connect(db_path) as conn:
            stored_key = conn.execute(
                "SELECT idempotency_key FROM research_review_events WHERE review_event_id=?",
                (first.json()["baseline_selection_event"]["review_event_id"],),
            ).fetchone()[0]
        assert stored_key.startswith("daily-baseline-selection-v1:")
        assert stored_key != "daily-key"

        conflict = client.post(
            path,
            json={**payload, "knowledge_cutoff_at": "2026-08-04T00:00:00Z"},
            headers={**headers, "idempotency-key": "daily-key"},
        )
        assert conflict.status_code == 409

        future = client.post(
            path,
            json={**payload, "knowledge_cutoff_at": "2999-01-01T00:00:00Z"},
            headers={**headers, "idempotency-key": "future-daily-key"},
        )
        assert future.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {
            "market_date": "2026-08-31",
            "loaded_knowledge_cutoff_at": "2026-08-31T00:00:00Z",
            "expected_snapshot_id": None,
            "advance_knowledge_cutoff": False,
        },
        {
            "market_date": "2026-08-31",
            "loaded_knowledge_cutoff_at": "2026-08-31T00:00:00Z",
            "expected_snapshot_id": None,
            "advance_knowledge_cutoff": True,
            "unexpected": True,
        },
    ],
)
def test_daily_refresh_requires_explicit_contract(monkeypatch, tmp_path, payload):
    client, _ = _client(monkeypatch, tmp_path)
    with client:
        headers = _write_headers(client)
        item = client.post("/api/v2/research/queue", json={"symbol": "2330"}, headers=headers).json()
        response = client.post(
            f"/api/v2/research/queue/{item['watchlist_item_id']}/snapshot-refresh",
            json=payload,
            headers={**headers, "idempotency-key": "refresh-contract-key"},
        )
        assert response.status_code == 422
