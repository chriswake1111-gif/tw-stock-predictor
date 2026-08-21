from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import src.repositories.migration_runner as migration_runner
from src.domain.data_foundation import (
    PublicationVerificationMode,
    ResourcePublicationEvidence,
)
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import (
    UniverseIdempotencyRequired,
    UniverseRepository,
)
from src.services.universe_ingestion_service import UniverseIngestionService
from src.services.universe_write_guard import (
    UniverseOperatorContext,
    UniverseOperatorContextRequired,
    UniverseWriteGuard,
)


PHASE13 = "20260821_16_phase13_universe_foundation"


def _context(suffix: str = "review") -> UniverseOperatorContext:
    return UniverseOperatorContext(
        "operator", f"run-{suffix}", f"lock-{suffix}", f"audit-{suffix}"
    )


def _repo(tmp_path, *, enabled: bool = True):
    db = tmp_path / "universe.sqlite"
    apply_valuation_migration(str(db))
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(enabled))


def _anchor(repo: UniverseRepository, context: UniverseOperatorContext):
    return repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture",
        context=context,
    )


def _payload(**overrides):
    value = {
        "venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
        "fetched_at": "2026-08-21T00:01:00Z", "received_at": "2026-08-21T00:01:00Z",
        "ingested_at": "2026-08-21T00:02:00Z", "available_at": "2026-08-21T00:01:00Z",
        "source_reference": "fixture", "status": "accepted",
        "freshness_status": "current", "freshness_mode": "official_cadence_window",
        "current_complete": True, "coverage_complete": True,
    }
    value.update(overrides)
    return value


def _apply_before_phase13(db, monkeypatch):
    index = migration_runner.MIGRATION_IDS.index(PHASE13)
    with monkeypatch.context() as context:
        context.setattr(migration_runner, "MIGRATION_IDS", migration_runner.MIGRATION_IDS[:index])
        context.setattr(migration_runner, "MIGRATION_FILES", migration_runner.MIGRATION_FILES[:index])
        context.setattr(migration_runner, "MIGRATION_ID", migration_runner.MIGRATION_IDS[index - 1])
        apply_valuation_migration(str(db))


@pytest.mark.parametrize("missing", ["actor_id", "run_id", "lock_id", "audit_id"])
def test_every_universe_mutation_requires_full_operator_context(tmp_path, missing):
    db, repo = _repo(tmp_path)
    values = {"actor_id": "operator", "run_id": "run", "lock_id": "lock", "audit_id": "audit"}
    values[missing] = None
    before = db.read_bytes()
    with pytest.raises(UniverseOperatorContextRequired, match=missing):
        repo.allocate_instrument(
            venue="TWSE", official_code="2330", source_identity="fixture",
            first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture",
            context=UniverseOperatorContext(**values),
        )
    assert db.read_bytes() == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_instruments").fetchone()[0] == 0


def test_revision_ingestion_requires_idempotency_before_mutation(tmp_path):
    db, repo = _repo(tmp_path)
    context = _context("no-key")
    anchor = _anchor(repo, context)
    with pytest.raises(UniverseIdempotencyRequired):
        repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
            logical_revision_key="master", revision_number=1,
            payload=_payload(), context=context,
        )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_revisions").fetchone()[0] == 0


def test_revision_cannot_override_audited_context_actor(tmp_path):
    _, repo = _repo(tmp_path)
    context = _context("actor")
    anchor = _anchor(repo, context)
    with pytest.raises(UniverseOperatorContextRequired, match="actor_id"):
        repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
            logical_revision_key="master", revision_number=1, payload=_payload(),
            context=context, idempotency_key="actor-override", actor_id="other",
        )


def test_ingestion_service_rejects_missing_idempotency_before_repository_call(tmp_path):
    db, repo = _repo(tmp_path)
    service = UniverseIngestionService(
        str(db), repository=repo, guard=UniverseWriteGuard(True)
    )
    with pytest.raises(UniverseIdempotencyRequired):
        service.ingest_revision(
            context=_context("service-no-key"), instrument_id="missing",
            resource_id="twse-universe-master", logical_revision_key="master",
            revision_number=1, payload=_payload(),
        )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_revisions").fetchone()[0] == 0


def test_historical_lookup_excludes_unknown_null_availability(tmp_path):
    _, repo = _repo(tmp_path)
    context = _context("null-availability")
    anchor = _anchor(repo, context)
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_payload(available_at=None), context=context, idempotency_key="null-available",
    )
    result = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-22T00:00:00Z")
    assert result["status"] == "insufficient_data"
    assert result["identity_reference"] is None


def _publication_evidence(db, *, ingested_at: str):
    return DataFoundationRepository(str(db)).add_publication_evidence(
        ResourcePublicationEvidence(
            provider_id="twse-universe-official",
            resource_id="twse-universe-termination",
            logical_revision_key="termination-2330",
            official_release_at="2026-08-21T00:00:00Z",
            source_reference="https://www.twse.com.tw/termination/2330",
            source_identity="twse:termination:2330:v1",
            evidence_file_sha256="a" * 64,
            captured_at="2026-08-21T01:00:00Z",
            verification_mode=PublicationVerificationMode.MANUAL_OFFICIAL_SOURCE_REVIEW,
            verified_by="reviewer",
        ),
        ingested_at=ingested_at,
    )


