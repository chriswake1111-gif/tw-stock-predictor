import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import main as api_main


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v1_api_golden.json"


def test_v1_analysis_contract_matches_golden_snapshot(monkeypatch):
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        api_main,
        "analyze_symbol",
        lambda symbol, db_path: (copy.deepcopy(expected), symbol),
    )

    response = TestClient(api_main.app).get("/api/analysis/2330")

    assert response.status_code == 200
    actual = response.json()
    for key, value in expected.items():
        assert actual[key] == value
