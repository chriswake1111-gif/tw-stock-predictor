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
                table: conn.execute(
                    f'''SELECT COUNT(*) FROM "{table}"'''
                    + (''' WHERE resource_id NOT IN (SELECT resource_id FROM universe_resource_policies)'''
                       + ''' AND resource_type NOT IN ('eod_close','product_classification')'''
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
        return {
            "status": "valid",
            "integrity_check": integrity,
            "irreplaceable_counts": counts,
            "operational_provenance_counts": operational,
            "universe_foundation_counts": universe_foundation,
            "eod_foundation_counts": eod_foundation,
            "research_workflow_counts": workflow,
        }
