import json
import hashlib
import csv
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
    (root / "calendar.csv").write_text(
        "trade_date\n2025-01-02\n2025-01-03\n", encoding="utf-8"
    )
    return root


def fixture_hash(root: Path) -> str:
    with (root / "calendar.csv").open("r", encoding="utf-8", newline="") as stream:
        calendar_dates = {row["trade_date"] for row in csv.DictReader(stream)}
    resources = []
    for symbol in PHASE2_UNIVERSE:
        path = root / "data" / f"{symbol.replace('.', '-')}.csv"
        with path.open("r", encoding="utf-8", newline="") as stream:
            excluded_count = sum(
                row["date"] not in calendar_dates for row in csv.DictReader(stream)
            )
        resources.append({
            "symbol": symbol,
            "path": f"data/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "quality_status": "quality_warning" if symbol in {"006208.TW", "2308.TW"} else "available",
            "excluded_outside_calendar_rows": excluded_count,
        })
    benchmark_path = root / "data" / BENCHMARK_FILENAME
    benchmark_hash = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    with benchmark_path.open("r", encoding="utf-8", newline="") as stream:
        benchmark_excluded_count = sum(
            row["date"] not in calendar_dates for row in csv.DictReader(stream)
        )
    calendar_hash = hashlib.sha256((root / "calendar.csv").read_bytes()).hexdigest()
    return sha256_json({
        "source_snapshot_fingerprint": PHASE2_SOURCE_FINGERPRINT,
        "symbol_resources": resources,
        "benchmark_sha256": benchmark_hash,
        "calendar_sha256": calendar_hash,
        "calendar_policy": "fixed_phase2_trading_calendar_v1",
        "calendar_filter_policy": "exclude_raw_rows_outside_frozen_calendar_v1",
        "benchmark_excluded_outside_calendar_rows": benchmark_excluded_count,
        "outcome_observed_through_session": "2025-01-03",
        "adjustment_contract": "provider_compatible_adjusted_v1",
    })


def adapter(
    root: Path, *, calendar_path: Path | None = None,
    expected_dataset_hash: str | None = None,
) -> FixedPhase2OutcomeAdapter:
    path = calendar_path or root / "calendar.csv"
    expected_calendar_hash = (
        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    )
    return FixedPhase2OutcomeAdapter(
        root,
        expected_dataset_hash=expected_dataset_hash or fixture_hash(root),
        calendar_path=path,
        expected_calendar_hash=expected_calendar_hash,
        calendar_resource_path="calendar.csv",
    )


def test_fixed_outcome_manifest_hashes_every_resource_and_freezes_calendar(tmp_path):
    root = fixture_dataset(tmp_path)
    loaded = adapter(root).load(
        ingested_at="2026-08-10T00:00:00Z"
    )
    payload = loaded.manifest.canonical_payload()
    assert len(payload["symbol_resources"]) == 14
    assert loaded.calendar == ("2025-01-02", "2025-01-03")
    assert payload["calendar_resource"]["policy"] == "fixed_phase2_trading_calendar_v1"
    assert payload["calendar_resource"]["path"] == "calendar.csv"
    assert payload["outcome_observed_through_session"] == "2025-01-03"
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
        adapter(root).load(
            ingested_at="2026-08-10T00:00:00Z"
        )


def test_outcome_adapter_rejects_unmanifested_file(tmp_path):
    root = fixture_dataset(tmp_path)
    expected = fixture_hash(root)
    (root / "data" / "extra.csv").write_text(HEADER, encoding="utf-8")
    with pytest.raises(OutcomeManifestError, match="outcome_manifest_invalid"):
        adapter(root, expected_dataset_hash=expected).load(
            ingested_at="2026-08-10T00:00:00Z"
        )


def test_outcome_adapter_rejects_tampered_symbol_file(tmp_path):
    root = fixture_dataset(tmp_path)
    expected = fixture_hash(root)
    with (root / "data" / "2317-TW.csv").open("a", encoding="utf-8") as stream:
        stream.write("2317.TW,2025-01-03,10,11,9,10,100\n")
    with pytest.raises(OutcomeManifestError, match="outcome_manifest_hash_mismatch"):
        adapter(root, expected_dataset_hash=expected).load(
            ingested_at="2026-08-10T00:00:00Z"
        )


def test_outcome_adapter_fails_closed_for_missing_or_tampered_calendar(tmp_path):
    root = fixture_dataset(tmp_path / "calendar")
    missing = root / "missing-calendar.csv"
    with pytest.raises(OutcomeManifestError, match="outcome_calendar_missing"):
        adapter(root, calendar_path=missing).load(
            ingested_at="2026-08-10T00:00:00Z"
        )

    calendar = root / "calendar.csv"
    expected_hash = hashlib.sha256(calendar.read_bytes()).hexdigest()
    expected_dataset_hash = fixture_hash(root)
    calendar.write_text(
        "trade_date\n2025-01-02\n2025-01-03\n2025-01-06\n", encoding="utf-8"
    )
    with pytest.raises(OutcomeManifestError, match="outcome_calendar_hash_mismatch"):
        FixedPhase2OutcomeAdapter(
            root,
            expected_dataset_hash=expected_dataset_hash,
            calendar_path=calendar,
            expected_calendar_hash=expected_hash,
            calendar_resource_path="calendar.csv",
        ).load(ingested_at="2026-08-10T00:00:00Z")


def test_raw_bar_outside_calendar_is_hash_traced_but_cannot_create_session(tmp_path):
    root = fixture_dataset(tmp_path)
    benchmark = root / "data" / BENCHMARK_FILENAME
    with benchmark.open("a", encoding="utf-8") as stream:
        stream.write("^TWII,2025-01-06,100,101,99,100,1000\n")
    loaded = adapter(root).load(ingested_at="2026-08-10T00:00:00Z")
    assert loaded.calendar == ("2025-01-02", "2025-01-03")
    assert {bar.date for bar in loaded.benchmark_bars} == {"2025-01-02"}
    assert loaded.manifest.benchmark_resource["excluded_outside_calendar_rows"] == 1
