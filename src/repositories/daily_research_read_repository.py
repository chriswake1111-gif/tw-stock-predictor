"""The single, read-only SQLite boundary for the Phase 17 Daily GET path."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class DailyResearchContractUnavailable(RuntimeError):
    code = "daily_research_contract_unavailable"


# These are the durable contracts consumed by the Daily projection.  The
# check deliberately uses SELECT/PRAGMA metadata only; it never repairs a DB.
_REQUIRED_TABLES = (
    "research_watchlist_items",
    "research_review_events",
    "analysis_snapshots",
    "analysis_snapshot_idempotency_keys",
    "data_providers",
    "data_resources",
    "ingestion_runs",
    "ingestion_run_items",
    "raw_resource_revisions",
    "trading_calendar_revisions",
    "resource_publication_evidence",
    "forward_eps_observations",
    "pe_scenarios",
    "valuation_approvals",
    "technical_anchor_revisions",
    "technical_anchor_approvals",
    "deployment_plan_revisions",
    "deployment_plan_approvals",
    "synthesis_profile_revisions",
    "synthesis_profile_approvals",
    "screening_profile_revisions",
    "screening_profile_approvals",
    "security_valuation_observations",
    "market_turnover_daily",
    "cbc_m1b_monthly",
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
    "eod_product_classification_evidence",
)

_REQUIRED_COLUMNS = {
    "research_watchlist_items": {"watchlist_item_id", "symbol", "membership_state"},
    "research_review_events": {
        "review_event_id", "watchlist_item_id", "acknowledged_snapshot_id",
        "comparison_cutoff_at", "reviewed_at", "created_at", "idempotency_key",
    },
    "analysis_snapshots": {
        "snapshot_id", "symbol", "knowledge_cutoff_at", "created_at", "output_json",
        "output_sha256",
    },
}


class DailyResearchReadRepository:
    """Open an already-migrated database without migration or write access."""

    def __init__(self, db_path: str = "data/cache.db") -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            database = self.db_path
            uri = False
        else:
            path = Path(self.db_path).expanduser().resolve()
            if not path.is_file():
                raise DailyResearchContractUnavailable(
                    "daily research database is not available"
                )
            database = f"file:{path.as_posix()}?mode=ro"
            uri = True
        try:
            conn = sqlite3.connect(database, uri=uri, timeout=5)
        except sqlite3.Error as exc:
            raise DailyResearchContractUnavailable(str(exc)) from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            self._verify_schema(conn)
        except Exception:
            conn.close()
            raise
        return conn

    @staticmethod
    def _verify_schema(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = {str(row["name"]) for row in rows}
        missing_tables = sorted(set(_REQUIRED_TABLES).difference(tables))
        if missing_tables:
            raise DailyResearchContractUnavailable(
                "daily_research_required_tables_missing:" + ",".join(missing_tables)
            )
        for table, required in _REQUIRED_COLUMNS.items():
            columns = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM pragma_table_info(?)", (table,)
                ).fetchall()
            }
            missing = sorted(required.difference(columns))
            if missing:
                raise DailyResearchContractUnavailable(
                    f"daily_research_required_columns_missing:{table}:{','.join(missing)}"
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


__all__ = ["DailyResearchContractUnavailable", "DailyResearchReadRepository"]
