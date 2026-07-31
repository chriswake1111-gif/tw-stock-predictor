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
    assert "wave_analysis" in data
    assert "eps" in data
    assert "valuation" in data
    assert "eva_valuation" in data
    assert "sentiment" in data
    
    # 驗證 TWD 單位與契約
    assert data["eps"]["unit"] == "TWD_per_share"
    assert data["eva_valuation"]["status"] == "unsupported"

def test_symbol_normalization():
    response = client.get("/api/analysis/0000")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "^TWII"
