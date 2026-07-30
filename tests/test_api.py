import pytest
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

def test_api_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_api_analysis_tsmc():
    response = client.get("/api/analysis/2330")
    assert response.status_code == 200
    data = response.json()
    
    assert data["symbol"] == "2330.TW"
    assert "latest_price" in data
    assert "wave_targets" in data
    assert "valuation" in data
    assert "kline_data" in data
    
    # 檢查 TradingView Lightweight Charts 時間格式 (YYYY-MM-DD)
    if len(data["kline_data"]) > 0:
        first_k = data["kline_data"][0]
        assert "time" in first_k
        assert len(first_k["time"]) == 10 # YYYY-MM-DD
        assert "-" in first_k["time"]

def test_symbol_normalization():
    # 測試 0000 轉譯為 ^TWII
    response = client.get("/api/analysis/0000")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "^TWII"
