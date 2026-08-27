from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.v2_eod_close import router
from src.repositories.migration_runner import apply_valuation_migration


def _client(tmp_path, monkeypatch) -> TestClient:
    db = tmp_path / "api.sqlite"
    apply_valuation_migration(str(db))
    monkeypatch.setenv("EOD_DB_PATH", str(db))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_current_and_as_of_are_get_only_and_safe_for_ordinary_missing_data(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    current = client.get("/api/v2/market-context/eod-close/current/2330.TW")
    as_of = client.get(
        "/api/v2/market-context/eod-close/as-of/2330.TW",
        params={"knowledge_cutoff_at": "2026-08-27T08:00:00Z"},
    )
    post = client.post("/api/v2/market-context/eod-close/current/2330.TW")

    assert current.status_code == 200
    assert current.json()["status"] == "unknown"
    assert current.json()["close_value"] is None
    assert as_of.status_code == 200
    assert as_of.json()["status"] == "unknown"
    assert post.status_code == 405


def test_api_rejects_invalid_symbol_and_cutoff_and_does_not_declare_trade_date(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    invalid_symbol = client.get("/api/v2/market-context/eod-close/current/2330")
    missing_cutoff = client.get("/api/v2/market-context/eod-close/as-of/2330.TW")
    invalid_cutoff = client.get(
        "/api/v2/market-context/eod-close/as-of/2330.TW",
        params={"knowledge_cutoff_at": "2026-08-27"},
    )

    assert invalid_symbol.status_code == 422
    assert missing_cutoff.status_code == 422
    assert invalid_cutoff.status_code == 422
    openapi = client.get("/openapi.json").json()
    for path in openapi["paths"]:
        if path.startswith("/api/v2/market-context/eod-close/"):
            assert "trade_date" not in str(openapi["paths"][path])


def test_api_maps_missing_phase14_storage_to_service_unavailable(tmp_path, monkeypatch) -> None:
    missing_db = tmp_path / "missing.sqlite"
    monkeypatch.setenv("EOD_DB_PATH", str(missing_db))
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/api/v2/market-context/eod-close/current/2330.TW")

    assert response.status_code == 503
    assert response.json()["detail"] == "eod_storage_unavailable"
    assert not missing_db.exists()
