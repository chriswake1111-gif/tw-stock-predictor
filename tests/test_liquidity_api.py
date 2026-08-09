from fastapi.testclient import TestClient

from src.api.main import app
from src.domain.liquidity import M1BMonthlyObservation, MarketTurnoverObservation
from src.repositories.liquidity_repository import LiquidityRepository


client = TestClient(app)


def seed(db_path):
    repo = LiquidityRepository(str(db_path))
    repo.add_m1b(M1BMonthlyObservation(
        period="2026-06", value_raw=30_000, raw_unit="TWD_million",
        data_date="2026-06-30", available_at="2026-07-20T08:00:00Z",
        fetched_at="2026-07-20T08:01:00Z", source="CBC",
        source_dataset="CBC EF15M01",
    ), ingested_at="2026-07-20T08:02:00Z")
    repo.add_turnover(MarketTurnoverObservation(
        trade_date="2026-07-21", twse_turnover_twd=800_000_000_000,
        tpex_turnover_twd=100_000_000_000, twse_source="TWSE", tpex_source="TPEx",
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset="tpex_daily_trading_index",
        available_at="2026-07-21T09:00:00Z", fetched_at="2026-07-21T09:01:00Z",
    ), ingested_at="2026-07-21T09:02:00Z")


def test_v2_api_exposes_independent_symbol_invariant_liquidity(monkeypatch, tmp_path):
    db_path = tmp_path / "api.db"
    seed(db_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    url = "?knowledge_cutoff_at=2026-07-22T00:00:00Z"
    first = client.get("/api/v2/analysis/2330" + url).json()
    second = client.get("/api/v2/analysis/2317" + url).json()
    assert first["liquidity"]["status"] == "available"
    assert first["liquidity"] == second["liquidity"]
    assert "liquidity" in first["data_quality"]["available_sections"]
    assert first["valuation"]["status"] == "insufficient_data"
    assert "forward_valuation" in first["data_quality"]["missing_sections"]
    rule_ids = {rule["rule_id"] for rule in first["rules_used"]}
    assert {"LIQ-01", "LIQ-02"}.issubset(rule_ids)
    assert "LIQ-03" not in rule_ids


def test_v2_api_liquidity_missing_does_not_break_valuation_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "empty.db"))
    response = client.get(
        "/api/v2/analysis/2330?knowledge_cutoff_at=2026-07-22T00:00:00Z"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["liquidity"]["status"] == "insufficient_data"
    assert "liquidity" in data["data_quality"]["missing_sections"]
