"""Read-only classification of the canonical SQLite database state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from src.repositories.migration_runner import migration_manifest

from .manifest import sha256_file


class DatabaseState(str, Enum):
    FRESH = "fresh"
    KNOWN_V2_CURRENT = "known_v2_current"
    KNOWN_V2_UPGRADEABLE = "known_v2_upgradeable"
    LEGACY = "legacy"
    CORRUPT_UNKNOWN = "corrupt_unknown"


V2_REQUIRED_TABLES = (
    "schema_migrations",
    "additive_schema_migrations",
    "analysis_snapshots",
    "analysis_snapshot_idempotency_keys",
    "data_providers",
    "data_resources",
    "data_quality_issues",
    "ingestion_runs",
    "ingestion_run_items",
    "raw_resource_revisions",
    "trading_calendar_revisions",
    "resource_publication_evidence",
    "snapshot_dependency_checks",
    "ingestion_resource_locks",
    "ingestion_lock_recovery_events",
    "forward_eps_observations",
    "pe_scenarios",
    "valuation_idempotency_keys",
    "valuation_approvals",
    "technical_anchor_revisions",
    "technical_anchor_approvals",
    "technical_anchor_idempotency_keys",
    "deployment_plan_revisions",
    "deployment_plan_approvals",
    "deployment_plan_idempotency_keys",
    "screening_idempotency_keys",
    "synthesis_profile_revisions",
    "synthesis_profile_approvals",
    "synthesis_idempotency_keys",
    "screening_profile_revisions",
    "screening_profile_approvals",
    "security_valuation_observations",
    "market_turnover_daily",
    "cbc_m1b_monthly",
    "evaluation_profile_revisions",
    "evaluation_profile_approvals",
    "outcome_resource_manifests",
    "evaluation_runs",
    "scenario_evaluations",
    "evaluation_idempotency_keys",
    "evaluation_run_snapshots",
    "research_watchlist_items",
    "research_review_events",
    "universe_instruments",
    "universe_instrument_revisions",
    "universe_revisions",
    "universe_resource_policies",
    "universe_lifecycle_events",
    "universe_operational_state_events",
    "universe_identity_alias_events",
    "universe_ingestion_idempotency",
    "eod_price_resource_policies",
    "eod_close_source_snapshots",
    "eod_close_observations",
    "eod_ingestion_idempotency",
    "eod_ingestion_command_reservations",
    "eod_product_classification_evidence",
)


@dataclass(frozen=True)
class DatabaseClassification:
    state: DatabaseState
    path: Path
    reason: str
    file_sha256: str | None
    integrity_check: str | None
    applied_migration_ids: tuple[str, ...] = ()
    pending_migration_ids: tuple[str, ...] = ()
    migration_checksums: tuple[tuple[str, str], ...] = ()
    table_names: tuple[str, ...] = ()
    missing_required_tables: tuple[str, ...] = ()

    @property
    def is_ready_for_reads(self) -> bool:
        return self.state is DatabaseState.KNOWN_V2_CURRENT

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "path": str(self.path),
            "reason": self.reason,
            "file_sha256": self.file_sha256,
            "integrity_check": self.integrity_check,
            "applied_migration_ids": list(self.applied_migration_ids),
            "pending_migration_ids": list(self.pending_migration_ids),
            "migration_checksums": dict(self.migration_checksums),
            "table_names": list(self.table_names),
            "missing_required_tables": list(self.missing_required_tables),
        }


def _read_only_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def _expected_checksums(resource_root: str | Path | None) -> dict[str, str]:
    return {
        record["version_id"]: record["checksum_sha256"]
        for record in migration_manifest(resource_root)
    }


def _markers(
    conn: sqlite3.Connection,
    table: str,
) -> tuple[dict[str, str], str | None]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return {}, None
    rows = conn.execute(
        f"SELECT version_id, checksum_sha256 FROM {table} ORDER BY version_id"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}, table


def classify_database(
    db_path: str | Path,
    *,
    resource_root: str | Path | None = None,
    required_tables: Iterable[str] = V2_REQUIRED_TABLES,
) -> DatabaseClassification:
    """Classify without creating tables, migrating, or mutating the file."""

    path = Path(db_path).expanduser().resolve(strict=False)
    if not path.exists():
        return DatabaseClassification(
            state=DatabaseState.FRESH,
            path=path,
            reason="canonical_database_absent",
            file_sha256=None,
            integrity_check=None,
        )
    file_hash: str | None
    try:
        file_hash = sha256_file(path)
    except OSError:
        file_hash = None
    try:
        conn = sqlite3.connect(_read_only_uri(path), uri=True, timeout=2)
    except sqlite3.Error as exc:
        return DatabaseClassification(
            state=DatabaseState.CORRUPT_UNKNOWN,
            path=path,
            reason="database_open_failed",
            file_sha256=file_hash,
            integrity_check=None,
        )
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            return DatabaseClassification(
                state=DatabaseState.CORRUPT_UNKNOWN,
                path=path,
                reason="integrity_check_failed",
                file_sha256=file_hash,
                integrity_check=integrity,
            )
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return DatabaseClassification(
                state=DatabaseState.CORRUPT_UNKNOWN,
                path=path,
                reason="foreign_key_check_failed",
                file_sha256=file_hash,
                integrity_check=integrity,
            )
        tables = tuple(sorted(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ))
        table_set = set(tables)
        schema_markers, schema_table = _markers(conn, "schema_migrations")
        additive_markers, additive_table = _markers(conn, "additive_schema_migrations")
        if schema_table is None:
            return DatabaseClassification(
                state=DatabaseState.LEGACY,
                path=path,
                reason="migration_marker_absent",
                file_sha256=file_hash,
                integrity_check=integrity,
                table_names=tables,
            )

        expected = _expected_checksums(resource_root)
        applied: dict[str, str] = {**schema_markers, **additive_markers}
        if len(applied) != len(schema_markers) + len(additive_markers):
            return DatabaseClassification(
                state=DatabaseState.CORRUPT_UNKNOWN,
                path=path,
                reason="duplicate_migration_marker_id",
                file_sha256=file_hash,
                integrity_check=integrity,
                table_names=tables,
            )
        unknown = sorted(set(applied).difference(expected))
        if unknown:
            return DatabaseClassification(
                state=DatabaseState.CORRUPT_UNKNOWN,
                path=path,
                reason="unknown_migration_marker:" + ",".join(unknown),
                file_sha256=file_hash,
                integrity_check=integrity,
                applied_migration_ids=tuple(sorted(applied)),
                table_names=tables,
            )
        mismatches = sorted(
            migration_id
            for migration_id, checksum in applied.items()
            if expected.get(migration_id) != checksum
        )
        if mismatches:
            return DatabaseClassification(
                state=DatabaseState.CORRUPT_UNKNOWN,
                path=path,
                reason="migration_checksum_mismatch:" + ",".join(mismatches),
                file_sha256=file_hash,
                integrity_check=integrity,
                applied_migration_ids=tuple(sorted(applied)),
                migration_checksums=tuple(sorted(applied.items())),
                table_names=tables,
            )
        missing = tuple(sorted(set(required_tables).difference(table_set)))
        pending = tuple(sorted(set(expected).difference(applied)))
        if not applied:
            state = DatabaseState.CORRUPT_UNKNOWN
            reason = "migration_marker_empty"
        elif missing and not pending:
            state = DatabaseState.CORRUPT_UNKNOWN
            reason = "required_v2_tables_missing"
        elif pending:
            state = DatabaseState.KNOWN_V2_UPGRADEABLE
            reason = "approved_migrations_pending"
        elif missing:
            state = DatabaseState.CORRUPT_UNKNOWN
            reason = "required_v2_tables_missing"
        else:
            state = DatabaseState.KNOWN_V2_CURRENT
            reason = "all_approved_migrations_and_tables_current"
        return DatabaseClassification(
            state=state,
            path=path,
            reason=reason,
            file_sha256=file_hash,
            integrity_check=integrity,
            applied_migration_ids=tuple(sorted(applied)),
            pending_migration_ids=pending,
            migration_checksums=tuple(sorted(applied.items())),
            table_names=tables,
            missing_required_tables=missing,
        )
    except sqlite3.Error:
        return DatabaseClassification(
            state=DatabaseState.CORRUPT_UNKNOWN,
            path=path,
            reason="database_metadata_read_failed",
            file_sha256=file_hash,
            integrity_check=None,
        )
    finally:
        conn.close()


__all__ = [
    "DatabaseClassification",
    "DatabaseState",
    "V2_REQUIRED_TABLES",
    "classify_database",
]
