"""Hash-verified adapter for the fixed Phase 2 historical outcome snapshot."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.domain.analysis_snapshot import sha256_json
from src.domain.performance_validation import OutcomeResourceManifest


PHASE2_DATASET_NAME = "phase2-gap-adjusted-v1-20260801-ec781a02134f"
PHASE2_SOURCE_FINGERPRINT = (
    "ec781a02134f1f08fc943eefcb8d3aa44baf3eae983d046ce62e4fc834f72692"
)
PHASE2_OUTCOME_DATASET_HASH = (
    "39b4998e0d120fcb5aa6d87a4d6645b490f58975789b36bf2f12e515fcb7ab15"
)
PHASE2_UNIVERSE = (
    "0056.TW", "006208.TW", "1101.TW", "1216.TW", "1301.TW",
    "2002.TW", "2105.TW", "2308.TW", "2317.TW", "2412.TW",
    "2454.TW", "2881.TW", "2882.TW", "2912.TW",
)
PHASE2_QUALITY_WARNINGS = frozenset({"006208.TW", "2308.TW"})
BENCHMARK_FILENAME = "index-TWII.csv"
CALENDAR_POLICY = "fixed_phase2_trading_calendar_v1"
CALENDAR_FILTER_POLICY = "exclude_raw_rows_outside_frozen_calendar_v1"
CALENDAR_RESOURCE_ID = "twse_fmtqik_20140102_20260731_v1"
CALENDAR_RELATIVE_PATH = (
    "config/outcome_calendars/phase2_twse_trading_calendar_20140102_20260731.csv"
)
PHASE2_CALENDAR_HASH = (
    "110dd61a903797bfa6c5e345bf6b1245ad509def7a52aac67a8d595c6b0ab259"
)


class OutcomeManifestError(RuntimeError):
    """The immutable outcome resource cannot be verified."""


@dataclass(frozen=True)
class OutcomeBar:
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class VerifiedOutcomeDataset:
    manifest: OutcomeResourceManifest
    calendar: tuple[str, ...]
    bars_by_symbol: dict[str, tuple[OutcomeBar, ...]]
    benchmark_bars: tuple[OutcomeBar, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_bars(path: Path, expected_symbol: str | None = None) -> tuple[OutcomeBar, ...]:
    rows: list[OutcomeBar] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != ["symbol", "date", "open", "high", "low", "close", "volume"]:
                raise OutcomeManifestError(f"unsupported outcome CSV schema: {path.name}")
            for item in reader:
                if expected_symbol and item["symbol"].upper() != expected_symbol:
                    raise OutcomeManifestError(f"symbol mismatch in {path.name}")
                values = tuple(Decimal(item[name]) for name in ("open", "high", "low", "close"))
                if any(not value.is_finite() or value <= 0 for value in values):
                    raise OutcomeManifestError(f"invalid adjusted OHLC in {path.name}")
                open_, high, low, close = values
                tolerance = max(values) * Decimal("0.0000000001")
                if (
                    low > high
                    or high + tolerance < max(open_, close)
                    or low - tolerance > min(open_, close)
                ):
                    raise OutcomeManifestError(f"inconsistent adjusted OHLC in {path.name}")
                rows.append(OutcomeBar(item["date"], open_, high, low, close))
    except (OSError, KeyError, ValueError, ArithmeticError) as exc:
        if isinstance(exc, OutcomeManifestError):
            raise
        raise OutcomeManifestError(f"cannot parse outcome resource: {path.name}") from exc
    if not rows or any(left.date >= right.date for left, right in zip(rows, rows[1:])):
        raise OutcomeManifestError(f"outcome dates are empty or unordered: {path.name}")
    return tuple(rows)


def _read_calendar(path: Path) -> tuple[tuple[str, ...], str]:
    if not path.is_file():
        raise OutcomeManifestError("outcome_calendar_missing")
    try:
        raw = path.read_bytes()
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != ["trade_date"]:
                raise OutcomeManifestError("outcome_calendar_invalid")
            dates = tuple(date.fromisoformat(item["trade_date"]).isoformat() for item in reader)
    except (OSError, KeyError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, OutcomeManifestError):
            raise
        raise OutcomeManifestError("outcome_calendar_invalid") from exc
    if (
        not dates
        or any(left >= right for left, right in zip(dates, dates[1:]))
    ):
        raise OutcomeManifestError("outcome_calendar_invalid")
    return dates, hashlib.sha256(raw).hexdigest()


class FixedPhase2OutcomeAdapter:
    """Load only the approved immutable Phase 2 directory; no mutable fallback."""

    def __init__(
        self,
        dataset_root: str | Path | None = None,
        *,
        expected_dataset_hash: str = PHASE2_OUTCOME_DATASET_HASH,
        calendar_path: str | Path | None = None,
        expected_calendar_hash: str | None = PHASE2_CALENDAR_HASH,
        calendar_resource_path: str = CALENDAR_RELATIVE_PATH,
    ):
        repo_root = Path(__file__).resolve().parents[2]
        self.dataset_root = Path(dataset_root) if dataset_root else (
            repo_root / "reports" / "backtest" / PHASE2_DATASET_NAME
        )
        self.expected_dataset_hash = expected_dataset_hash
        self.calendar_path = (
            Path(calendar_path)
            if calendar_path is not None
            else repo_root / CALENDAR_RELATIVE_PATH
        )
        self.expected_calendar_hash = expected_calendar_hash
        self.calendar_resource_path = calendar_resource_path

    def load(self, *, ingested_at: str) -> VerifiedOutcomeDataset:
        source_manifest_path = self.dataset_root / "snapshot_manifest.json"
        data_dir = self.dataset_root / "data"
        if not source_manifest_path.is_file() or not data_dir.is_dir():
            raise OutcomeManifestError("outcome_manifest_missing")
        try:
            source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OutcomeManifestError("outcome_manifest_invalid") from exc
        if (
            source.get("snapshot_fingerprint_sha256") != PHASE2_SOURCE_FINGERPRINT
            or source.get("contract_version") != "provider_compatible_adjusted_v1"
            or source.get("target_symbol_count") != len(PHASE2_UNIVERSE)
            or tuple(sorted(source.get("quality_warning_symbols", [])))
            != tuple(sorted(PHASE2_QUALITY_WARNINGS))
        ):
            raise OutcomeManifestError("outcome_manifest_invalid")

        expected_names = {symbol.replace(".", "-") + ".csv" for symbol in PHASE2_UNIVERSE}
        expected_names.add(BENCHMARK_FILENAME)
        actual_names = {path.name for path in data_dir.glob("*.csv")}
        if actual_names != expected_names:
            raise OutcomeManifestError("outcome_manifest_invalid")

        calendar, calendar_hash = _read_calendar(self.calendar_path)
        if self.expected_calendar_hash and calendar_hash != self.expected_calendar_hash:
            raise OutcomeManifestError("outcome_calendar_hash_mismatch")
        calendar_set = set(calendar)
        resources: list[dict[str, Any]] = []
        bars_by_symbol: dict[str, tuple[OutcomeBar, ...]] = {}
        for symbol in PHASE2_UNIVERSE:
            relative_path = f"data/{symbol.replace('.', '-')}.csv"
            path = self.dataset_root / relative_path
            digest = _sha256_file(path)
            raw_bars = _read_bars(path, symbol)
            bars = tuple(bar for bar in raw_bars if bar.date in calendar_set)
            excluded_count = len(raw_bars) - len(bars)
            if not bars:
                raise OutcomeManifestError("outcome_symbol_has_no_frozen_calendar_bars")
            bars_by_symbol[symbol] = bars
            resources.append({
                "symbol": symbol,
                "path": relative_path,
                "sha256": digest,
                "quality_status": (
                    "quality_warning" if symbol in PHASE2_QUALITY_WARNINGS else "available"
                ),
                "excluded_outside_calendar_rows": excluded_count,
            })
        benchmark_path = data_dir / BENCHMARK_FILENAME
        benchmark_hash = _sha256_file(benchmark_path)
        raw_benchmark_bars = _read_bars(benchmark_path)
        benchmark_bars = tuple(
            bar for bar in raw_benchmark_bars if bar.date in calendar_set
        )
        benchmark_excluded_count = len(raw_benchmark_bars) - len(benchmark_bars)
        if not benchmark_bars:
            raise OutcomeManifestError("outcome_benchmark_has_no_frozen_calendar_bars")
        observed_through = calendar[-1]
        dataset_hash = sha256_json({
            "source_snapshot_fingerprint": PHASE2_SOURCE_FINGERPRINT,
            "symbol_resources": resources,
            "benchmark_sha256": benchmark_hash,
            "calendar_sha256": calendar_hash,
            "calendar_policy": CALENDAR_POLICY,
            "calendar_filter_policy": CALENDAR_FILTER_POLICY,
            "benchmark_excluded_outside_calendar_rows": benchmark_excluded_count,
            "outcome_observed_through_session": observed_through,
            "adjustment_contract": "provider_compatible_adjusted_v1",
        })
        if dataset_hash != self.expected_dataset_hash:
            raise OutcomeManifestError("outcome_manifest_hash_mismatch")
        manifest = OutcomeResourceManifest(
            manifest_id=f"phase2_outcome_manifest_{dataset_hash[:24]}",
            manifest_version=1,
            dataset_name=PHASE2_DATASET_NAME,
            provider="fixed Phase 2 audited adjusted snapshot",
            dataset_hash=dataset_hash,
            date_start=calendar[0],
            date_end=calendar[-1],
            universe_definition="phase2_fixed_14_symbol_research_cohort",
            calendar_resource={
                "resource_id": CALENDAR_RESOURCE_ID,
                "path": self.calendar_resource_path,
                "policy": CALENDAR_POLICY,
                "bar_filter_policy": CALENDAR_FILTER_POLICY,
                "source_snapshot_fingerprint": PHASE2_SOURCE_FINGERPRINT,
            },
            calendar_hash=calendar_hash,
            outcome_observed_through_session=observed_through,
            benchmark_resource={
                "symbol": "^TWII",
                "path": f"data/{BENCHMARK_FILENAME}",
                "sha256": benchmark_hash,
                "return_type": "price_return",
                "excluded_outside_calendar_rows": benchmark_excluded_count,
            },
            benchmark_hash=benchmark_hash,
            ohlc_adjustment_contract="provider_compatible_adjusted_v1",
            corporate_action_contract="frozen_adjusted_history_no_runtime_rewrite",
            symbol_resources=tuple(resources),
            ingested_at=ingested_at,
            created_at=ingested_at,
        )
        manifest.canonical_payload()
        return VerifiedOutcomeDataset(manifest, calendar, bars_by_symbol, benchmark_bars)
