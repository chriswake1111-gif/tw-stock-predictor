import sqlite3
from types import SimpleNamespace

import pytest

from src.repositories.migration_runner import apply_valuation_migration
from src.services import current_research_service as module
from src.services.current_research_service import CurrentResearchService
from src.services.daily_market_data_service import DailyMarketDataService


def market_context(turnover=999):
    return {
        "official_close": {"value": 2465, "status": "available", "unit": "TWD_per_share", "currency": "TWD"},
        "settled_trade_date": "2026-09-10",
        "market_context": {"market_turnover_total": turnover, "is_market_closed": False, "market_status_label": "open"},
    }


class Repo:
    db_path = ""
    def resolve_latest_settled_context(self, symbol, cutoff=None, conn=None):
        return market_context()


class Engine:
    def __init__(self, *args, **kwargs):
        pass
    def analyze_preloaded(self, *args, **kwargs):
        return {"status": "insufficient_data", "reason": "missing"}


class Public:
    def __init__(self, *args, **kwargs):
        pass
    def view(self, symbol, cutoff):
        return {
            "TaiwanStockPER": {"source": "FinMind", "rows": [{"date": "2026-09-10", "pe": 25.9, "pb": None, "yield_ratio": None}]},
            "TaiwanStockPrice": {"source": "FinMind", "rows": []},
        }


def service(monkeypatch, tmp_path, public=None, market=None):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    repo = Repo()
    repo.db_path = str(db)
    monkeypatch.setattr(module, "ForwardEPSService", Engine)
    monkeypatch.setattr(module, "TechnicalScenarioService", Engine)
    monkeypatch.setattr(module, "DailyPublicDataService", public or Public)
    monkeypatch.setattr(module, "DailyMarketDataService", market or type("M", (), {"__init__": lambda self, *a, **k: None, "view": lambda self, *a: {}}))
    return CurrentResearchService(repository=repo)


def test_summary_uses_finmind_pe_fields_and_common_market_date(monkeypatch, tmp_path):
    class Market:
        def __init__(self, *args, **kwargs): pass
        def view(self, symbol, cutoff):
            return {
                "TWSE_TURNOVER": {"rows": [{"date": "2026-09-09", "value": 100}, {"date": "2026-09-10", "value": 300}]},
                "TPEX_TURNOVER": {"rows": [{"date": "2026-09-10", "value": 200}, {"date": "2026-09-11", "value": 500}]},
                "CBC_M1B": {"rows": [{"period": "2026-08", "value": 1000}]},
            }
    result = service(monkeypatch, tmp_path, market=Market).get_summary("2330.TW", knowledge_cutoff_at="2026-09-11T00:00:00Z")
    assert result["screening_context"]["pe"]["value"] == 25.9
    assert result["screening_context"]["pb"]["value"] is None
    assert result["screening_context"]["dividend_yield"]["value"] is None
    assert result["market_context"]["market_turnover_total"] == 500
    assert result["market_context"]["market_turnover_date"] == "2026-09-10"
    assert result["market_context"]["cbc_m1b_ratio"] == 0.5
    assert result["market_context"]["cbc_period"] == "2026-08"


def test_summary_does_not_add_noncommon_market_dates(monkeypatch, tmp_path):
    class Market:
        def __init__(self, *args, **kwargs): pass
        def view(self, symbol, cutoff):
            return {"TWSE_TURNOVER": {"rows": [{"date": "2026-09-09", "value": 100}]},
                    "TPEX_TURNOVER": {"rows": [{"date": "2026-09-10", "value": 200}]}, "CBC_M1B": {"rows": []}}
    result = service(monkeypatch, tmp_path, market=Market).get_summary("2330.TW", knowledge_cutoff_at="2026-09-11T00:00:00Z")
    assert result["market_context"]["market_turnover_total"] == 999


def test_daily_market_view_missing_source_is_not_labeled_finmind(tmp_path):
    db = str(tmp_path / "market.db")
    apply_valuation_migration(db)
    data = DailyMarketDataService(db).view("MARKET", "9999-01-01T00:00:00Z")
    assert data["TWSE_TURNOVER"]["source"] == "TWSE"
    assert data["TPEX_TURNOVER"]["source"] == "TPEx"
    assert data["CBC_M1B"]["source"] == "CBC"
    assert all(item["source"] != "FinMind" for item in data.values())
