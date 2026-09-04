"""Prepare deterministic SQLite states for the installed Windows smoke only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.services.evidence_backup_service import EvidenceBackupService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("current", "upgradeable", "legacy"):
        command = commands.add_parser(name)
        command.add_argument("database")
        command.add_argument("--symbol", default=None)
    backup = commands.add_parser("backup")
    backup.add_argument("source")
    backup.add_argument("destination")
    symbols = commands.add_parser("symbols")
    symbols.add_argument("database")
    args = parser.parse_args()

    if args.command in {"current", "upgradeable"}:
        apply_valuation_migration(args.database, resource_root=ROOT)
        if args.symbol:
            ResearchWorkflowRepository(args.database).add_membership(args.symbol)
        if args.command == "upgradeable":
            with sqlite3.connect(args.database) as conn:
                conn.execute(
                    "DELETE FROM additive_schema_migrations WHERE version_id=?",
                    ("20260902_21_installed_data_operations",),
                )
                conn.execute("DROP TABLE IF EXISTS installed_data_operation_items")
                conn.execute("DROP TABLE IF EXISTS installed_data_operations")
        result: object = {"status": "created", "state": args.command}
    elif args.command == "legacy":
        path = Path(args.database)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE legacy_rows(value TEXT)")
            conn.execute("INSERT INTO legacy_rows VALUES ('preserve-me')")
        result = {"status": "created", "state": "legacy"}
    elif args.command == "backup":
        result = EvidenceBackupService.backup(args.source, args.destination)
    else:
        with sqlite3.connect(args.database) as conn:
            result = {
                "status": "read",
                "symbols": [
                    str(row[0])
                    for row in conn.execute(
                        "SELECT symbol FROM research_watchlist_items ORDER BY symbol"
                    ).fetchall()
                ],
            }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
