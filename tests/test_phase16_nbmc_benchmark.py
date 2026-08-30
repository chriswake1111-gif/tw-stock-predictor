from __future__ import annotations

import json
import os
from pathlib import Path

from tools.phase16_nbmc_benchmark import (
    BENCHMARK_MIGRATION_VERSION,
    CONTRACT_VERSION,
    DEFAULT_VARIANTS,
    build_database,
    CountingStorage,
    environment_metadata,
    load_manifest,
    manifest_sha256,
    nearest_rank,
    run_cold_sample,
    run_request,
    validate_manifest,
)
from src.repositories.neutral_batch_market_context_repository import (
    NeutralBatchMarketContextRepository,
)
from src.services.neutral_batch_market_context_service import (
    NeutralBatchMarketContextService,
)


MANIFEST = Path(__file__).parent / "fixtures" / "phase16_nbmc_benchmark_v1.json"


def test_phase16_benchmark_manifest_is_immutable_and_complete() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["migration_version"] == BENCHMARK_MIGRATION_VERSION
    assert tuple(manifest["variants"]) == DEFAULT_VARIANTS
    assert manifest["effective_denominator_candidates"] == {
        "TWSE": 1200,
        "TPEX": 800,
        "total": 2000,
    }
    assert manifest["additional_identity_revision_event_rows"] == 240
    assert manifest["combined_source_observation_orphan_count"] == 40
    assert manifest["samples"] == {
        "cold": 30,
        "warm": 30,
        "total": 60,
        "percentile": "nearest_rank",
        "nearest_rank_index": "ceil(percentile * sample_count)",
    }
    assert manifest["workload"]["network"] is False
    assert manifest["workload"]["production_database"] is False
    assert manifest["workload"]["max_python_page_rows"] == 101
    assert manifest_sha256(manifest) == manifest["fixture_sha256"]


def test_phase16_benchmark_nearest_rank_is_ceil_index() -> None:
    values = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert nearest_rank(values, 0.95) == 5.0
    assert nearest_rank(values, 0.50) == 3.0


def test_phase16_benchmark_manifest_rejects_drift() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["variants"] = list(reversed(manifest["variants"]))
    try:
        validate_manifest(manifest)
    except ValueError as exc:
        assert str(exc) == "benchmark_variants_mismatch"
    else:
        raise AssertionError("manifest drift was not rejected")


def test_phase16_benchmark_manifest_rejects_migration_drift() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["migration_version"] = "20260827_18_phase14_eod_close_context"
    try:
        validate_manifest(manifest)
    except ValueError as exc:
        assert str(exc) == "benchmark_migration_version_mismatch"
    else:
        raise AssertionError("benchmark migration drift was not rejected")


def test_phase16_benchmark_environment_records_hardware_class_and_size() -> None:
    metadata = environment_metadata()
    assert metadata["cpu_class"] == metadata["cpu_class"].strip()
    assert metadata["cpu_class"]
    assert metadata["cpu_model"] == metadata["cpu_model"].strip()
    assert metadata["cpu_model"]
    assert metadata["memory_class"] in {
        "lt_8GiB",
        "8_16GiB",
        "16_32GiB",
        "32_64GiB",
        "gte_64GiB",
    }
    assert isinstance(metadata["memory_total_bytes"], int)
    assert metadata["memory_total_bytes"] > 0


def test_phase16_cold_sample_uses_a_new_process(tmp_path) -> None:
    database = tmp_path / "cold.sqlite"
    build_database(database, "both_available")

    sample = run_cold_sample(database, limit=1)

    assert sample["worker_pid"] != os.getpid()
    assert sample["process_isolated"] is True
    assert sample["mode"] == "cold"
    assert sample["query_count"] > 0


def test_phase16_warm_samples_reuse_connection_but_restart_snapshot(tmp_path) -> None:
    database = tmp_path / "warm.sqlite"
    build_database(database, "both_available")
    storage = CountingStorage(database, reuse_connection=True)
    service = NeutralBatchMarketContextService(
        repository=NeutralBatchMarketContextRepository(storage=storage)
    )

    try:
        first = run_request(service, storage, limit=1)
        second = run_request(service, storage, limit=1)
        assert first["process_pid"] == second["process_pid"] == os.getpid()
        assert first["connection_id"] == second["connection_id"]
        assert first["connection_reused"] is True
        assert second["connection_reused"] is True
        assert storage.connection_open_count == 1
        assert storage.transaction_count >= 2
        assert storage.query_count > 0
    finally:
        storage.close()


def test_phase16_classification_ambiguity_keeps_mixed_denominator_available(tmp_path) -> None:
    database = tmp_path / "mixed.sqlite"
    build_database(database, "classification_ambiguity")
    body = NeutralBatchMarketContextService(str(database)).as_of(
        market_date="2026-08-28",
        knowledge_cutoff_at="2026-08-29T00:00:00Z",
        venue_scope="TWSE",
        limit=1,
    )
    aggregate = body["per_venue"]["TWSE"]["aggregate"]
    assert aggregate["denominator_candidate_count"] == 1200
    assert aggregate["denominator_expected_count"] == 1199
    assert aggregate["denominator_unresolved_count"] == 1
    assert body["per_venue"]["TWSE"]["assembly_status"] == "available"
