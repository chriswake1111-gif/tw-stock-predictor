from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


def enable_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.repositories.forward_eps_repository.utc_now_timestamp",
        lambda: "2026-08-01T08:00:00.000000Z",
    )
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", "test-secret")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "code-review-admin")
    return {"X-Admin-API-Key": "test-secret"}


def headers(admin, key):
    return {**admin, "Idempotency-Key": key}


def eps_payload(**overrides):
    payload = {
        "logical_series_id": "2330-2027-source-a", "revision_number": 1,
        "symbol": "2330", "fiscal_year": 2027, "eps_low": 45.0,
        "eps_base": 50.0, "eps_high": 55.0, "source_name": "Source A",
        "source_type": "manual", "published_at": "2026-08-01",
        "available_at": "2026-08-01T09:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def pe_payload(**overrides):
    payload = {
        "logical_series_id": "2330-pe-base", "revision_number": 1,
        "label": "base", "pe_value": 20.0, "rationale": "reviewed PE",
        "scope": "symbol", "symbol": "2330",
        "available_at": "2026-08-01T09:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def approval_payload(rule_id):
    return {
        "decision": "approved", "rule_id": rule_id,
        "rationale": "human evidence review completed",
        "available_at": "2026-08-01T09:02:00+08:00",
    }


def test_write_api_defaults_closed_and_requires_admin_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))
    disabled = client.post("/api/v2/forward-eps", json=eps_payload(),
                           headers={"Idempotency-Key": "disabled"})
    admin = enable_writes(monkeypatch, tmp_path)
    unauthorized = client.post("/api/v2/forward-eps", json=eps_payload(),
                               headers={"Idempotency-Key": "unauthorized"})
    assert disabled.status_code == 503
    assert unauthorized.status_code == 401
    assert admin["X-Admin-API-Key"] == "test-secret"


def test_import_is_draft_and_public_dto_hides_dedup_fields(monkeypatch, tmp_path):
    admin = enable_writes(monkeypatch, tmp_path)
    first = client.post("/api/v2/forward-eps", json=eps_payload(), headers=headers(admin, "same"))
    second = client.post("/api/v2/forward-eps", json=eps_payload(), headers=headers(admin, "same"))
    assert first.status_code == second.status_code == 200
    observation = first.json()["observation"]
    assert observation["id"] == second.json()["observation"]["id"]
    assert first.json()["approval_status"] == "draft"
    assert "idempotency_key" not in observation
    assert "payload_fingerprint" not in observation


def test_post_validation_and_caller_cannot_claim_approval(monkeypatch, tmp_path):
    admin = enable_writes(monkeypatch, tmp_path)
    cases = [
        ("/api/v2/forward-eps", eps_payload(available_at="2026-08-01T09:00:00"), "naive"),
        ("/api/v2/forward-eps", eps_payload(source_type="historical_ttm"), "ttm"),
        ("/api/v2/forward-eps", eps_payload(eps_low=60.0), "range"),
        ("/api/v2/pe-scenarios", pe_payload(pe_value=0), "pe-zero"),
        ("/api/v2/pe-scenarios", pe_payload(evidence_level="A"), "claim-evidence"),
        ("/api/v2/pe-scenarios", pe_payload(approved_by="caller"), "claim-actor"),
    ]
    for endpoint, payload, key in cases:
        response = client.post(endpoint, json=payload, headers=headers(admin, key))
        assert response.status_code == 422, (key, response.text)


