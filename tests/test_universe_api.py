from __future__ import annotations

import os

from fastapi.testclient import TestClient

from src.api.main import app
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import seed_raw_provenance


def test_universe_api_requires_aware_cutoff_and_is_get_only(tmp_path, monkeypatch):
    db = tmp_path / "api.sqlite"
    apply_valuation_migration(str(db))
    raw_id, raw_hash = seed_raw_provenance(db)["twse-universe-master"]
    repo = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = UniverseOperatorContext("operator", "run-api", "lock-api", "audit-api")
    anchor = repo.allocate_instrument(venue="TWSE", official_code="2330", source_identity="fixture",
                                      first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context)
    repo.add_revision(instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
                      revision_number=1, payload={"venue":"TWSE","official_code":"2330","canonical_symbol":"2330.TW","fetched_at":"2026-08-21T00:01:00Z","received_at":"2026-08-21T00:01:00Z","ingested_at":"2026-08-21T00:02:00Z","available_at":"2026-08-21T00:01:00Z","source_reference":"fixture","status":"accepted","freshness_status":"current","freshness_mode":"official_cadence_window","current_complete":True,"coverage_complete":True,"raw_resource_revision_id":raw_id,"raw_payload_sha256":raw_hash}, context=context, idempotency_key="api")
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(db))
    client = TestClient(app)
    assert client.get("/api/v2/universe/instruments/2330.TW", params={"knowledge_cutoff_at": "2026-08-21"}).status_code == 422
    response = client.get("/api/v2/universe/instruments/2330.TW", params={"knowledge_cutoff_at": "2026-08-21T08:03:00+08:00"})
    assert response.status_code == 200
    body = response.json()
    assert body["identity_reference"]["instrument_id"] == anchor["instrument_id"]
    assert "idempotency_key" not in str(body)
    assert client.post("/api/v2/universe/instruments/2330.TW").status_code in {405, 422}
