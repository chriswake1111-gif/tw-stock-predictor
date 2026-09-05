"""Current research repository for latest-settled market and symbol context."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from src.domain.universe import parse_canonical_symbol, validate_knowledge_cutoff_at
from src.domain.valuation import utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


TAIPEI_TZ = timezone(timedelta(hours=8))
TWSE_EOD_RESOURCE_ID = "twse.eod.stock_day_all"
TPEX_EOD_RESOURCE_ID = "tpex.eod.daily_close_quotes"


class CurrentResearchRepository:
    """Repository executing the two-stage settled context CTE and fail-closed anti-fallback."""

    def __init__(self, db_path: str = "data/cache.db", *, auto_migrate: bool = False):
        self.db_path = db_path
        if auto_migrate:
            apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def resolve_latest_settled_context(
        self,
        canonical_symbol: str,
        *,
        cutoff: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Resolve latest-settled context for canonical_symbol at cutoff.

        Uses Two-Stage Positive-Proof CTE with Same-Date Revision Selection (BC-IP-1)
        and Fail-Closed Anti-Fallback Rule (P1-1).
        """
        cut = validate_knowledge_cutoff_at(cutoff) if cutoff else validate_knowledge_cutoff_at(utc_now_timestamp())
        venue, official_code = parse_canonical_symbol(canonical_symbol)
        venue_str = venue.value
        resource_id = TWSE_EOD_RESOURCE_ID if venue_str == "TWSE" else TPEX_EOD_RESOURCE_ID

        if conn is not None:
            return self._execute_resolve(conn, canonical_symbol, venue_str, official_code, resource_id, cut)
        with self._connect() as read_conn:
            return self._execute_resolve(read_conn, canonical_symbol, venue_str, official_code, resource_id, cut)

    @classmethod
    def _execute_resolve(
        cls,
        conn: sqlite3.Connection,
        canonical_symbol: str,
        venue: str,
        official_code: str,
        resource_id: str,
        cutoff: str,
    ) -> dict[str, Any]:
        query = """
        WITH candidate_snapshots AS (
            SELECT
                s.source_snapshot_id,
                s.resource_id,
                s.source_trade_date,
                s.revision_number,
                s.status AS snapshot_status,
                s.available_at AS snapshot_available_at,
                s.ingested_at AS snapshot_ingested_at,
                ROW_NUMBER() OVER (
                    PARTITION BY s.source_trade_date
                    ORDER BY s.revision_number DESC, s.ingested_at DESC, s.source_snapshot_id DESC
                ) AS date_rev_rank
            FROM eod_close_source_snapshots s
            WHERE s.resource_id = :resource_id
              AND s.source_trade_date_status = 'valid'
              AND s.status IN ('available', 'partial')
              AND s.available_at IS NOT NULL AND s.available_at <= :cutoff
              AND s.ingested_at IS NOT NULL AND s.ingested_at <= :cutoff
        ),
        latest_settled_snapshot AS (
            SELECT
                source_snapshot_id,
                resource_id,
                source_trade_date,
                snapshot_status,
                snapshot_available_at,
                snapshot_ingested_at
            FROM candidate_snapshots
            WHERE date_rev_rank = 1
            ORDER BY source_trade_date DESC
            LIMIT 1
        )
        SELECT
            ls.source_trade_date AS settled_trade_date,
            ls.source_snapshot_id,
            ls.snapshot_status,
            o.close_observation_id,
            o.close_value,
            o.currency,
            o.unit,
            o.observation_status,
            o.public_eligibility_status,
            o.available_at AS eod_available_at,
            o.ingested_at AS eod_ingested_at
        FROM latest_settled_snapshot ls
        LEFT JOIN eod_close_observations o
          ON o.source_snapshot_id = ls.source_snapshot_id
         AND o.official_code = :official_code
         AND o.trade_date = ls.source_trade_date
         AND o.public_eligibility_status = 'eligible'
         AND o.observation_status = 'available'
         AND o.available_at <= :cutoff AND o.ingested_at <= :cutoff
        """
        row = conn.execute(
            query,
            {
                "resource_id": resource_id,
                "official_code": official_code,
                "cutoff": cutoff,
            },
        ).fetchone()

        # Derive market closure and status label
        try:
            cutoff_dt = datetime.fromisoformat(cutoff.replace("Z", "+00:00")).astimezone(TAIPEI_TZ)
        except Exception:
            cutoff_dt = datetime.now(TAIPEI_TZ)

        today_taipei = cutoff_dt.strftime("%Y-%m-%d")
        is_weekend = cutoff_dt.weekday() in (5, 6)

        if row is None:
            # No settled snapshot at all
            settled_trade_date = None
            official_close = {
                "status": "insufficient_data",
                "value": None,
                "currency": "TWD",
                "unit": "TWD_per_share",
                "observation_id": None,
                "snapshot_id": None,
                "reason": "no_settled_snapshots_available",
            }
            market_turnover_total = None
        else:
            settled_trade_date = str(row["settled_trade_date"])
            close_val = row["close_value"]
            if close_val is not None:
                official_close = {
                    "status": "available",
                    "value": float(close_val),
                    "currency": str(row["currency"] or "TWD"),
                    "unit": str(row["unit"] or "TWD_per_share"),
                    "observation_id": str(row["close_observation_id"]),
                    "snapshot_id": str(row["source_snapshot_id"]),
                    "reason": None,
                }
            else:
                # Fail-closed anti-fallback rule (P1-1): do NOT fall back to D-1
                official_close = {
                    "status": "insufficient_data",
                    "value": None,
                    "currency": "TWD",
                    "unit": "TWD_per_share",
                    "observation_id": None,
                    "snapshot_id": str(row["source_snapshot_id"]),
                    "reason": "symbol_observation_not_yet_materialized_for_settled_session",
                }

            # Query market turnover for settled date D
            to_row = conn.execute(
                """
                SELECT total_turnover_twd
                FROM market_turnover_daily
                WHERE trade_date = ?
                  AND available_at <= ? AND ingested_at <= ?
                ORDER BY revision DESC, available_at DESC, ingested_at DESC, id DESC
                LIMIT 1
                """,
                (settled_trade_date, cutoff, cutoff),
            ).fetchone()
            market_turnover_total = float(to_row["total_turnover_twd"]) if to_row and to_row["total_turnover_twd"] is not None else None

        is_market_closed = False
        if settled_trade_date:
            market_status_label = f"最新已結算行情：{settled_trade_date}"
        else:
            market_status_label = "尚未有結算行情"

        if is_weekend:
            is_market_closed = True
            market_status_label = "休市中（週末非交易日）"
        else:
            cal_row = conn.execute(
                """
                SELECT session_status, note
                FROM trading_calendar_revisions
                WHERE market = 'TW' AND trade_date = ?
                  AND session_status IN ('holiday', 'closed', 'no_trading')
                  AND status = 'available'
                  AND available_at <= ? AND ingested_at <= ?
                ORDER BY revision_number DESC, ingested_at DESC
                LIMIT 1
                """,
                (today_taipei, cutoff, cutoff),
            ).fetchone()
            if cal_row:
                is_market_closed = True
                note = (cal_row["note"] or "").strip() or "官方公告節假日"
                market_status_label = f"休市中（{note}）"

        return {
            "canonical_symbol": canonical_symbol,
            "official_code": official_code,
            "venue": venue,
            "settled_trade_date": settled_trade_date,
            "official_close": official_close,
            "market_context": {
                "settled_trade_date": settled_trade_date,
                "is_market_closed": is_market_closed,
                "market_status_label": market_status_label,
                "market_turnover_total": market_turnover_total,
                "currency": "TWD",
            },
            "knowledge_cutoff_at": cutoff,
        }
