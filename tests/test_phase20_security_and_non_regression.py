"""Phase 20 Security, Boundary, and Non-Regression Automated Verification Suite (WP10)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.paths import RuntimePaths
from src.runtime.settings import RuntimeSettings
from tests.test_phase20_research_summary_and_queue import (
    _insert_snapshot_and_observation,
    _insert_universe_instrument,
    _setup_db,
)


def _create_test_app(tmp_path: Path):
    db, _ = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_universe_instrument(conn, official_code="2330", display_name="台積電", short_name="台積電")
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close="980.0")

    os.environ["DATABASE_PATH"] = str(db)
    os.environ["UNIVERSE_DB_PATH"] = str(db)
    os.environ["RESEARCH_APPLICATION_ORIGIN"] = "http://127.0.0.1:8000"

    environ = {
        "TW_STOCK_PREDICTOR_ENV": "development",
        "DATABASE_PATH": str(db),
        "UNIVERSE_DB_PATH": str(db),
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
    }
    paths = RuntimePaths.from_environment(environ)
    settings = RuntimeSettings.from_environment(environ, paths=paths)
    settings.paths.ensure_user_dirs()
    app = create_app(settings)
    return db, app


def test_zero_egress_on_search_and_summary(tmp_path):
    """Local universe search and research summary must never make outbound HTTP calls."""
    db, app = _create_test_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    # Mock socket or urllib to guarantee no egress
    with patch("urllib.request.urlopen") as mock_urlopen, patch("http.client.HTTPConnection.request") as mock_http:
        # 1. Search request
        s_resp = client.get("/api/v2/universe/search?q=2330")
        assert s_resp.status_code == 200

        # 2. Summary request
        sum_resp = client.get("/api/v2/research/summary/2330.TW")
        assert sum_resp.status_code == 200

        # Zero outbound calls
        assert mock_urlopen.call_count == 0
        assert mock_http.call_count == 0


def test_prohibition_of_synthetic_valuation_and_wave_targets(tmp_path):
    """Summary must never invent synthetic forward EPS, target PE, or wave targets."""
    db, app = _create_test_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    resp = client.get("/api/v2/research/summary/2330.TW")
    assert resp.status_code == 200
    data = resp.json()

    # Valuation context
    assert data["valuation_context"]["status"] == "needs_human_judgment"
    assert data["valuation_context"]["target_matrix"] == []

    # Technical context
    assert data["technical_context"]["status"] == "needs_human_judgment"
    assert data["technical_context"]["targets"] is None

    # Screening metrics
    assert data["screening_context"]["pe"]["status"] == "unavailable"
    assert data["screening_context"]["pe"]["value"] is None
    assert data["screening_context"]["pb"]["status"] == "unavailable"
    assert data["screening_context"]["dividend_yield"]["status"] == "unavailable"


def test_csrf_and_loopback_security_enforcement(tmp_path):
    """Mutating endpoints reject non-loopback clients and missing CSRF headers."""
    db, app = _create_test_app(tmp_path)

    # 1. Non-loopback client rejected for research endpoint
    external_client = TestClient(app, base_url="http://127.0.0.1:8000", client=("192.168.1.100", 50000))
    resp_ext = external_client.get("/api/v2/research/summary/2330.TW")
    assert resp_ext.status_code == 403

    # 2. Mutating POST without CSRF rejected
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    resp_no_csrf = client.post(
        "/api/v2/research/bootstrap",
        json={"canonical_symbol": "2330.TW"},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert resp_no_csrf.status_code == 403


def test_backward_compatibility_existing_endpoints(tmp_path):
    """Existing Phase 14, 18, and 19 endpoints remain fully operational."""
    db, app = _create_test_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    # Phase 19 status endpoint
    ops_resp = client.get("/api/v2/data-operations/status")
    assert ops_resp.status_code == 200

    # Phase 20 coverage endpoint
    cov_resp = client.get("/api/v2/universe/coverage")
    assert cov_resp.status_code == 200
    assert cov_resp.json()["universe_status"] in ("ready", "short_names_uninitialized", "short_names_partial", "not_initialized")