def test_protected_approval_enters_matrix_and_rule_trace(monkeypatch, tmp_path):
    admin = enable_writes(monkeypatch, tmp_path)
    eps = client.post("/api/v2/forward-eps", json=eps_payload(), headers=headers(admin, "eps")).json()
    pe = client.post("/api/v2/pe-scenarios", json=pe_payload(), headers=headers(admin, "pe")).json()
    eps_id = eps["observation"]["id"]
    pe_id = pe["pe_scenario"]["id"]
    eps_approval = client.post(
        f"/api/v2/forward-eps/{eps_id}/approval", json=approval_payload("VAL-02"),
        headers=headers(admin, "approve-eps"),
    )
    pe_approval = client.post(
        f"/api/v2/pe-scenarios/{pe_id}/approval", json=approval_payload("VAL-04"),
        headers=headers(admin, "approve-pe"),
    )
    assert eps_approval.status_code == pe_approval.status_code == 200
    assert eps_approval.json()["approval"]["approved_by"] == "code-review-admin"
    explicit = client.get("/api/v2/analysis/2330?knowledge_cutoff_at=2026-08-03T00:00:00Z")
    assert explicit.status_code == 200
    valuation = explicit.json()["valuation"]
    assert valuation["status"] == "available"
    approval_ids = {eps_approval.json()["approval"]["approval_id"],
                    pe_approval.json()["approval"]["approval_id"]}
    assert set(valuation["target_matrix"][0]["approval_ids"].values()) == approval_ids
    assert approval_ids.issubset({item for trace in valuation["rules_used"] for item in trace["approval_ids"]})
    for record in valuation["forward_eps"] + valuation["pe_scenarios"]:
        assert "idempotency_key" not in record
        assert "payload_fingerprint" not in record


def test_date_only_cutoff_prevents_same_day_lookahead(monkeypatch, tmp_path):
    admin = enable_writes(monkeypatch, tmp_path)
    eps = client.post("/api/v2/forward-eps", json=eps_payload(), headers=headers(admin, "eps")).json()
    pe = client.post("/api/v2/pe-scenarios", json=pe_payload(), headers=headers(admin, "pe")).json()
    client.post(f"/api/v2/forward-eps/{eps['observation']['id']}/approval",
                json=approval_payload("VAL-02"), headers=headers(admin, "approve-eps"))
    client.post(f"/api/v2/pe-scenarios/{pe['pe_scenario']['id']}/approval",
                json=approval_payload("VAL-04"), headers=headers(admin, "approve-pe"))
    date_only = client.get("/api/v2/analysis/2330?as_of_date=2026-08-01")
    assert date_only.json()["knowledge_cutoff_at"] == "2026-07-31T16:00:00.000000Z"
    assert date_only.json()["cutoff_policy"]["mode"] == "date_start_of_day"
    assert date_only.json()["valuation"]["status"] == "insufficient_data"


def test_partial_data_does_not_fail_v2_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))
    response = client.get("/api/v2/analysis/2330?knowledge_cutoff_at=2026-08-01T00:00:00Z")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["valuation"]["status"] == "insufficient_data"
    assert data["model"]["official_affiliation"] is False


def test_draft_forward_eps_requires_human_approval(monkeypatch, tmp_path):
    admin = enable_writes(monkeypatch, tmp_path)
    created = client.post(
        "/api/v2/forward-eps", json=eps_payload(), headers=headers(admin, "draft-eps")
    )
    assert created.status_code == 200
    response = client.get(
        "/api/v2/analysis/2330?knowledge_cutoff_at=2026-08-03T00:00:00Z"
    )
    data = response.json()
    assert data["valuation"]["status"] == "needs_human_input"
    assert data["valuation"]["reason"] == "approved_forward_eps_required"
    assert "approved_forward_eps" in data["data_quality"]["needs_human_input"]
    assert data["valuation"]["forward_eps"][0]["effective_approval_status"] == "draft"


def test_pe_approval_rejects_noncanonical_rule(monkeypatch, tmp_path):
    admin = enable_writes(monkeypatch, tmp_path)
    pe = client.post(
        "/api/v2/pe-scenarios", json=pe_payload(), headers=headers(admin, "pe")
    ).json()
    response = client.post(
        f"/api/v2/pe-scenarios/{pe['pe_scenario']['id']}/approval",
        json=approval_payload("VAL-03"), headers=headers(admin, "wrong-rule"),
    )
    assert response.status_code == 422
    assert "cannot approve" in response.json()["detail"]
