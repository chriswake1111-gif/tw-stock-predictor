"""Explicit Phase 10 production-data ingestion entry point.

Examples:
  python tools/ingest_production_data.py official-daily --trade-date 2026-08-11
  python tools/ingest_production_data.py calendar
  python tools/ingest_production_data.py cbc --publication-map release-times.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.services.production_ingestion_service import ProductionIngestionService


def _json_file(path: str | None) -> dict:
    if not path:
        return {}
    with Path(path).open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("publication map must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest official data with Phase 10 evidence governance"
    )
    parser.add_argument("--database", default="data/cache.db")
    commands = parser.add_subparsers(dest="command", required=True)
    daily = commands.add_parser("official-daily")
    daily.add_argument("--trade-date", required=True)
    commands.add_parser("calendar")
    cbc = commands.add_parser("cbc")
    cbc.add_argument(
        "--publication-map",
        help="JSON map of YYYY-MM to authoritative timezone-aware release timestamp",
    )
    args = parser.parse_args()
    service = ProductionIngestionService(args.database)
    if args.command == "official-daily":
        result = service.ingest_official_turnover(args.trade_date)
    elif args.command == "calendar":
        result = service.fetch_twse_calendar()
    else:
        result = service.fetch_cbc_m1b(_json_file(args.publication_map))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] in {"succeeded", "partial", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
