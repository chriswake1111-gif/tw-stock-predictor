from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.main import SPAStaticFiles


def test_spa_supports_taiwan_symbol_deep_link_without_hiding_missing_assets(tmp_path):
    (tmp_path / "index.html").write_text(
        '<!doctype html><div id="root">phase9</div>', encoding="utf-8"
    )
    (tmp_path / "assets").mkdir()
    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True))
    client = TestClient(app)

    deep_link = client.get("/stocks/2330.TW")
    assert deep_link.status_code == 200
    assert 'id="root"' in deep_link.text

    missing_asset = client.get("/assets/missing.js")
    assert missing_asset.status_code == 404
    assert 'id="root"' not in missing_asset.text
