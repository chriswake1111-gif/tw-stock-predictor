from __future__ import annotations

import json
from pathlib import Path

from tools.phase16_nbmc_benchmark import (
    CONTRACT_VERSION,
    DEFAULT_VARIANTS,
    load_manifest,
    manifest_sha256,
    nearest_rank,
    validate_manifest,
)


MANIFEST = Path(__file__).parent / "fixtures" / "phase16_nbmc_benchmark_v1.json"


def test_phase16_benchmark_manifest_is_immutable_and_complete() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest["contract_version"] == CONTRACT_VERSION
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
