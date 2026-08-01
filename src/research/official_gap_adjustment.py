"""Fail-closed reconstruction of missing adjusted OHLCV from official TWSE rows."""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pandas as pd

from src.research.backtest_evaluation import clean_ohlcv, dataframe_sha256


CONTRACT_VERSION = "provider_compatible_adjusted_v1"
FACTOR_CLUSTER_RELATIVE_TOLERANCE = 1e-6
MINIMUM_FACTOR_ANCHORS = 2


def safe_symbol(symbol: str) -> str:
    return symbol.replace("^", "index-").replace(".", "-")


def parse_list_cell(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _relative_difference(left: float, right: float) -> float:
    scale = max(abs(left), abs(right))
    return abs(left - right) / scale if scale else 0.0


def _records_sha256(records: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(
            records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _factor_consensus_groups(
    candidates: list[dict],
    tolerance: float = FACTOR_CLUSTER_RELATIVE_TOLERANCE,
) -> list[list[dict]]:
    """Return unique candidate groups whose factors agree with a group center."""
    groups: dict[tuple[int, ...], list[dict]] = {}
    for center_index, center in enumerate(candidates):
        member_indexes = tuple(
            index
            for index, candidate in enumerate(candidates)
            if _relative_difference(candidate["factor"], center["factor"]) <= tolerance
        )
        groups.setdefault(member_indexes, [candidates[index] for index in member_indexes])
    return list(groups.values())


def _action_segment(date: str, action_dates: Iterable[str]) -> str:
    prior_actions = [event_date for event_date in action_dates if event_date <= date]
    return max(prior_actions) if prior_actions else "before_first_known_action"


def derive_adjusted_gap_row(
    symbol: str,
    date: str,
    snapshot: pd.DataFrame,
    official_rows: dict[str, dict],
    action_dates: Iterable[str],
    tolerance: float = FACTOR_CLUSTER_RELATIVE_TOLERANCE,
    minimum_anchors: int = MINIMUM_FACTOR_ANCHORS,
) -> dict:
    """Derive one gap row only when a unique near-identical factor consensus exists."""
    if date in set(snapshot["date"].astype(str)):
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "date": date,
            "reason": "target_date_already_exists",
        }
    raw = official_rows.get(date)
    if raw is None:
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "date": date,
            "reason": "official_raw_row_unavailable",
        }
    if int(raw.get("volume") or 0) <= 0:
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "date": date,
            "reason": "official_raw_volume_not_positive",
        }
    required_prices = [raw.get(column) for column in ["open", "high", "low", "close"]]
    if any(value is None or float(value) <= 0 for value in required_prices):
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "date": date,
            "reason": "official_raw_price_invalid",
        }

    month = date[:7]
    target_segment = _action_segment(date, action_dates)
    snapshot_close = dict(
        zip(snapshot["date"].astype(str), pd.to_numeric(snapshot["close"], errors="coerce"))
    )
    candidates = []
    for anchor_date, anchor_raw in sorted(official_rows.items()):
        adjusted_close = snapshot_close.get(anchor_date)
        official_close = anchor_raw.get("close")
        if (
            anchor_date[:7] != month
            or _action_segment(anchor_date, action_dates) != target_segment
            or adjusted_close is None
            or pd.isna(adjusted_close)
            or official_close is None
            or float(official_close) <= 0
        ):
            continue
        candidates.append({
            "date": anchor_date,
            "adjusted_close": float(adjusted_close),
            "official_raw_close": float(official_close),
            "factor": float(adjusted_close) / float(official_close),
        })

    if len(candidates) < minimum_anchors:
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "date": date,
            "reason": "fewer_than_two_same_segment_anchors",
            "candidate_anchor_count": len(candidates),
            "action_segment": target_segment,
        }

    groups = _factor_consensus_groups(candidates, tolerance=tolerance)
    maximum_size = max((len(group) for group in groups), default=0)
    winners = [group for group in groups if len(group) == maximum_size]
    if maximum_size < minimum_anchors:
        reason = "no_factor_consensus"
    elif len(winners) != 1:
        reason = "ambiguous_factor_consensus"
    else:
        reason = None
    if reason:
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "date": date,
            "reason": reason,
            "candidate_anchor_count": len(candidates),
            "largest_consensus_size": maximum_size,
            "largest_consensus_group_count": len(winners),
            "action_segment": target_segment,
        }

    selected = sorted(winners[0], key=lambda item: item["date"])
    factor = float(median(item["factor"] for item in selected))
    row = {
        "symbol": symbol,
        "date": date,
        "open": float(raw["open"]) * factor,
        "high": float(raw["high"]) * factor,
        "low": float(raw["low"]) * factor,
        "close": float(raw["close"]) * factor,
        "volume": float(raw["volume"]),
    }
    return {
        "status": "available",
        "symbol": symbol,
        "date": date,
        "contract_version": CONTRACT_VERSION,
        "adjustment_factor": factor,
        "factor_derivation": "median_of_unique_same_month_same_action_segment_consensus",
        "factor_cluster_relative_tolerance": tolerance,
        "action_segment": target_segment,
        "selected_anchors": selected,
        "candidate_anchor_count": len(candidates),
        "official_raw_ohlcv": {
            column: raw.get(column)
            for column in ["open", "high", "low", "close", "volume"]
        },
        "official_source": {
            "dataset": raw.get("source_dataset"),
            "url": raw.get("source_url"),
            "fetched_at": raw.get("fetched_at"),
            "payload_sha256": raw.get("payload_sha256"),
        },
        "adjusted_row": row,
        "adjusted_row_sha256": dataframe_sha256(pd.DataFrame([row])),
    }


