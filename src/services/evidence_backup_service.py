"""Deterministic SQLite backup/restore helpers for irreplaceable Evidence V2 state."""

from __future__ import annotations

import sqlite3
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
)


class EvidenceBackupService:
    @staticmethod
    def _resolved(path: str) -> Path:
        return Path(path).resolve()

    @classmethod
    def backup(cls, source_db: str, backup_db: str) -> dict[str, Any]:
        source = cls._resolved(source_db)
        destination = cls._resolved(backup_db)
        if source == destination:
            raise ValueError("backup destination must differ from source database")
        if not source.is_file():
            raise ValueError("source database does not exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("backup destination already exists")
        with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as dest_conn:
            source_conn.backup(dest_conn)
        return cls.validate(str(destination))

    @classmethod
    def restore(cls, backup_db: str, restored_db: str) -> dict[str, Any]:
        backup = cls._resolved(backup_db)
        destination = cls._resolved(restored_db)
        if not backup.is_file():
            raise ValueError("backup database does not exist")
        if backup == destination:
            raise ValueError("restore destination must differ from backup database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("restore destination already exists")
        with sqlite3.connect(backup) as source_conn, sqlite3.connect(destination) as dest_conn:
            source_conn.backup(dest_conn)
        return cls.validate(str(destination))

    @classmethod
    def validate(cls, db_path: str) -> dict[str, Any]:
        path = cls._resolved(db_path)
        with sqlite3.connect(path) as conn:
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
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "data_providers", "data_resources", "ingestion_runs",
                    "ingestion_run_items", "raw_resource_revisions",
                    "data_quality_issues", "trading_calendar_revisions",
                    "snapshot_dependency_checks", "resource_publication_evidence",
                    "ingestion_lock_recovery_events", "ingestion_resource_locks",
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
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        return {
            "status": "valid",
            "integrity_check": integrity,
            "irreplaceable_counts": counts,
            "operational_provenance_counts": operational,
            "research_workflow_counts": workflow,
        }
