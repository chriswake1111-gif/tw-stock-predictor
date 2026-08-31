from __future__ import annotations

from pathlib import Path

from tools.phase17_daily_research_benchmark import (
    CONTRACT_VERSION,
    QUEUE_SIZES,
    _database_sha256,
    build_database,
    load_manifest,
    manifest_sha256,
    nearest_rank,
)


MANIFEST = Path(__file__).parent / "fixtures" / "phase17_daily_research_benchmark_v1.json"


def test_phase17_benchmark_manifest_is_immutable_and_complete():
    manifest = load_manifest(MANIFEST)
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert tuple(manifest["queue_sizes"]) == QUEUE_SIZES
    assert manifest["samples"] == {
        "cold": 30,
        "warm": 30,
        "total": 60,
        "percentile": "nearest_rank",
        "nearest_rank_index": "ceil(percentile * sample_count)",
    }
    assert manifest_sha256(manifest) == "59ffd8da6d159153571fa8c9a814ca9780f5944e2e82bed8d686010722d01847"


def test_phase17_benchmark_nearest_rank_is_ceil_index():
    assert nearest_rank([0.1, 0.2, 0.3, 0.4], 0.95) == 0.4
    assert nearest_rank([0.1, 0.2, 0.3, 0.4], 0.50) == 0.2


def test_phase17_benchmark_fixture_is_byte_deterministic(tmp_path):
    first = tmp_path / "first.sqlite"
    second = tmp_path / "second.sqlite"
    build_database(first, 25)
    build_database(second, 25)
    assert _database_sha256(first) == _database_sha256(second)
