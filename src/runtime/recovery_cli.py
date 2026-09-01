"""Deterministic local recovery command shared by source and packaged entrypoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from src.services.evidence_backup_service import EvidenceBackupService

from .paths import RuntimePathError, RuntimePaths
from .restore import RestoreError, restore_backup_and_activate


def _print(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=sys.stderr if error else sys.stdout,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    paths: RuntimePaths | None = None,
    packaged: bool = False,
) -> int:
    parser = argparse.ArgumentParser(description="Local Evidence V2 recovery")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("source_db")
    backup.add_argument("backup_db")
    restore = commands.add_parser("restore")
    restore.add_argument("backup_db")
    restore.add_argument("restored_db")
    activate = commands.add_parser("activate")
    activate.add_argument("backup_db")
    activate.add_argument("canonical_db", nargs="?")
    activate.add_argument("--backup-root", default=None)
    activate.add_argument("--runtime-dir", default=None)
    validate = commands.add_parser("validate")
    validate.add_argument("db_path")
    args = parser.parse_args(argv)

    try:
        resolved = paths or RuntimePaths.from_environment()
        if args.command == "backup":
            result = EvidenceBackupService.backup(args.source_db, args.backup_db)
        elif args.command == "restore":
            result = EvidenceBackupService.restore(args.backup_db, args.restored_db)
        elif args.command == "activate":
            canonical = Path(args.canonical_db).resolve(strict=False) if args.canonical_db else resolved.database_path
            backup_root = Path(args.backup_root).resolve(strict=False) if args.backup_root else resolved.backup_dir
            runtime_dir = Path(args.runtime_dir).resolve(strict=False) if args.runtime_dir else resolved.runtime_dir
            if packaged and canonical != resolved.database_path:
                raise RestoreError("recovery_packaged_canonical_override_rejected")
            if packaged and backup_root != resolved.backup_dir:
                raise RestoreError("recovery_packaged_backup_root_override_rejected")
            if packaged and runtime_dir != resolved.runtime_dir:
                raise RestoreError("recovery_packaged_runtime_override_rejected")
            result = restore_backup_and_activate(
                args.backup_db,
                canonical,
                backup_root=backup_root,
                runtime_dir=runtime_dir,
                resource_root=resolved.resource_root,
            )
        else:
            result = EvidenceBackupService.validate(args.db_path)
    except (RestoreError, RuntimePathError, OSError, RuntimeError, ValueError) as exc:
        code = getattr(exc, "code", f"recovery_{args.command}_failed")
        _print({"status": "failed", "code": code}, error=True)
        return 2
    _print(result)
    return 0


__all__ = ["main"]
