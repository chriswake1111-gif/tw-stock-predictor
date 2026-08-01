"""Build and reconcile the Phase 1 official TWSE integrity layer."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_official_collector import TWSEOfficialCollector
from src.research.backtest_evaluation import normalize_symbol


def month_range(start_date: str, end_date: str) -> list[str]:
    start = pd.Period(start_date[:7], freq="M")
    end = pd.Period(end_date[:7], freq="M")
    return [str(period) for period in pd.period_range(start, end, freq="M")]


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("^", "index-").replace(".", "-")


def discover_symbols(snapshot_dir: Path) -> list[str]:
    provenance_path = snapshot_dir / "data_provenance.csv"
    if provenance_path.exists():
        frame = pd.read_csv(provenance_path)
        symbols = [
            normalize_symbol(value)
            for value in frame.get("symbol", pd.Series(dtype=str)).dropna().astype(str)
            if not str(value).startswith("^")
        ]
        if symbols:
            return list(dict.fromkeys(symbols))
    symbols = []
    for path in sorted((snapshot_dir / "data").glob("*-TW.csv")):
        symbols.append(path.stem.replace("-TW", ".TW"))
    return symbols


def load_snapshot(snapshot_dir: Path, symbol: str) -> pd.DataFrame:
    path = snapshot_dir / "data" / f"{_safe_symbol(symbol)}.csv"
    frame = pd.read_csv(path)
    frame["date"] = frame["date"].astype(str)
    return frame


def _list_cell(value) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def initial_discrepancy_months(snapshot_dir: Path, symbols: list[str]) -> set[tuple[str, str]]:
    quality_path = snapshot_dir / "data_quality.csv"
    provenance_path = snapshot_dir / "data_provenance.csv"
    quality = pd.read_csv(quality_path) if quality_path.exists() else pd.DataFrame()
    provenance = pd.read_csv(provenance_path) if provenance_path.exists() else pd.DataFrame()
    result: set[tuple[str, str]] = set()
    for symbol in symbols:
        dates: list[str] = []
        if not quality.empty and "symbol" in quality:
            match = quality[quality["symbol"] == symbol]
            if not match.empty:
                dates.extend(_list_cell(match.iloc[0].get("missing_reference_dates")))
        if not provenance.empty and "symbol" in provenance:
            match = provenance[provenance["symbol"] == symbol]
            if not match.empty:
                dates.extend(_list_cell(match.iloc[0].get("zero_volume_dates_removed")))
        result.update((symbol, date[:7]) for date in dates)
    return result


def build_dry_run_plan(
    snapshot_dir: Path,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict:
    calendar_months = month_range(start_date, end_date)
    initial_months = initial_discrepancy_months(snapshot_dir, symbols)
    years = list(range(int(start_date[:4]), int(end_date[:4]) + 1))
    return {
        "mode": "dry_run",
        "snapshot_dir": str(snapshot_dir.resolve()),
        "symbols": symbols,
        "requested_start": start_date,
        "requested_end": end_date,
        "calendar_months": len(calendar_months),
        "initial_symbol_months": len(initial_months),
        "corporate_action_year_requests": len(years) * 2,
        "minimum_request_count": len(calendar_months) + len(initial_months) + len(years) * 2,
        "note": "exact symbol-month requests are recalculated from the official calendar before stock queries",
    }


def official_gaps(
    snapshot_dir: Path,
    symbols: list[str],
    official_dates: set[str],
    start_date: str,
    end_date: str,
) -> dict[str, list[str]]:
    result = {}
    for symbol in symbols:
        frame = load_snapshot(snapshot_dir, symbol)
        observed = set(frame["date"])
        if frame.empty:
            result[symbol] = sorted(official_dates)
            continue
        lower = max(start_date, str(frame["date"].min()))
        upper = min(end_date, str(frame["date"].max()))
        expected = {date for date in official_dates if lower <= date <= upper}
        result[symbol] = sorted(expected - observed)
    return result


def reconcile(
    collector: TWSEOfficialCollector,
    snapshot_dir: Path,
    symbols: list[str],
    official_dates: set[str],
    gaps: dict[str, list[str]],
    start_date: str,
    end_date: str,
) -> dict:
    actions = collector.actions_for_symbols(symbols, start_date, end_date)
    action_dates: dict[tuple[str, str], list[str]] = {}
    for action in actions:
        key = (f"{action['symbol']}.TW", action["event_date"])
        action_dates.setdefault(key, []).append(action["event_type"])

    symbol_summaries = []
    classifications = []
    for symbol in symbols:
        frame = load_snapshot(snapshot_dir, symbol)
        observed = set(frame["date"])
        official_raw = collector.raw_dates(symbol, gaps[symbol])
        counts = {
            "provider_missing_official_trade": 0,
            "security_no_trade_or_suspended": 0,
            "official_zero_volume": 0,
            "provider_row_on_non_market_day": 0,
        }
        for date in gaps[symbol]:
            raw = official_raw.get(date)
            if raw is None:
                classification = "security_no_trade_or_suspended"
            elif int(raw.get("volume") or 0) <= 0:
                classification = "official_zero_volume"
            else:
                classification = "provider_missing_official_trade"
            counts[classification] += 1
            classifications.append({
                "symbol": symbol,
                "date": date,
                "classification": classification,
                "official_volume": raw.get("volume") if raw else None,
                "corporate_actions": action_dates.get((symbol, date), []),
            })

        lower = max(start_date, str(frame["date"].min()))
        upper = min(end_date, str(frame["date"].max()))
        provider_only = sorted(
            date for date in observed
            if lower <= date <= upper and date not in official_dates
        )
        counts["provider_row_on_non_market_day"] = len(provider_only)
        classifications.extend({
            "symbol": symbol,
            "date": date,
            "classification": "provider_row_on_non_market_day",
            "official_volume": None,
            "corporate_actions": action_dates.get((symbol, date), []),
        } for date in provider_only)

        warning_count = (
            counts["provider_missing_official_trade"]
            + counts["provider_row_on_non_market_day"]
        )
        symbol_summaries.append({
            "symbol": symbol,
            "official_integrity_status": "quality_warning" if warning_count else "available",
            **counts,
            "corporate_action_count": sum(
                1 for action in actions if f"{action['symbol']}.TW" == symbol
            ),
        })

    warning_symbols = [
        row["symbol"] for row in symbol_summaries
        if row["official_integrity_status"] == "quality_warning"
    ]
    return {
        "schema_version": 1,
        "mode": "official_twse_integrity_audit",
        "execution_capability": "read_only_market_data_audit",
        "status": "quality_warning" if warning_symbols else "available",
        "requested_start": start_date,
        "requested_end": end_date,
        "symbol_count": len(symbols),
        "quality_approved_symbol_count": len(symbols) - len(warning_symbols),
        "quality_warning_symbols": warning_symbols,
        "corporate_action_scope": ["TWT49U", "TWTAUU"],
        "unsupported_corporate_action_types": [
            "stock_face_value_change", "etf_split_or_reverse_split"
        ],
        "symbol_summaries": symbol_summaries,
        "classifications": classifications,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="建立 TWSE Phase 1 官方資料完整性層")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--db-path", default="data/cache.db")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbols", default=None, help="可選逗號分隔標的")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--audit-id", default=None)
    parser.add_argument("--rate-limit-ms", type=int, default=150)
    parser.add_argument("--output", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot_dir = Path(args.snapshot_dir)
    symbols = (
        [normalize_symbol(item) for item in args.symbols.split(",") if item.strip()]
        if args.symbols
        else discover_symbols(snapshot_dir)
    )
    dry_plan = build_dry_run_plan(snapshot_dir, symbols, args.start, args.end)
    if args.dry_run:
        print(json.dumps(dry_plan, ensure_ascii=False, indent=2))
        return 0

    audit_id = args.audit_id or datetime.now().strftime("twse-phase1-%Y%m%dT%H%M%S")
    output_path = Path(args.output) if args.output else snapshot_dir / "official_integrity_audit.json"
    collector = TWSEOfficialCollector(db_path=args.db_path)
    completed = collector.start_or_resume_audit(
        audit_id, str(snapshot_dir.resolve()), args.start, args.end, symbols, dry_plan
    )
    delay = max(args.rate_limit_ms, 0) / 1000.0

    def run_request(key: str, callback) -> None:
        if key in completed:
            return
        callback()
        completed.add(key)
        collector.checkpoint_audit(audit_id, completed)
        if delay:
            time.sleep(delay)

    try:
        for month in month_range(args.start, args.end):
            run_request(f"calendar:{month}", lambda month=month: collector.fetch_market_month(month))

        calendar = set(collector.market_dates(args.start, args.end))
        gaps = official_gaps(
            snapshot_dir, symbols, calendar, args.start, args.end
        )
        stock_months = sorted({
            (symbol, date[:7])
            for symbol, dates in gaps.items()
            for date in dates
        })
        years = list(range(int(args.start[:4]), int(args.end[:4]) + 1))
        exact_plan = {
            **dry_plan,
            "mode": "execute",
            "official_trading_days": len(calendar),
            "exact_symbol_months": len(stock_months),
            "exact_request_count": len(month_range(args.start, args.end))
            + len(stock_months) + len(years) * 2,
        }
        collector.checkpoint_audit(audit_id, completed, plan=exact_plan)

        for symbol, month in stock_months:
            run_request(
                f"stock:{symbol}:{month}",
                lambda symbol=symbol, month=month: collector.fetch_stock_month(symbol, month),
            )
        for year in years:
            run_request(
                f"exright:{year}",
                lambda year=year: collector.fetch_exright_year(year),
            )
            run_request(
                f"reduction:{year}",
                lambda year=year: collector.fetch_reduction_year(year),
            )

        summary = reconcile(
            collector, snapshot_dir, symbols, calendar, gaps, args.start, args.end
        )
        summary["audit_id"] = audit_id
        summary["request_plan"] = exact_plan
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        collector.finish_audit(audit_id, completed, summary)
    except Exception as exc:
        collector.checkpoint_audit(audit_id, completed, error_text=str(exc))
        raise

    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered if args.json else (
        f"{summary['status']}: {summary['quality_approved_symbol_count']} / "
        f"{summary['symbol_count']} symbols quality approved; report={output_path.resolve()}"
    ))
    return 1 if summary["status"] == "quality_warning" else 0


if __name__ == "__main__":
    raise SystemExit(main())