@dataclass
class SnapshotBuildResult:
    snapshots: dict[str, pd.DataFrame]
    provenance: list[dict]
    adjustments: list[dict]
    unresolved: list[dict]
    zero_volume_reconciliation: list[dict]
    summary: dict


def _readonly_connection(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"official audit database not found: {path}")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _load_provenance(snapshot_dir: Path) -> tuple[pd.DataFrame, dict[str, dict]]:
    path = snapshot_dir / "data_provenance.csv"
    if not path.exists():
        raise ValueError("source snapshot requires data_provenance.csv")
    frame = pd.read_csv(path)
    if "symbol" not in frame:
        raise ValueError("source data_provenance.csv requires symbol")
    records = {
        str(row["symbol"]): {
            key: (None if pd.isna(value) else value)
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    }
    return frame, records


def build_reconciled_snapshot(
    source_snapshot_dir: str | Path,
    audit_report_path: str | Path,
    db_path: str,
) -> SnapshotBuildResult:
    source_dir = Path(source_snapshot_dir).resolve()
    audit_path = Path(audit_report_path).resolve()
    if not source_dir.is_dir() or not (source_dir / "data").is_dir():
        raise ValueError("source snapshot data directory is unavailable")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("mode") != "official_twse_integrity_audit":
        raise ValueError("audit report mode is not official_twse_integrity_audit")
    audited_source = Path(audit.get("request_plan", {}).get("snapshot_dir", "")).resolve()
    if audited_source != source_dir:
        raise ValueError("audit report does not belong to the source snapshot")

    _, provenance_by_symbol = _load_provenance(source_dir)
    snapshots: dict[str, pd.DataFrame] = {}
    parent_hashes: dict[str, str] = {}
    for csv_path in sorted((source_dir / "data").glob("*.csv")):
        frame = clean_ohlcv(pd.read_csv(csv_path))
        if frame.empty or "symbol" not in frame:
            continue
        symbol = str(frame.iloc[0]["symbol"])
        actual_hash = dataframe_sha256(frame)
        expected_hash = provenance_by_symbol.get(symbol, {}).get(
            "normalized_snapshot_sha256"
        )
        if expected_hash and str(expected_hash) != actual_hash:
            raise ValueError(
                f"source snapshot hash mismatch: {symbol} "
                f"expected={expected_hash} actual={actual_hash}"
            )
        snapshots[symbol] = frame
        parent_hashes[symbol] = actual_hash

    target_symbols = [str(row["symbol"]) for row in audit.get("symbol_summaries", [])]
    if any(symbol not in snapshots for symbol in target_symbols):
        missing = sorted(symbol for symbol in target_symbols if symbol not in snapshots)
        raise ValueError(f"source snapshot symbols unavailable: {', '.join(missing)}")
    gap_dates: dict[str, list[str]] = {
        symbol: sorted({
            str(row["date"])
            for row in audit.get("classifications", [])
            if row.get("symbol") == symbol
            and row.get("classification") in {
                "provider_missing_official_trade", "official_trade_without_ohlc"
            }
        })
        for symbol in target_symbols
    }

    adjustments: list[dict] = []
    unresolved: list[dict] = []
    zero_records: list[dict] = []
    with _readonly_connection(db_path) as conn:
        market_dates = {
            str(row[0]) for row in conn.execute(
                "SELECT trade_date FROM twse_market_calendar"
            )
        }
        official_by_symbol: dict[str, dict[str, dict]] = {}
        actions_by_symbol: dict[str, list[str]] = {}
        for symbol in target_symbols:
            official_by_symbol[symbol] = {
                str(row["trade_date"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM twse_daily_raw WHERE symbol = ? ORDER BY trade_date",
                    (symbol,),
                )
            }
            code = symbol.split(".")[0]
            actions_by_symbol[symbol] = [
                str(row[0]) for row in conn.execute(
                    "SELECT DISTINCT event_date FROM twse_corporate_actions "
                    "WHERE symbol = ? ORDER BY event_date",
                    (code,),
                )
            ]

        for symbol in target_symbols:
            frame = snapshots[symbol]
            for date in gap_dates[symbol]:
                result = derive_adjusted_gap_row(
                    symbol,
                    date,
                    frame,
                    official_by_symbol[symbol],
                    actions_by_symbol[symbol],
                )
                if result["status"] == "available":
                    frame = clean_ohlcv(pd.concat([
                        frame,
                        pd.DataFrame([result["adjusted_row"]]),
                    ], ignore_index=True))
                    adjustments.append(result)
                else:
                    unresolved.append(result)
            snapshots[symbol] = frame

            removed_zero_dates = parse_list_cell(
                provenance_by_symbol.get(symbol, {}).get("zero_volume_dates_removed")
            )
            observed = set(frame["date"].astype(str))
            for date in removed_zero_dates:
                raw = official_by_symbol[symbol].get(date)
                if date not in market_dates:
                    classification = "provider_non_market_zero_volume_artifact"
                    resolved = True
                elif raw is None:
                    classification = "security_no_trade_or_suspended"
                    resolved = True
                elif int(raw.get("volume") or 0) <= 0:
                    classification = "official_zero_volume"
                    resolved = True
                elif date in observed:
                    classification = "replaced_with_official_positive_volume_row"
                    resolved = True
                else:
                    classification = "unresolved_official_positive_volume_row"
                    resolved = False
                zero_records.append({
                    "symbol": symbol,
                    "date": date,
                    "classification": classification,
                    "resolved": resolved,
                    "official_volume": raw.get("volume") if raw else None,
                })

    output_provenance = []
    benchmark_non_market_dates = []
    if "^TWII" in snapshots:
        benchmark_non_market_dates = sorted(
            date
            for date in snapshots["^TWII"]["date"].astype(str)
            if date not in market_dates
        )
    for symbol, frame in snapshots.items():
        record = dict(provenance_by_symbol.get(symbol, {"symbol": symbol}))
        if symbol in target_symbols:
            symbol_adjustments = [row for row in adjustments if row["symbol"] == symbol]
            symbol_unresolved = [row for row in unresolved if row["symbol"] == symbol]
            symbol_zero = [row for row in zero_records if row["symbol"] == symbol]
            explained_no_bar_dates = sorted({
                str(row["date"])
                for row in audit.get("classifications", [])
                if row.get("symbol") == symbol
                and row.get("classification") in {
                    "official_zero_volume", "security_no_trade_or_suspended"
                }
            })
            frame_start = str(frame["date"].min())
            frame_end = str(frame["date"].max())
            reference_exempt_dates = sorted(set(explained_no_bar_dates).union(
                date
                for date in benchmark_non_market_dates
                if frame_start <= date <= frame_end
            ))
            provider_only_count = next(
                (
                    int(row.get("provider_row_on_non_market_day", 0))
                    for row in audit.get("symbol_summaries", [])
                    if row.get("symbol") == symbol
                ),
                0,
            )
            zero_available = all(row["resolved"] for row in symbol_zero)
            integrity_available = (
                not symbol_unresolved and not provider_only_count and zero_available
            )
            parent_provider = record.get("provider")
            record.update({
                "provider": "hybrid immutable adjusted parent + TWSE official raw gap rows",
                "parent_provider": parent_provider,
                "official_exchange_source": False,
                "official_gap_source": True,
                "adjustment_contract": CONTRACT_VERSION,
                "factor_cluster_relative_tolerance": FACTOR_CLUSTER_RELATIVE_TOLERANCE,
                "minimum_factor_anchors": MINIMUM_FACTOR_ANCHORS,
                "parent_normalized_snapshot_sha256": parent_hashes[symbol],
                "normalized_snapshot_sha256": dataframe_sha256(frame),
                "row_count": int(len(frame)),
                "official_gap_rows_inserted": len(symbol_adjustments),
                "official_gap_rows_unresolved": len(symbol_unresolved),
                "official_gap_adjustment_ledger": "official_gap_adjustments.csv",
                "official_gap_adjustment_records_sha256": _records_sha256(
                    symbol_adjustments
                ),
                "official_gap_unresolved_reasons": json.dumps(
                    symbol_unresolved, ensure_ascii=False, separators=(",", ":")
                ),
                "official_integrity_status": (
                    "available" if integrity_available else "quality_warning"
                ),
                "official_explained_no_bar_dates": json.dumps(
                    explained_no_bar_dates, ensure_ascii=False, separators=(",", ":")
                ),
                "benchmark_provider_non_market_dates": json.dumps(
                    benchmark_non_market_dates, ensure_ascii=False, separators=(",", ":")
                ),
                "official_reference_exempt_dates": json.dumps(
                    reference_exempt_dates, ensure_ascii=False, separators=(",", ":")
                ),
                "zero_volume_reconciliation_status": (
                    "available" if zero_available else "quality_warning"
                ),
                "zero_volume_reconciliation_ledger": (
                    "zero_volume_reconciliation.csv"
                ),
                "zero_volume_reconciliation_records_sha256": _records_sha256(
                    symbol_zero
                ),
            })
        output_provenance.append(record)

    available_symbols = sorted(
        row["symbol"]
        for row in output_provenance
        if row.get("official_integrity_status") == "available"
    )
    warning_symbols = sorted(set(target_symbols) - set(available_symbols))
    audit_sha256 = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    summary = {
        "schema_version": 2,
        "mode": "official_gap_adjusted_snapshot_build",
        "execution_capability": "historical_research_data_only",
        "contract_version": CONTRACT_VERSION,
        "source_snapshot_dir": str(source_dir),
        "source_audit_report": str(audit_path),
        "source_audit_sha256": audit_sha256,
        "target_symbol_count": len(target_symbols),
        "official_gap_count": sum(len(values) for values in gap_dates.values()),
        "resolved_gap_count": len(adjustments),
        "unresolved_gap_count": len(unresolved),
        "quality_approved_symbol_count": len(available_symbols),
        "quality_approved_symbols": available_symbols,
        "quality_warning_symbols": warning_symbols,
        "official_gap_adjustment_records_sha256": _records_sha256(adjustments),
        "official_gap_unresolved_records_sha256": _records_sha256(unresolved),
        "zero_volume_reconciliation_records_sha256": _records_sha256(zero_records),
        "source_snapshot_mutated": False,
    }
    return SnapshotBuildResult(
        snapshots=snapshots,
        provenance=output_provenance,
        adjustments=adjustments,
        unresolved=unresolved,
        zero_volume_reconciliation=zero_records,
        summary=summary,
    )


def snapshot_fingerprint(result: SnapshotBuildResult) -> str:
    payload = {
        "artifact_schema_version": result.summary["schema_version"],
        "contract_version": CONTRACT_VERSION,
        "source_audit_sha256": result.summary["source_audit_sha256"],
        "snapshot_hashes": {
            symbol: dataframe_sha256(frame)
            for symbol, frame in sorted(result.snapshots.items())
        },
        "provenance_contracts": {
            str(row.get("symbol")): {
                key: row.get(key)
                for key in [
                    "adjustment_contract",
                    "official_integrity_status",
                    "official_gap_rows_inserted",
                    "official_gap_rows_unresolved",
                    "official_reference_exempt_dates",
                    "zero_volume_reconciliation_status",
                ]
            }
            for row in result.provenance
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
