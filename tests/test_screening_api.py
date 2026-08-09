from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


def admin_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "screening-api.db"))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", "screening-secret")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase6-reviewer")
    for module in (
        "src.repositories.security_valuation_repository",
        "src.repositories.screening_repository",
    ):
        monkeypatch.setattr(
            f"{module}.utc_now_timestamp",
            lambda: "2026-01-10T12:00:00.000000Z",
        )
    return {"X-Admin-API-Key": "screening-secret"}


def headers(admin, key):
    return {**admin, "Idempotency-Key": key}


def valuation_payload(**overrides):
    result = {
        "logical_observation_id": "2330-2025-01-02",
        "revision_number": 1,
        "symbol": "2330",
        "metric_date": "2025-01-02",
        "pe": 15.0,
        "pb": 2.0,
        "dividend_yield_ratio": 0.04,
        "source_name": "authorized-research",
        "source_dataset": "daily-valuation-v1",
        "available_at": "2025-01-02T10:00:00Z",
    }
    result.update(overrides)
    return result


def profile_payload(logical_profile_id="phase6-profile", **overrides):
    result = {
        "logical_profile_id": logical_profile_id,
        "revision_number": 1,
        "scope": "global",
        "valuation_basis": "self_history",
        "valuation_source_name": "authorized-research",
        "valuation_source_dataset": "daily-valuation-v1",
        "pe_percentile_max": 30,
        "pb_percentile_max": 30,
        "dividend_yield_percentile_min": 70,
        "history_years": 5,
        "minimum_observations": 20,
        "forward_eps_source_name": "broker-a",
        "forward_eps_source_type": "broker_report",
        "technical_component": "wv03_confirmation",
        "available_at": "2026-01-05T10:00:00Z",
        "rationale": "approved project-operated research profile",
    }
    result.update(overrides)
    return result


def create_and_approve_profile(admin, logical_profile_id="phase6-profile"):
    created = client.post(
        "/api/v2/screening-profiles",
        json=profile_payload(logical_profile_id),
        headers=headers(admin, f"create-{logical_profile_id}"),
    )
    assert created.status_code == 200, created.text
    profile_id = created.json()["screening_profile"]["id"]
    approved = client.post(
        f"/api/v2/screening-profiles/{profile_id}/approval",
        json={
            "decision": "approved",
            "rationale": "reviewed percentile screening profile",
            "approved_at": "2026-01-05T11:00:00Z",
        },
        headers=headers(admin, f"approve-{logical_profile_id}"),
    )
    assert approved.status_code == 200, approved.text
    return profile_id, approved.json()["approval"]


def test_screening_writes_default_closed_and_require_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "screening-api.db"))
    monkeypatch.delenv("EVIDENCE_V2_WRITES_ENABLED", raising=False)
    disabled = client.post(
        "/api/v2/security-valuations",
        json=valuation_payload(),
        headers={"Idempotency-Key": "disabled"},
    )
    assert disabled.status_code == 503

    admin_env(monkeypatch, tmp_path)
    unauthorized = client.post(
        "/api/v2/security-valuations",
        json=valuation_payload(),
        headers={"Idempotency-Key": "unauthorized"},
    )
    assert unauthorized.status_code == 401


def test_public_dtos_hide_dedup_and_server_controls_approval(monkeypatch, tmp_path):
    admin = admin_env(monkeypatch, tmp_path)
    first = client.post(
        "/api/v2/security-valuations",
        json=valuation_payload(),
        headers=headers(admin, "valuation-a"),
    )
    second = client.post(
        "/api/v2/security-valuations",
        json=valuation_payload(),
        headers=headers(admin, "valuation-b"),
    )
    assert first.status_code == second.status_code == 200
    first_row = first.json()["valuation_observation"]
    assert first_row["id"] == second.json()["valuation_observation"]["id"]
    assert "idempotency_key" not in str(first.json())
    assert "payload_fingerprint" not in str(first.json())

    profile_id, approval = create_and_approve_profile(admin)
    assert approval["approved_by"] == "phase6-reviewer"
    assert approval["rule_id"] == "SEL-01"
    assert approval["evidence_level"] == "C"
    assert approval["implementation_mode"] == "project_operationalization"

    injected = client.post(
        "/api/v2/screening-profiles",
        json={**profile_payload("injected"), "approved_by": "caller"},
        headers=headers(admin, "injected"),
    )
    assert injected.status_code == 422
    assert profile_id


def test_screening_requires_explicit_selection_when_multiple_profiles_apply(
    monkeypatch, tmp_path
):
    admin = admin_env(monkeypatch, tmp_path)
    first_id, _ = create_and_approve_profile(admin, "profile-a")
    create_and_approve_profile(admin, "profile-b")

    ambiguous = client.get(
        "/api/v2/screening/2330?knowledge_cutoff_at=2026-01-20T00:00:00Z"
    )
    assert ambiguous.status_code == 200
    assert ambiguous.json()["status"] == "needs_human_input"
    assert ambiguous.json()["reason"] == "screening_profile_selection_required"

    explicit = client.get(
        "/api/v2/screening/2330"
        f"?knowledge_cutoff_at=2026-01-20T00:00:00Z&profile_revision_id={first_id}"
    )
    assert explicit.status_code == 200
    assert explicit.json()["status"] == "insufficient_data"
    assert explicit.json()["profile"]["profile_revision_id"] == first_id
    assert explicit.json()["automatic_order"] is False


def test_general_analysis_exposes_screening_human_input_without_legacy_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "screening-api.db"))
    response = client.get(
        "/api/v2/analysis/2330?knowledge_cutoff_at=2026-01-20T00:00:00Z"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["screening"]["status"] == "needs_human_input"
    assert body["screening"]["reason"] == "approved_screening_profile_required"
    assert "approved_screening_profile" in body["data_quality"]["needs_human_input"]
    assert body["screening"]["rules_used"] == []
    assert body["screening"]["automatic_order"] is False
