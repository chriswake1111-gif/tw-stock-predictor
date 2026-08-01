from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


def eps_payload(**overrides):
    payload = {
        "logical_series_id": "2330-2027-source-a",
        "revision_number": 1,
        "symbol": "2330",
        "fiscal_year": 2027,
        "eps_low": 45.0,
        "eps_base": 50.0,
        "eps_high": 55.0,
        "source_name": "Source A",
        "source_type": "manual",
        "published_at": "2026-08-01",
        "available_at": "2026-08-01T09:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def pe_payload(**overrides):
    payload = {
        "logical_series_id": "2330-pe-base",
        "revision_number": 1,
        "label": "base",
        "pe_value": 20.0,
        "rationale": "manual approved PE",
        "evidence_level": "B",
        "scope": "symbol",
        "symbol": "2330",
        "available_at": "2026-08-01T09:00:00+08:00",
        "approval_status": "approved",
        "approved_by": "reviewer",
        "approved_at": "2026-08-01T09:01:00+08:00",
    }
    payload.update(overrides)
    return payload


def test_duplicate_post_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))
    headers = {"Idempotency-Key": "same-request"}

    first = client.post("/api/v2/forward-eps", json=eps_payload(), headers=headers)
    second = client.post("/api/v2/forward-eps", json=eps_payload(), headers=headers)
    conflict = client.post(
        "/api/v2/forward-eps",
        json=eps_payload(eps_base=51.0),
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["observation"]["id"] == second.json()["observation"]["id"]
    assert first.json()["observation"]["created"] is True
    assert second.json()["observation"]["created"] is False
    assert conflict.status_code == 422


def test_post_validation_rejects_naive_time_ttm_source_and_bad_ranges(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))
    headers = {"Idempotency-Key": "validation"}

    naive = client.post(
        "/api/v2/forward-eps",
        json=eps_payload(available_at="2026-08-01T09:00:00"),
        headers=headers,
    )
    ttm = client.post(
        "/api/v2/forward-eps",
        json=eps_payload(source_type="historical_ttm"),
        headers={"Idempotency-Key": "ttm"},
    )
    bad_range = client.post(
        "/api/v2/forward-eps",
        json=eps_payload(eps_low=60.0),
        headers={"Idempotency-Key": "range"},
    )
    bad_pe = client.post(
        "/api/v2/pe-scenarios",
        json=pe_payload(pe_value=0),
        headers={"Idempotency-Key": "pe-zero"},
    )

    assert naive.status_code == 422
    assert ttm.status_code == 422
    assert bad_range.status_code == 422
    assert bad_pe.status_code == 422


def test_date_only_cutoff_uses_start_of_taipei_day_and_prevents_same_day_lookahead(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))
    assert client.post(
        "/api/v2/forward-eps",
        json=eps_payload(),
        headers={"Idempotency-Key": "eps"},
    ).status_code == 200
    assert client.post(
        "/api/v2/pe-scenarios",
        json=pe_payload(),
        headers={"Idempotency-Key": "pe"},
    ).status_code == 200

    date_only = client.get("/api/v2/analysis/2330?as_of_date=2026-08-01")
    explicit = client.get(
        "/api/v2/analysis/2330?knowledge_cutoff_at=2026-08-02T00:00:00Z"
    )

    assert date_only.status_code == 200
    assert date_only.json()["knowledge_cutoff_at"] == "2026-07-31T16:00:00.000000Z"
    assert date_only.json()["cutoff_policy"]["mode"] == "date_start_of_day"
    assert date_only.json()["valuation"]["status"] == "insufficient_data"
    assert explicit.status_code == 200
    assert explicit.json()["valuation"]["status"] == "available"


def test_partial_data_does_not_fail_v2_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "valuation.db"))

    response = client.get(
        "/api/v2/analysis/2330?knowledge_cutoff_at=2026-08-01T00:00:00Z"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["valuation"]["status"] == "insufficient_data"
    assert data["model"]["official_affiliation"] is False
    assert data["liquidity"]["status"] == "unsupported"
