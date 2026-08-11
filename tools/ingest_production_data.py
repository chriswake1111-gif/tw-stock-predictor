"""Explicit Phase 10 production-data ingestion entry point.

Examples:
  python tools/ingest_production_data.py official-daily --trade-date 2026-08-11
  python tools/ingest_production_data.py calendar
  python tools/ingest_production_data.py cbc --publication-map release-times.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.production_ingestion_service import ProductionIngestionService
from src.domain.data_foundation import TriggerType


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
    daily.add_argument("--retry-of")
    calendar = commands.add_parser("calendar")
    calendar.add_argument("--retry-of")
    cbc = commands.add_parser("cbc")
    cbc.add_argument(
        "--publication-map",
        help="JSON map of YYYY-MM to authoritative timezone-aware release timestamp",
    )
    cbc.add_argument("--retry-of")
    args = parser.parse_args()
    service = ProductionIngestionService(args.database)
    retry_of = getattr(args, "retry_of", None)
    trigger = TriggerType.RETRY if retry_of else TriggerType.MANUAL
    if args.command == "official-daily":
        result = service.ingest_official_turnover(
            args.trade_date, trigger_type=trigger, retry_of_run_id=retry_of
        )
    elif args.command == "calendar":
        result = service.fetch_twse_calendar(
            trigger_type=trigger, retry_of_run_id=retry_of
        )
    else:
        result = service.fetch_cbc_m1b(
            _json_file(args.publication_map),
            trigger_type=trigger, retry_of_run_id=retry_of,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] in {"succeeded", "partial", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