def test_manual_publication_evidence_missing_is_not_historical_reference(tmp_path):
    _, repo = _repo(tmp_path)
    context = _context("manual-missing")
    anchor = _anchor(repo, context)
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-termination",
        logical_revision_key="termination-2330", revision_number=1,
        payload=_payload(available_at="2026-08-21T00:01:00Z"),
        context=context, idempotency_key="manual-missing",
    )
    result = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-22T00:00:00Z")
    assert result["status"] == "insufficient_data"
    assert result["identity_reference"] is None


def test_manual_publication_evidence_uses_visibility_cutoff_and_approved_point(tmp_path):
    db, repo = _repo(tmp_path)
    context = _context("manual-later")
    anchor = _anchor(repo, context)
    evidence = _publication_evidence(db, ingested_at="2026-08-22T00:00:00Z")
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-termination",
        logical_revision_key="termination-2330", revision_number=1,
        payload=_payload(
            available_at="2026-08-21T00:01:00Z",
            publication_evidence_id=evidence["publication_evidence_id"],
            availability_mode="manual_publication_evidence_required",
        ),
        context=context, idempotency_key="manual-later",
    )
    before = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T23:59:59Z")
    after = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-22T00:00:00Z")
    assert before["status"] == "insufficient_data"
    assert before["identity_reference"] is None
    assert after["identity_reference"]["canonical_symbol"] == "2330.TW"


def test_phase13_registry_accepts_identical_preseed_and_rejects_provider_resource_conflicts(tmp_path, monkeypatch):
    db = tmp_path / "preseed.sqlite"
    _apply_before_phase13(db, monkeypatch)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO data_providers VALUES (?,?,?,?,?,?,?,?)",
            ("twse-universe-official", "TWSE Universe Official", "authoritative", "official",
             "https://www.twse.com.tw", 1, "2026-08-21T00:00:00Z",
             "042ecef36a6ca8dd109b948f6a679973b31e69c759d7e9d1e408b1f83746cb3e"),
        )
        conn.execute(
            "INSERT INTO data_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("twse-universe-master", "twse-universe-official", "twse.t187ap03_L", "symbol_master",
             "TWSE", "periodic", "unknown_without_official_cadence", "twse_universe_master",
             "1", "phase13", "archive_raw", 1, "2026-08-21T00:00:00Z",
             "be3665c8f7640f7031de4c8526eeace37873f34df8adebdff32792f94e8e808d"),
        )
    apply_valuation_migration(str(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_resource_policies").fetchone()[0] == 7

    for table, column, value in (
        ("data_providers", "display_name", "tampered"),
        ("data_resources", "market", "TPEX"),
    ):
        conflict_db = tmp_path / f"{table}-conflict.sqlite"
        _apply_before_phase13(conflict_db, monkeypatch)
        with sqlite3.connect(conflict_db) as conn:
            if table == "data_providers":
                conn.execute(
                    "INSERT INTO data_providers VALUES (?,?,?,?,?,?,?,?)",
                    ("twse-universe-official", value, "authoritative", "official",
                     "https://www.twse.com.tw", 1, "2026-08-21T00:00:00Z", "f" * 64),
                )
            else:
                conn.execute(
                    "INSERT INTO data_providers VALUES (?,?,?,?,?,?,?,?)",
                    ("twse-universe-official", "TWSE Universe Official", "authoritative", "official",
                     "https://www.twse.com.tw", 1, "2026-08-21T00:00:00Z",
                     "042ecef36a6ca8dd109b948f6a679973b31e69c759d7e9d1e408b1f83746cb3e"),
                )
                conn.execute(
                    "INSERT INTO data_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("twse-universe-master", "twse-universe-official", "twse.t187ap03_L", "symbol_master",
                     value, "periodic", "unknown_without_official_cadence", "twse_universe_master",
                     "1", "phase13", "archive_raw", 1, "2026-08-21T00:00:00Z", "f" * 64),
                )
        with pytest.raises(RuntimeError, match="phase13 seed conflict"):
            apply_valuation_migration(str(conflict_db))
        with sqlite3.connect(conflict_db) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version_id=?", (PHASE13,)
            ).fetchone()[0] == 0


def test_phase13_registry_rejects_incompatible_policy_seed_before_commit(tmp_path, monkeypatch):
    db = tmp_path / "policy-conflict.sqlite"
    _apply_before_phase13(db, monkeypatch)
    original = Path(migration_runner.MIGRATION_FILES[-1]).read_text(encoding="utf-8")
    marker = "INSERT INTO universe_resource_policies\n"
    injected = (
        "INSERT INTO universe_resource_policies VALUES "
        "('twse-universe-master','master_snapshot','conservative_first_observed',"
        "'event_observation','accepted_master_complete','universe_symbol_mapping_v1',"
        "'tampered scope',1,'2026-08-21T00:00:00Z','tampered');\n" + marker
    )
    custom = tmp_path / "phase13-policy-conflict.sql"
    custom.write_text(original.replace(marker, injected, 1), encoding="utf-8")
    with monkeypatch.context() as context:
        context.setattr(
            migration_runner, "MIGRATION_FILES",
            (*migration_runner.MIGRATION_FILES[:-1], custom),
        )
        with pytest.raises(RuntimeError, match="phase13 seed conflict"):
            apply_valuation_migration(str(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version_id=?", (PHASE13,)
        ).fetchone()[0] == 0
