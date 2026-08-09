import json
import hashlib
from pathlib import Path

import pytest

from src.services.fixed_outcome_dataset import (
    BENCHMARK_FILENAME,
    PHASE2_SOURCE_FINGERPRINT,
    PHASE2_UNIVERSE,
    FixedPhase2OutcomeAdapter,
    OutcomeManifestError,
)
from src.domain.analysis_snapshot import sha256_json


HEADER = "symbol,date,open,high,low,close,volume\n"


def fixture_dataset(root: Path) -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    for symbol in PHASE2_UNIVERSE:
        path = data / f"{symbol.replace('.', '-')}.csv"
        path.write_text(
            HEADER + f"{symbol},2025-01-02,10,11,9,10,100\n",
            encoding="utf-8",
        )
    (data / BENCHMARK_FILENAME).write_text(
        HEADER + "^TWII,2025-01-02,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    (root / "snapshot_manifest.json").write_text(json.dumps({
        "snapshot_fingerprint_sha256": PHASE2_SOURCE_FINGERPRINT,
        "contract_version": "provider_compatible_adjusted_v1",
        "target_symbol_count": 14,
        "quality_warning_symbols": ["006208.TW", "2308.TW"],
    }), encoding="utf-8")
    return root


def fixture_hash(root: Path) -> str:
    resources = []
    for symbol in PHASE2_UNIVERSE:
        path = root / "data" / f"{symbol.replace('.', '-')}.csv"
        resources.append({
            "symbol": symbol,
            "path": f"data/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "quality_status": "quality_warning" if symbol in {"006208.TW", "2308.TW"} else "available",
        })
    benchmark_hash = hashlib.sha256((root / "data" / BENCHMARK_FILENAME).read_bytes()).hexdigest()
    calendar_hash = hashlib.sha256(b"2025-01-02\n").hexdigest()
    return sha256_json({
        "source_snapshot_fingerprint": PHASE2_SOURCE_FINGERPRINT,
        "symbol_resources": resources,
        "benchmark_sha256": benchmark_hash,
        "calendar_sha256": calendar_hash,
        "calendar_policy": "fixed_phase2_union_session_calendar_v1",
        "adjustment_contract": "provider_compatible_adjusted_v1",
    })


def test_fixed_outcome_manifest_hashes_every_resource_and_freezes_calendar(tmp_path):
    root = fixture_dataset(tmp_path)
    loaded = FixedPhase2OutcomeAdapter(root, expected_dataset_hash=fixture_hash(root)).load(
        ingested_at="2026-08-10T00:00:00Z"
    )
    payload = loaded.manifest.canonical_payload()
    assert len(payload["symbol_resources"]) == 14
    assert loaded.calendar == ("2025-01-02",)
    assert payload["calendar_resource"]["policy"] == "fixed_phase2_union_session_calendar_v1"
    warning = {item["symbol"] for item in payload["symbol_resources"] if item["quality_status"] == "quality_warning"}
    assert warning == {"006208.TW", "2308.TW"}


def test_outcome_adapter_fails_closed_for_missing_or_tampered_contract(tmp_path):
    with pytest.raises(OutcomeManifestError, match="outcome_manifest_missing"):
        FixedPhase2OutcomeAdapter(tmp_path / "missing").load(
            ingested_at="2026-08-10T00:00:00Z"
        )
    root = fixture_dataset(tmp_path / "invalid")
    source = json.loads((root / "snapshot_manifest.json").read_text(encoding="utf-8"))
    source["snapshot_fingerprint_sha256"] = "0" * 64
    (root / "snapshot_manifest.json").write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(OutcomeManifestError, match="outcome_manifest_invalid"):
        FixedPhase2OutcomeAdapter(root, expected_dataset_hash=fixture_hash(root)).load(
            ingested_at="2026-08-10T00:00:00Z"
        )


def test_outcome_adapter_rejects_unmanifested_file(tmp_path):
    root = fixture_dataset(tmp_path)
    expected = fixture_hash(root)
    (root / "data" / "extra.csv").write_text(HEADER, encoding="utf-8")
    with pytest.raises(OutcomeManifestError, match="outcome_manifest_invalid"):
        FixedPhase2OutcomeAdapter(root, expected_dataset_hash=expected).load(
            ingested_at="2026-08-10T00:00:00Z"
        )


def test_outcome_adapter_rejects_tampered_symbol_file(tmp_path):
    root = fixture_dataset(tmp_path)
    expected = fixture_hash(root)
    with (root / "data" / "2317-TW.csv").open("a", encoding="utf-8") as stream:
        stream.write("2317.TW,2025-01-03,10,11,9,10,100\n")
    with pytest.raises(OutcomeManifestError, match="outcome_manifest_hash_mismatch"):
        FixedPhase2OutcomeAdapter(root, expected_dataset_hash=expected).load(
            ingested_at="2026-08-10T00:00:00Z"
        )
