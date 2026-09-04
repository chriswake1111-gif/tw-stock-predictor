"""Deterministic SQLite backup/restore helpers for irreplaceable Evidence V2 state."""

from __future__ import annotations

import sqlite3
import os
import json
import hashlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IRREPLACEABLE_TABLES = (
    "forward_eps_observations",
    "pe_scenarios",
    "valuation_approvals",
    "technical_anchor_revisions",
    "technical_anchor_approvals",
    "deployment_plan_revisions",
    "deployment_plan_approvals",
    "screening_profile_revisions",
    "screening_profile_approvals",
    "synthesis_profile_revisions",
    "synthesis_profile_approvals",
    "analysis_snapshots",
    "universe_resource_policies",
    "universe_instruments",
    "universe_revisions",
    "universe_instrument_revisions",
    "universe_identity_alias_events",
    "universe_lifecycle_events",
    "universe_operational_state_events",
    "universe_ingestion_idempotency",
    "eod_price_resource_policies",
    "eod_close_source_snapshots",
    "eod_product_classification_evidence",
    "eod_close_observations",
    "eod_ingestion_idempotency",
    "eod_ingestion_command_reservations",
)


class EvidenceBackupService:
    @staticmethod
    def _resolved(path: str) -> Path:
        return Path(path).resolve()

    @classmethod
    def _metadata_path(cls, db_path: str | Path) -> Path:
        return Path(f"{cls._resolved(str(db_path))}.meta.json")

    @classmethod
    def _write_metadata(cls, db_path: str | Path, metadata: dict[str, Any]) -> None:
        destination = cls._metadata_path(db_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)

    @classmethod
    def _read_metadata(cls, db_path: str | Path) -> dict[str, Any] | None:
        path = cls._metadata_path(db_path)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("backup metadata is invalid") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("backup metadata is invalid")
        return payload

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _migration_metadata(cls, conn: sqlite3.Connection) -> dict[str, Any]:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        result: dict[str, Any] = {"migration_ids": [], "migration_checksums": {}}
        for table in ("schema_migrations", "additive_schema_migrations"):
            if table not in tables:
                continue
            rows = conn.execute(
                f"SELECT version_id, checksum_sha256 FROM {table} ORDER BY version_id"
            ).fetchall()
            for version_id, checksum in rows:
                result["migration_ids"].append(str(version_id))
                result["migration_checksums"][str(version_id)] = str(checksum)
        return result

    @classmethod
    def _metadata(
        cls,
        db_path: Path,
        *,
        source_sha256: str,
        reason: str,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        with closing(sqlite3.connect(db_path)) as conn:
            migrations = cls._migration_metadata(conn)
        return {
            "metadata_version": "tw_stock_backup_metadata_v1",
            "source_sha256": source_sha256,
            "backup_sha256": cls._sha256(db_path),
            "app_version": os.getenv("TW_STOCK_APP_VERSION", "unknown"),
            "build_sha": os.getenv("TW_STOCK_BUILD_SHA", "unknown"),
            "schema_state": "known_v2" if migrations["migration_ids"] else "legacy_or_unknown",
            "applied_migration_ids": migrations["migration_ids"],
            "migration_checksums": migrations["migration_checksums"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "validation_status": validation.get("status"),
            "foundation_counts": {
                "operational_provenance": validation.get("operational_provenance_counts", {}),
                "universe": validation.get("universe_foundation_counts", {}),
                "eod": validation.get("eod_foundation_counts", {}),
                "workflow": validation.get("research_workflow_counts", {}),
            },
        }

    @classmethod
    def backup(
        cls,
        source_db: str,
        backup_db: str,
        *,
        reason: str = "manual",
    ) -> dict[str, Any]:
        source = cls._resolved(source_db)
        destination = cls._resolved(backup_db)
        if source == destination:
            raise ValueError("backup destination must differ from source database")
        if not source.is_file():
            raise ValueError("source database does not exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("backup destination already exists")
        with closing(sqlite3.connect(source)) as source_conn, closing(sqlite3.connect(destination)) as dest_conn:
            source_conn.backup(dest_conn)
        validation = cls.validate(str(destination))
        metadata = cls._metadata(
            destination,
            source_sha256=cls._sha256(source),
            reason=reason,
            validation=validation,
        )
        cls._write_metadata(destination, metadata)
        validation["backup_metadata"] = metadata
        return validation

    @classmethod
    def restore(
        cls,
        backup_db: str,
        restored_db: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        backup = cls._resolved(backup_db)
        destination = cls._resolved(restored_db)
        if not backup.is_file():
            raise ValueError("backup database does not exist")
        if backup == destination:
            raise ValueError("restore destination must differ from backup database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("restore destination already exists")
        with closing(sqlite3.connect(backup)) as source_conn, closing(sqlite3.connect(destination)) as dest_conn:
            source_conn.backup(dest_conn)
        validation = cls.validate(str(destination))
        source_metadata = cls._read_metadata(backup)
        if source_metadata is not None:
            metadata = dict(source_metadata)
            metadata["backup_sha256"] = cls._sha256(destination)
            if reason is not None:
                metadata["reason"] = reason
        else:
            metadata = cls._metadata(
                destination,
                source_sha256=cls._sha256(backup),
                reason=reason or "recovery",
                validation=validation,
            )
        cls._write_metadata(destination, metadata)
        validation["backup_metadata"] = metadata
        return validation

    @classmethod
    def validate(cls, db_path: str) -> dict[str, Any]:
        path = cls._resolved(db_path)
        if not path.is_file():
            raise ValueError("database does not exist")
        with closing(sqlite3.connect(path)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            table_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            counts = {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in IRREPLACEABLE_TABLES
                if table in table_names
            }
            operational = {
                table: conn.execute(
                    f'''SELECT COUNT(*) FROM "{table}"'''
                    + (''' WHERE resource_id NOT IN (SELECT resource_id FROM universe_resource_policies)'''
                       + ''' AND resource_type NOT IN ('eod_close','product_classification')'''
                       + ''' AND resource_id NOT IN ('twse.t187ap03_L','tpex.mopsfin_t187ap03_O')'''
                       if table == "data_resources" and "universe_resource_policies" in table_names else "")
                    + (''' WHERE provider_id NOT IN (SELECT DISTINCT r.provider_id FROM data_resources r JOIN universe_resource_policies up ON up.resource_id = r.resource_id)'''
                       + ''' AND provider_id NOT IN (SELECT DISTINCT provider_id FROM data_resources WHERE resource_type IN ('eod_close','product_classification'))'''
                       if table == "data_providers" and "universe_resource_policies" in table_names else "")
                ).fetchone()[0]
                for table in (
                    "data_providers", "data_resources", "ingestion_runs",
                    "ingestion_run_items", "raw_resource_revisions",
                    "data_quality_issues", "trading_calendar_revisions",
                    "snapshot_dependency_checks", "resource_publication_evidence",
                    "ingestion_lock_recovery_events", "ingestion_resource_locks",
                )
                if table in table_names
            }
            universe_foundation = {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "universe_resource_policies", "universe_instruments", "universe_revisions",
                    "universe_instrument_revisions", "universe_identity_alias_events",
                    "universe_lifecycle_events", "universe_operational_state_events",
                    "universe_ingestion_idempotency",
                )
                if table in table_names
            }
            eod_foundation = {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "eod_price_resource_policies", "eod_close_source_snapshots",
                    "eod_product_classification_evidence", "eod_close_observations",
                    "eod_ingestion_idempotency", "eod_ingestion_command_reservations",
                )
                if table in table_names
            }
            workflow = {}
            if "research_watchlist_items" in table_names:
                workflow = {
                    "membership_total": conn.execute(
                        "SELECT COUNT(*) FROM research_watchlist_items"
                    ).fetchone()[0],
                    "active_count": conn.execute(
                        "SELECT COUNT(*) FROM research_watchlist_items WHERE membership_state='active'"
                    ).fetchone()[0],
                    "archived_count": conn.execute(
                        "SELECT COUNT(*) FROM research_watchlist_items WHERE membership_state='archived'"
                    ).fetchone()[0],
                    "review_event_count": conn.execute(
                        "SELECT COUNT(*) FROM research_review_events"
                    ).fetchone()[0],
                }
                workflow_violations = [
                    row
                    for table in ("research_watchlist_items", "research_review_events")
                    for row in conn.execute(f"PRAGMA foreign_key_check({table})").fetchall()
                ]
                if workflow_violations:
                    raise RuntimeError(
                        f"workflow foreign key check failed: {workflow_violations[:3]}"
                    )
            universe_tables = [
                "universe_resource_policies", "universe_instruments", "universe_revisions",
                "universe_instrument_revisions", "universe_identity_alias_events",
                "universe_lifecycle_events", "universe_operational_state_events",
                "universe_ingestion_idempotency",
            ]
            universe_violations = [
                row for table in universe_tables if table in table_names
                for row in conn.execute(f"PRAGMA foreign_key_check({table})").fetchall()
            ]
            if universe_violations:
                raise RuntimeError(
                    f"universe foreign key check failed: {universe_violations[:3]}"
                )
            eod_tables = [
                "eod_price_resource_policies", "eod_close_source_snapshots",
                "eod_product_classification_evidence", "eod_close_observations",
                "eod_ingestion_idempotency", "eod_ingestion_command_reservations",
            ]
            eod_violations = [
                row for table in eod_tables if table in table_names
                for row in conn.execute(f"PRAGMA foreign_key_check({table})").fetchall()
            ]
            if eod_violations:
                raise RuntimeError(
                    f"EOD foreign key check failed: {eod_violations[:3]}"
                )
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        result = {
            "status": "valid",
            "integrity_check": integrity,
            "irreplaceable_counts": counts,
            "operational_provenance_counts": operational,
            "universe_foundation_counts": universe_foundation,
            "eod_foundation_counts": eod_foundation,
            "research_workflow_counts": workflow,
        }
        metadata = cls._read_metadata(path)
        if metadata is not None:
            if metadata.get("backup_sha256") != cls._sha256(path):
                raise RuntimeError("backup metadata checksum mismatch")
            result["backup_metadata"] = metadata
        return result
