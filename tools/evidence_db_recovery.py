"""Offline backup, restore, and integrity validation for Evidence V2 SQLite."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.evidence_backup_service import EvidenceBackupService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or copy immutable Evidence V2 SQLite state"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("source_db")
    backup.add_argument("backup_db")
    restore = commands.add_parser("restore")
    restore.add_argument("backup_db")
    restore.add_argument("restored_db")
    validate = commands.add_parser("validate")
    validate.add_argument("db_path")
    args = parser.parse_args()
    if args.command == "backup":
        result = EvidenceBackupService.backup(args.source_db, args.backup_db)
    elif args.command == "restore":
        result = EvidenceBackupService.restore(args.backup_db, args.restored_db)
    else:
        result = EvidenceBackupService.validate(args.db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
