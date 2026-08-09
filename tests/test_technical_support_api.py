from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


def admin_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", "secret")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase4-reviewer")
    monkeypatch.setattr(
        "src.repositories.technical_anchor_repository.utc_now_timestamp",
        lambda: "2026-02-15T00:01:00.000000Z",
    )
    return {"X-Admin-API-Key": "secret"}


def headers(admin, key):
    return {**admin, "Idempotency-Key": key}


def anchor_payload(symbol="2330", **overrides):
    payload = {
        "logical_anchor_set_id": f"{symbol}-fb03-1",
        "revision_number": 1,
        "symbol": symbol,
        "evidence_basis_rule_id": "FB-03",
        "anchors": [
            {"role": "origin", "price": 100, "market_date": "2026-01-10"},
            {"role": "swing_end", "price": 150, "market_date": "2026-01-20"},
            {"role": "projection_origin", "price": 130, "market_date": "2026-02-01"},
        ],
        "available_at": "2026-02-15T00:00:00Z",
        "source": "manual_research",
    }
    payload.update(overrides)
    return payload


def test_missing_and_draft_anchor_need_human_input_without_breaking_other_sections(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    empty = client.get("/api/v2/analysis/2330?knowledge_cutoff_at=2026-02-16T00:00:00Z")
    assert empty.status_code == 200
    empty_data = empty.json()
    assert empty_data["technical_support"]["reason"] == "manual_anchor_required"
    assert "technical_support" in empty_data["data_quality"]["missing_sections"]
    assert "manual_anchor" in empty_data["data_quality"]["needs_human_input"]

    created = client.post("/api/v2/anchors", json=anchor_payload(), headers=headers(admin, "anchor"))
    assert created.status_code == 200
    draft = client.get("/api/v2/analysis/2330?knowledge_cutoff_at=2026-02-16T00:00:00Z").json()
    assert draft["technical_support"]["status"] == "needs_human_input"
    assert draft["technical_support"]["reason"] == "approved_manual_anchor_required"
    assert "approved_manual_anchor" in draft["data_quality"]["needs_human_input"]


def test_approved_anchor_generates_traceable_scenario_and_is_symbol_isolated(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    created = client.post("/api/v2/anchors", json=anchor_payload(), headers=headers(admin, "anchor")).json()
    anchor_id = created["anchor_set"]["id"]
    approved = client.post(
        f"/api/v2/anchors/{anchor_id}/approval",
        json={"decision": "approved", "rule_id": "FB-03", "rationale": "reviewed",
              "approved_at": "2026-02-15T00:00:30Z"},
        headers=headers(admin, "approval"),
    )
    assert approved.status_code == 200
    approval_id = approved.json()["approval"]["approval_id"]

    data = client.get("/api/v2/analysis/2330?knowledge_cutoff_at=2026-02-16T00:00:00Z").json()
    scenario = data["technical_support"]["scenarios"][0]
    assert scenario["calculated_level"] == 180.0
    assert scenario["rule_trace"]["rule_id"] == "FB-03"
    assert scenario["rule_trace"]["approval_id"] == approval_id
    assert {trace["rule_id"] for trace in data["rules_used"]} >= {"FB-03"}
    assert "manual_anchor" not in data["data_quality"]["needs_human_input"]
    assert "approved_manual_anchor" not in data["data_quality"]["needs_human_input"]

    other = client.get("/api/v2/analysis/2317?knowledge_cutoff_at=2026-02-16T00:00:00Z").json()
    assert other["technical_support"]["status"] == "needs_human_input"


def test_revoked_anchor_approval_requires_new_human_approval(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    created = client.post(
        "/api/v2/anchors", json=anchor_payload(), headers=headers(admin, "anchor")
    ).json()
    anchor_id = created["anchor_set"]["id"]
    client.post(
        f"/api/v2/anchors/{anchor_id}/approval",
        json={"decision": "approved", "rule_id": "FB-03", "rationale": "reviewed",
              "approved_at": "2026-02-15T00:00:30Z"},
        headers=headers(admin, "approve"),
    )
    revoked = client.post(
        f"/api/v2/anchors/{anchor_id}/approval",
        json={"decision": "revoked", "rule_id": "FB-03", "rationale": "approval withdrawn",
              "approved_at": "2026-02-15T00:00:45Z"},
        headers=headers(admin, "revoke"),
    )
    assert revoked.status_code == 200

    data = client.get(
        "/api/v2/analysis/2330?knowledge_cutoff_at=2026-02-16T00:00:00Z"
    ).json()
    assert data["technical_support"]["status"] == "needs_human_input"
    assert data["technical_support"]["reason"] == "anchor_approval_revoked"
    assert data["technical_support"]["scenarios"] == []
    assert "approved_manual_anchor" in data["data_quality"]["needs_human_input"]


def test_unsupported_ratio_and_wrong_rule_cannot_enter_phase4(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    with_ratio = anchor_payload(ratio=0.618)
    assert client.post("/api/v2/anchors", json=with_ratio, headers=headers(admin, "ratio")).status_code == 422
    wrong_rule = anchor_payload(evidence_basis_rule_id="FB-05")
    assert client.post("/api/v2/anchors", json=wrong_rule, headers=headers(admin, "rule")).status_code == 422


def test_anchor_idempotency_key_is_permanently_bound(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    first = client.post("/api/v2/anchors", json=anchor_payload(), headers=headers(admin, "key-a"))
    second = client.post("/api/v2/anchors", json=anchor_payload(), headers=headers(admin, "key-b"))
    changed = client.post(
        "/api/v2/anchors", json=anchor_payload(source_note="different"),
        headers=headers(admin, "key-b"),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["anchor_set"]["id"] == second.json()["anchor_set"]["id"]
    assert changed.status_code == 422
