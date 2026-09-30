"""Fixed synthetic replay only. Not an anchor, valuation or backtest producer."""
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.domain.valuation import normalize_utc_timestamp
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.services.wave_qualification_service import price_quality

CONTRACT = "wave_pivot_lab_v1"
RULE = "PIVOT-EXP-01"
VERSION = "1.0.0"
MAX_FIXTURE_ROWS = 256


def replay_fixture(payload, input_hash):
    if not isinstance(payload, dict) or payload.get("synthetic") is not True or payload.get("contract_version") != "wave_pivot_fixture_v1":
        raise ValueError("only_explicit_synthetic_fixture_supported")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FIXTURE_ROWS or not all(isinstance(r, dict) for r in rows):
        raise ValueError("fixture_rows_invalid_or_unbounded")
    observed = normalize_utc_timestamp(payload["observed_at"], "observed_at")
    quality = price_quality(rows, rows, [], None)
    if quality["status"] != "passed":
        raise ValueError("fixture_price_quality_failed")
    dates = [date.fromisoformat(r["date"]).isoformat() for r in rows]
    if dates != sorted(dates) or dates[-1] > observed[:10]:
        raise ValueError("fixture_date_or_observation_order_invalid")
    engine = WaveFibonacciEngine(config_path="")
    frame = pd.DataFrame(rows)
    pivots = engine.detect_confirmed_pivots(frame, confirmation_bars=3, lookback=5)
    for end in range(1, len(rows) + 1):
        prefix = engine.detect_confirmed_pivots(frame.iloc[:end], confirmation_bars=3, lookback=5)
        if prefix != [p for p in pivots if p.confirmed_at_index < end]:
            raise ValueError("prefix_replay_changed_confirmed_pivot")
    collisions = Counter(p.pivot_date for p in pivots)
    return dict(contract_version=CONTRACT, rule_id=RULE, rule_version=VERSION,
        evidence_level="C", implementation_mode="project_operationalization",
        project_operationalization=True, official_affiliation=False,
        algorithm_version="existing_pivot_lookback5_confirm3_v1", source_sha256=input_hash,
        synthetic=True, mode="isolated_fixed_snapshot_replay", status="passed",
        parameters=dict(lookback=5, confirmation_bars=3), prefixes_checked=len(rows),
        historical_availability="not_asserted", automatic_candidates_eligible=False,
        note="只驗證固定測試快照的逐段一致性；不是正式波段、價格目標或歷史當時可用證據。",
        pivots=[dict(type=p.pivot_type, pivot_date=p.pivot_date, price=p.pivot_price,
                     confirmation_session=p.confirmed_at, known_at=observed,
                     order_status="ambiguous_same_session" if collisions[p.pivot_date] > 1 else "single_pivot",
                     usable_as_anchor=False) for p in pivots])


def run_manifest(manifest_path):
    path = Path(manifest_path).resolve(strict=True)
    if path.stat().st_size > 16 * 1024:
        raise ValueError("fixture_manifest_too_large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("invalid_fixture_manifest")
    entries = manifest.get("fixtures")
    if manifest.get("contract_version") != "wave_pivot_manifest_v1" or not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise ValueError("invalid_fixture_manifest")
    results = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid_fixture_entry")
        relative = Path(entry["path"])
        source = (path.parent / relative).resolve(strict=True)
        if relative.is_absolute() or not source.is_relative_to(path.parent) or source.stat().st_size > 256 * 1024:
            raise ValueError("fixture_path_or_size_invalid")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if entry.get("sha256") != digest:
            raise ValueError("fixture_hash_mismatch")
        results.append(replay_fixture(json.loads(content), digest))
    return dict(contract_version=CONTRACT, mode="synthetic_manifest", results=results,
                automatic_candidates_eligible=False)
