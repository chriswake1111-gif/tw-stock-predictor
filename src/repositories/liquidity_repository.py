"""Immutable SQLite persistence and as-of queries for liquidity observations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from src.domain.liquidity import M1BMonthlyObservation, MarketTurnoverObservation
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


def _fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LiquidityRepository:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_m1b(
        self, observation: M1BMonthlyObservation, ingested_at: str | None = None
    ) -> dict[str, Any]:
        payload = observation.canonical_payload()
        record_id = f"m1b_{_fingerprint(payload)[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cbc_m1b_monthly (
                    id,period,value_raw,raw_unit,value_twd,data_date,available_at,
                    fetched_at,ingested_at,source,source_dataset,source_url,payload_hash,
                    revision,status,quality_note
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (record_id, payload["period"], payload["value_raw"], payload["raw_unit"],
                 payload["value_twd"], payload["data_date"], payload["available_at"],
                 payload["fetched_at"], ingested, payload["source"],
                 payload["source_dataset"], payload["source_url"], payload["payload_hash"],
                 payload["revision"], payload["status"], payload["quality_note"]),
            )
            row = conn.execute("SELECT * FROM cbc_m1b_monthly WHERE id = ?", (record_id,)).fetchone()
        return dict(row)

    def add_turnover(
        self, observation: MarketTurnoverObservation, ingested_at: str | None = None
    ) -> dict[str, Any]:
        payload = observation.canonical_payload()
        record_id = f"turnover_{_fingerprint(payload)[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_turnover_daily (
                    id,trade_date,twse_turnover_twd,tpex_turnover_twd,total_turnover_twd,
                    twse_source,tpex_source,twse_dataset,tpex_dataset,twse_payload_hash,
                    tpex_payload_hash,available_at,fetched_at,ingested_at,revision,status,
                    quality_note
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (record_id, payload["trade_date"], payload["twse_turnover_twd"],
                 payload["tpex_turnover_twd"], payload["total_turnover_twd"],
                 payload["twse_source"], payload["tpex_source"], payload["twse_dataset"],
                 payload["tpex_dataset"], payload["twse_payload_hash"],
                 payload["tpex_payload_hash"], payload["available_at"],
                 payload["fetched_at"], ingested, payload["revision"], payload["status"],
                 payload["quality_note"]),
            )
            row = conn.execute("SELECT * FROM market_turnover_daily WHERE id = ?", (record_id,)).fetchone()
        return dict(row)

    def latest_m1b_as_of(self, knowledge_cutoff_at: str) -> dict | None:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            row = conn.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY period, source_dataset
                        ORDER BY revision DESC, available_at DESC, ingested_at DESC, id DESC
                    ) AS rank_no
                    FROM cbc_m1b_monthly
                    WHERE available_at <= ? AND ingested_at <= ?
                )
                SELECT * FROM ranked
                WHERE rank_no = 1 AND status = 'available'
                ORDER BY available_at DESC, period DESC, revision DESC, ingested_at DESC
                LIMIT 1
                """, (cutoff, cutoff),
            ).fetchone()
        return dict(row) if row else None

    def turnover_as_of(self, knowledge_cutoff_at: str) -> list[dict]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY trade_date
                        ORDER BY revision DESC, available_at DESC, ingested_at DESC, id DESC
                    ) AS rank_no
                    FROM market_turnover_daily
                    WHERE available_at <= ? AND ingested_at <= ?
                )
                SELECT * FROM ranked WHERE rank_no = 1 ORDER BY trade_date
                """, (cutoff, cutoff),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_turnover_revision(self, trade_date: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_turnover_daily
                WHERE trade_date = ?
                ORDER BY revision DESC, available_at DESC, ingested_at DESC, id DESC
                LIMIT 1
                """,
                (trade_date,),
            ).fetchone()
        return dict(row) if row else None

    def latest_m1b_revision(self, period: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM cbc_m1b_monthly
                WHERE period = ? AND source_dataset = 'CBC EF15M01'
                ORDER BY revision DESC, available_at DESC, ingested_at DESC, id DESC
                LIMIT 1
                """,
                (period,),
            ).fetchone()
        return dict(row) if row else None

    def m1b_for_turnover(self, turnover: dict, knowledge_cutoff_at: str) -> dict | None:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        public_at = min(turnover["available_at"], cutoff)
        with self._connect() as conn:
            row = conn.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY period, source_dataset
                        ORDER BY revision DESC, available_at DESC, ingested_at DESC, id DESC
                    ) AS rank_no
                    FROM cbc_m1b_monthly
                    WHERE available_at <= ? AND ingested_at <= ?
                )
                SELECT * FROM ranked
                WHERE rank_no = 1 AND status = 'available'
                ORDER BY available_at DESC, period DESC, revision DESC, ingested_at DESC
                LIMIT 1
                """, (public_at, cutoff),
            ).fetchone()
        return dict(row) if row else None
