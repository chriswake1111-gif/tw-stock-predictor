from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


def admin_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", "secret")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase5-reviewer")
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    return {"X-Admin-API-Key": "secret"}


def headers(admin, key):
    return {**admin, "Idempotency-Key": key}


def payload(**overrides):
    result = {
        "logical_campaign_id": "2330-campaign-api", "revision_number": 1,
        "symbol": "2330", "planned_total_capital": "900000",
        "triggers": [
            {"stage": 1, "trigger_type": "manual_price", "value": "990"},
            {"stage": 2, "trigger_type": "user_percentage", "value": "0.05"},
            {"stage": 3, "trigger_type": "atr_multiple", "value": "2", "reference_id": "atr-14-v1"},
        ],
        "available_at": "2026-03-01T01:00:00Z",
    }
    result.update(overrides)
    return result


def test_write_api_is_disabled_and_requires_admin_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("EVIDENCE_V2_WRITES_ENABLED", raising=False)
    disabled = client.post("/api/v2/deployment-plan", json=payload(),
                           headers={"Idempotency-Key": "x"})
    assert disabled.status_code == 503

    admin_env(monkeypatch, tmp_path)
    unauthorized = client.post("/api/v2/deployment-plan", json=payload(),
                               headers={"Idempotency-Key": "y"})
    assert unauthorized.status_code == 401


def test_plan_remains_pending_until_protected_approval(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    created = client.post("/api/v2/deployment-plan", json=payload(), headers=headers(admin, "plan"))
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "needs_human_input"
    assert body["reason"] == "approved_deployment_plan_required"
    revision_id = body["deployment_plan"]["plan_revision_id"]
    assert "idempotency_key" not in str(body)
    assert "payload_fingerprint" not in str(body)

    approved = client.post(
        f"/api/v2/deployment-plan/{revision_id}/approval",
        json={"decision": "approved", "rationale": "three external triggers reviewed",
              "approved_at": "2026-03-01T02:00:00Z"},
        headers=headers(admin, "approval"),
    )
    assert approved.status_code == 200
    approval_id = approved.json()["approval"]["approval_id"]

    result = client.get("/api/v2/deployment-plan/2330?knowledge_cutoff_at=2026-03-11T03:00:00Z").json()
    assert result["status"] == "available"
    trace = result["plans"][0]["rule_trace"]
    assert trace["rule_id"] == "ENT-02"
    assert trace["approval_id"] == approval_id
    assert trace["evidence_level"] == "A"
    assert trace["source_data_as_of"] == "2026-03-11T03:00:00.000000Z"
    assert result["plans"][0]["automatic_order"] is False


def test_incomplete_plan_and_analysis_placeholder_need_human_input(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    created = client.post(
        "/api/v2/deployment-plan", json=payload(triggers=[]), headers=headers(admin, "draft")
    )
    assert created.status_code == 200
    assert created.json()["reason"] == "deployment_triggers_required"

    analysis = client.get("/api/v2/analysis/2317?knowledge_cutoff_at=2026-03-01T03:00:00Z").json()
    assert analysis["deployment_plan"] == {
        "status": "needs_human_input", "reason": "deployment_plan_request_required",
        "symbol": "2317.TW", "plans": [], "rules_used": [],
    }
    assert "deployment_plan_request" not in analysis["data_quality"]["needs_human_input"]


def test_caller_cannot_supply_approval_or_evidence_metadata(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    injected = payload(approved_by="caller", evidence_level="A", approval_id="fake")
    response = client.post("/api/v2/deployment-plan", json=injected, headers=headers(admin, "inject"))
    assert response.status_code == 422


def test_user_percentage_cannot_be_relabelled_ent01(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    relabelled = payload()
    relabelled["triggers"][1]["rule_id"] = "ENT-01"
    response = client.post(
        "/api/v2/deployment-plan", json=relabelled, headers=headers(admin, "ent01")
    )
    assert response.status_code == 422
    assert "cannot claim" in response.json()["detail"]
