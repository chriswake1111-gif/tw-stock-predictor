"""Compare fixed research snapshots with official TWSE monthly raw trading rows."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_official_collector import parse_twse_stock_day


def audit_month(symbol: str, month: str, snapshot: pd.DataFrame, official_rows: list[dict]) -> dict:
    month_prefix = datetime.strptime(month, "%Y-%m").strftime("%Y-%m")
    provider = snapshot[snapshot["date"].astype(str).str.startswith(month_prefix)].copy()
    provider_dates = set(provider["date"].astype(str))
    official_dates = {row["trade_date"] for row in official_rows}
    zero_volume_dates = sorted(
        provider.loc[pd.to_numeric(provider["volume"], errors="coerce") <= 0, "date"]
        .astype(str)
        .tolist()
    )
    missing = sorted(official_dates - provider_dates)
    provider_only = sorted(provider_dates - official_dates)
    return {
        "symbol": symbol,
        "month": month_prefix,
        "status": "quality_warning" if missing or zero_volume_dates else "matched",
        "official_row_count": len(official_rows),
        "provider_row_count": len(provider),
        "missing_official_dates": missing,
        "provider_only_dates": provider_only,
        "zero_volume_provider_dates": zero_volume_dates,
        "official_missing_rows": [
            row for row in official_rows if row["trade_date"] in missing
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="唯讀比對研究快照與 TWSE 官方月成交日")
    parser.add_argument("--snapshot-dir", required=True, help="包含 data/*.csv 的回測報告目錄")
    parser.add_argument(
        "--check",
        action="append",
        required=True,
        help="重複指定 SYMBOL:YYYY-MM，例如 1301.TW:2025-08",
    )
    parser.add_argument("--output", default=None, help="可選 JSON 稽核輸出路徑")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.snapshot_dir)
    audits = []
    for check in args.check:
        symbol, month = check.rsplit(":", 1)
        datetime.strptime(month, "%Y-%m")
        stock_no = symbol.split(".")[0]
        safe_symbol = symbol.replace("^", "index-").replace(".", "-")
        snapshot_path = root / "data" / f"{safe_symbol}.csv"
        snapshot = pd.read_csv(snapshot_path)
        query_date = f"{month.replace('-', '')}01"
        source_url = (
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
            f"?response=json&date={query_date}&stockNo={stock_no}"
        )
        response = requests.get(source_url, timeout=20)
        response.raise_for_status()
        audit = audit_month(symbol, month, snapshot, parse_twse_stock_day(response.json()))
        audit["source_url"] = source_url
        audits.append(audit)

    result = {
        "schema_version": 1,
        "mode": "read_only_twse_snapshot_audit",
        "audits": audits,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if any(item["status"] == "quality_warning" for item in audits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
