import gc
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.settings import RuntimePaths, RuntimeSettings


@pytest.fixture
def api(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / "data" / "research.db"
        env = {
            "TW_STOCK_PACKAGED": "0",
            "TW_STOCK_DATA_ROOT": str(root / "data"),
            "DATABASE_PATH": str(db),
            "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
            "RESEARCH_WORKFLOW_WRITES_ENABLED": "true",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        paths = RuntimePaths.from_environment(env)
        settings = RuntimeSettings.from_environment(env, paths=paths)
        paths.ensure_user_dirs()
        apply_valuation_migration(str(db))
        app = create_app(settings=settings)
        app.state.launch_handshake = {"launch_id": "local-test"}
        with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
            yield client
        del client, app
        gc.collect()


def csrf(client):
    response = client.get("/api/v2/data-operations/csrf-token")
    assert response.status_code == 200
    return {
        "Origin": "http://127.0.0.1:8000",
        "X-CSRF-Token": response.json()["csrf_token"],
        "Content-Type": "application/json",
    }


EPS = {
    "values": {
        "fiscal_year": 2026, "eps_base": 100,
        "source": "使用者研究", "source_date": "2026-09-01", "rationale": "預估年度獲利",
    }
}


def test_missing_handshake_returns_503(api):
    api.app.state.launch_handshake = None
    response = api.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers=csrf(api) | {"Idempotency-Key": "missing-handshake-1"}, json=EPS)
    assert response.status_code == 503
    assert "launch_handshake_missing" in response.json()["detail"]


def test_non_loopback_origin_and_csrf_are_rejected(api):
    response = api.post("/api/v2/research/assumptions/2330.TW/eps/draft",
                        headers={"Origin": "http://127.0.0.1:8000", "Content-Type": "application/json", "Idempotency-Key": "no-csrf-1"}, json=EPS)
    assert response.status_code == 403
    with TestClient(api.app, base_url="http://127.0.0.1:8000", client=("192.0.2.10", 50000)) as remote:
        response = remote.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers={
            "Origin": "http://127.0.0.1:8000", "X-CSRF-Token": "dummy",
            "Content-Type": "application/json", "Idempotency-Key": "remote-1",
        }, json=EPS)
        assert response.status_code == 403


@pytest.mark.parametrize("extra", ["available_at", "approved_by"])
def test_draft_rejects_caller_controlled_fields(api, extra):
    body = {**EPS, extra: "2026-09-11T00:00:00Z"}
    response = api.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers=csrf(api) | {"Idempotency-Key": f"extra-{extra}"}, json=body)
    assert response.status_code == 422


def test_legal_draft_approve_revoke_and_cross_symbol_resource_rejection(api):
    draft = api.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers=csrf(api) | {"Idempotency-Key": "legal-draft-1"}, json=EPS)
    assert draft.status_code == 200
    resource_id = draft.json()["record"]["id"]
    approved = api.post(f"/api/v2/research/assumptions/2330.TW/eps/{resource_id}/approve",
                        headers=csrf(api) | {"Idempotency-Key": "approve-eps-1"}, json={"rationale": "確認適用"})
    assert approved.status_code == 200
    revoked = api.post(f"/api/v2/research/assumptions/2330.TW/eps/{resource_id}/revoke",
                        headers=csrf(api) | {"Idempotency-Key": "revoke-eps-1"}, json={"rationale": "撤回"})
    assert revoked.status_code == 200
    cross = api.post(f"/api/v2/research/assumptions/2408.TW/eps/{resource_id}/approve",
                     headers=csrf(api) | {"Idempotency-Key": "cross-symbol-1"}, json={"rationale": "錯誤標的"})
    assert cross.status_code == 422


def test_idempotency_same_payload_retries_and_different_payload_conflicts(api):
    headers = csrf(api) | {"Idempotency-Key": "stable-eps-1"}
    first = api.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers=headers, json=EPS)
    second = api.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers=headers, json=EPS)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    different = {"values": {**EPS["values"], "eps_base": 200}}
    conflict = api.post("/api/v2/research/assumptions/2330.TW/eps/draft", headers=headers, json=different)
    assert conflict.status_code == 409


def test_packaged_daily_commands_work_without_enabling_other_workflows(tmp_path, monkeypatch):
    from tests.test_phase19_installed_smoke import _create_packaged_settings
    settings, coordinator = _create_packaged_settings(tmp_path)
    startup = coordinator.prepare()
    monkeypatch.setenv("DATABASE_PATH", str(settings.paths.database_path))
    monkeypatch.setenv("RESEARCH_WORKFLOW_WRITES_ENABLED", "false")
    app = create_app(settings=settings, startup_result=startup)
    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        headers = csrf(client) | {"Idempotency-Key": "installed-only-eps"}
        path = "/api/v2/research/assumptions/2330.TW/eps/draft"
        assert client.post(path, headers=headers, json=EPS).status_code == 503
        app.state.launch_handshake = {"launch_id": "verified-installed-test"}
        assert client.post(path, headers=headers, json=EPS).status_code == 200
        assert client.post(path, headers={"Origin": "http://127.0.0.1:8000"}, json=EPS).status_code == 403
        assert client.post(path, headers={**headers, "Origin": "https://example.com"}, json=EPS).status_code == 403
        response = client.post("/api/v2/research/queue", headers=headers, json={"symbol": "2330.TW"})
        assert response.status_code == 201
        blocked = client.post("/api/v2/research/queue/unknown/snapshot-refresh", headers=headers, json={})
        assert blocked.status_code == 503
        assert blocked.json()["detail"] == "research_workflow_writes_disabled"
    del app
    gc.collect()
