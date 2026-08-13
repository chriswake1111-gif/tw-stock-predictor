from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.research_workflow import router
from src.api.workflow_security import ResearchBoundaryMiddleware, ResearchSecurityConfig


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


def test_phase12_api_typed_cutoff_and_strict_schema_errors(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        assert client.get("/api/v2/research/queue").json()["detail"] == "comparison_cutoff_required"
        headers = _write_headers(client)
        response = client.post(
            "/api/v2/research/queue", json={"symbol": "2330", "extra": True}, headers=headers
        )
        assert response.status_code == 422
