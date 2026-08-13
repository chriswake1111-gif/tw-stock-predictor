from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.research_workflow import router
from src.api.workflow_security import ResearchBoundaryMiddleware, ResearchSecurityConfig
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository


ORIGIN = "http://127.0.0.1:8000"


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", ORIGIN)
    monkeypatch.setenv("RESEARCH_WORKFLOW_WRITES_ENABLED", "true")
    config = ResearchSecurityConfig.from_environment()
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(ResearchBoundaryMiddleware, config=config)
    return TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000))


def _write_headers(client):
    bootstrap = client.get("/api/v2/research/csrf-token", headers={"origin": ORIGIN})
    assert bootstrap.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": bootstrap.json()["csrf_token"]}


def test_phase12_api_empty_queue_and_membership_lifecycle(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        cutoff = "2026-08-13T00:00:00Z"
        empty = client.get("/api/v2/research/queue", params={"comparison_cutoff": cutoff})
        assert empty.status_code == 200 and empty.json()["items"] == []
        headers = _write_headers(client)
        created = client.post("/api/v2/research/queue", json={"symbol": "2330"}, headers=headers)
        assert created.status_code == 201
        item_id = created.json()["watchlist_item_id"]
        duplicate = client.post("/api/v2/research/queue", json={"symbol": "2330.TW"}, headers=headers)
        assert duplicate.status_code == 200 and duplicate.json()["created"] is False
        assert client.post(f"/api/v2/research/queue/{item_id}/archive", json={}, headers=headers).status_code == 200
        restored = client.post("/api/v2/research/queue", json={"symbol": "2330"}, headers=headers)
        assert restored.status_code == 200 and restored.json()["restored"] is True
        assert restored.json()["watchlist_item_id"] == item_id

        archive_extra = client.post(
            f"/api/v2/research/queue/{item_id}/archive",
            json={"extra": True}, headers=headers,
        )
        assert archive_extra.status_code == 422
        detail = client.get(
            f"/api/v2/research/queue/{item_id}", params={"comparison_cutoff": cutoff}
        )
        assert detail.json()["watchlist_item"]["membership_state"] == "active"
        unarchive_extra = client.post(
            f"/api/v2/research/queue/{item_id}/unarchive",
            json={"extra": True}, headers=headers,
        )
        assert unarchive_extra.status_code == 422
        assert client.get(
            f"/api/v2/research/queue/{item_id}", params={"comparison_cutoff": cutoff}
        ).json()["watchlist_item"]["membership_state"] == "active"


def test_phase12_api_typed_cutoff_and_strict_schema_errors(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        assert client.get("/api/v2/research/queue").json()["detail"] == "comparison_cutoff_required"
        naive = client.get(
            "/api/v2/research/queue", params={"comparison_cutoff": "2026-08-13T00:00:00"}
        )
        assert naive.status_code == 422
        assert client.get(
            "/api/v2/research/queue",
            params={"comparison_cutoff": "2026-08-13T00:00:00Z", "limit": 51},
        ).status_code == 422
        headers = _write_headers(client)
        response = client.post(
            "/api/v2/research/queue", json={"symbol": "2330", "extra": True}, headers=headers
        )
        assert response.status_code == 422


def test_phase12_api_acknowledgment_idempotency_and_cutoff(monkeypatch, tmp_path):
    db_path = str(tmp_path / "api.db")
    with _client(monkeypatch, tmp_path) as client:
        headers = _write_headers(client)
        item = client.post(
            "/api/v2/research/queue", json={"symbol": "2330"}, headers=headers
        ).json()
        snapshot = AnalysisSnapshotRepository(db_path).add(
            AnalysisSnapshot(
                symbol="2330.TW",
                knowledge_cutoff_at="2026-08-01T00:00:00Z",
                capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
                model_version="2.0.0",
                used_rule_versions={},
                source_resource_versions=[],
                manual_approval_ids=[],
                output={"status": "available", "symbol": "2330.TW"},
                created_at="2026-08-02T00:00:00Z",
            ),
            "phase12-api-snapshot",
        )
        path = f"/api/v2/research/queue/{item['watchlist_item_id']}/acknowledgments"
        payload = {
            "acknowledged_snapshot_id": snapshot["snapshot_id"],
            "comparison_cutoff": "2026-08-03T00:00:00Z",
        }
        first = client.post(
            path, json=payload, headers={**headers, "idempotency-key": "ack-key"}
        )
        retry = client.post(
            path, json=payload, headers={**headers, "idempotency-key": "ack-key"}
        )
        assert first.status_code == 201
        assert retry.status_code == 200
        assert retry.json()["review_event_id"] == first.json()["review_event_id"]
        conflict = client.post(
            path,
            json={**payload, "comparison_cutoff": "2026-08-04T00:00:00Z"},
            headers={**headers, "idempotency-key": "ack-key"},
        )
        assert conflict.status_code == 409
        future = client.post(
            path,
            json={**payload, "comparison_cutoff": "2999-01-01T00:00:00Z"},
            headers={**headers, "idempotency-key": "future-key"},
        )
        assert future.status_code == 422
        naive_review = client.post(
            path,
            json={**payload, "comparison_cutoff": "2026-08-03T00:00:00"},
            headers={**headers, "idempotency-key": "naive-key"},
        )
        assert naive_review.status_code == 422
        before_review = client.get(
            f"/api/v2/research/queue/{item['watchlist_item_id']}",
            params={"comparison_cutoff": "2026-08-02T00:00:00Z"},
        )
        assert before_review.status_code == 422


def test_phase12_api_database_failure_is_not_an_empty_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path))
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", ORIGIN)
    config = ResearchSecurityConfig.from_environment()
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(ResearchBoundaryMiddleware, config=config)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/api/v2/research/queue",
            params={"comparison_cutoff": "2026-08-13T00:00:00Z"},
        )
    assert response.status_code == 500
    assert response.json()["detail"] == "research_workflow_unavailable"
