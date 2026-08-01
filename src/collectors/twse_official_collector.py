"""Official TWSE integrity data collector for historical research snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FMTQIK_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"
STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
EXRIGHT_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
REDUCTION_URL = "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU"


def _number(value: Any, *, integer: bool = False) -> Optional[float | int]:
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "--", "---", "N/A", "nan", "None"}:
        return None
    parsed = float(text)
    return int(parsed) if integer else parsed


def roc_date_to_iso(value: str) -> str:
    text = str(value).strip().replace("年", "/").replace("月", "/").replace("日", "")
    year, month, day = (int(part) for part in text.split("/"))
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def _require_ok(payload: dict) -> None:
    if payload.get("stat") != "OK":
        raise ValueError(f"TWSE response is not OK: {payload.get('stat')}")


def parse_twse_market_month(payload: dict) -> list[dict]:
    _require_ok(payload)
    return [
        {
            "trade_date": roc_date_to_iso(row[0]),
            "traded_shares": _number(row[1], integer=True),
            "traded_value_twd": _number(row[2], integer=True),
            "transaction_count": _number(row[3], integer=True),
            "taiex": _number(row[4]),
            "taiex_change": _number(row[5]),
        }
        for row in payload.get("data", [])
    ]


def parse_twse_stock_day(payload: dict, symbol: Optional[str] = None) -> list[dict]:
    _require_ok(payload)
    rows = []
    for raw in payload.get("data", []):
        rows.append({
            "symbol": symbol,
            "trade_date": roc_date_to_iso(raw[0]),
            "volume": _number(raw[1], integer=True),
            "traded_value_twd": _number(raw[2], integer=True),
            "open": _number(raw[3]),
            "high": _number(raw[4]),
            "low": _number(raw[5]),
            "close": _number(raw[6]),
            "change_text": str(raw[7]).strip(),
            "transaction_count": _number(raw[8], integer=True),
            "note": str(raw[9]).strip() if len(raw) > 9 else "",
        })
    return rows


def parse_twse_exright(payload: dict) -> list[dict]:
    _require_ok(payload)
    rows = []
    for raw in payload.get("data", []):
        event_label = str(raw[6]).strip()
        event_type = {
            "權": "ex_right",
            "息": "ex_dividend",
            "權息": "ex_right_dividend",
        }.get(event_label, "other_exright")
        rows.append({
            "symbol": str(raw[1]).strip(),
            "event_date": roc_date_to_iso(raw[0]),
            "event_type": event_type,
            "previous_close": _number(raw[3]),
            "reference_price": _number(raw[4]),
            "opening_reference": _number(raw[9]),
            "cash_dividend": None,
            "action_value": _number(raw[5]),
            "action_reason": event_label,
            "raw_row": raw,
        })
    return rows


def parse_twse_reduction(payload: dict) -> list[dict]:
    _require_ok(payload)
    return [
        {
            "symbol": str(raw[1]).strip(),
            "event_date": roc_date_to_iso(raw[0]),
            "event_type": "capital_reduction",
            "previous_close": _number(raw[3]),
            "reference_price": _number(raw[4]),
            "opening_reference": _number(raw[7]),
            "cash_dividend": None,
            "action_value": None,
            "action_reason": str(raw[9]).strip(),
            "raw_row": raw,
        }
        for raw in payload.get("data", [])
    ]


class TWSEOfficialCollector:
    """Persist official raw facts in parallel tables without mutating daily_ohlcv."""

    def __init__(
        self,
        db_path: str = "data/cache.db",
        session: Optional[requests.Session] = None,
        initialize_db: bool = True,
        timeout: int = 20,
    ):
        self.db_path = db_path
        self.timeout = timeout
        self.session = session or self._build_session()
        if initialize_db:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            self._init_db()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update({
            "User-Agent": "tw-stock-predictor-research-audit/1.0",
            "Accept": "application/json",
        })
        return session

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS twse_market_calendar (
                    trade_date TEXT PRIMARY KEY,
                    traded_shares INTEGER,
                    traded_value_twd INTEGER,
                    transaction_count INTEGER,
                    taiex REAL,
                    taiex_change REAL,
                    source_dataset TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS twse_daily_raw (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    traded_value_twd INTEGER,
                    transaction_count INTEGER,
                    change_text TEXT,
                    note TEXT,
                    source_dataset TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    PRIMARY KEY (symbol, trade_date)
                );

                CREATE TABLE IF NOT EXISTS twse_corporate_actions (
                    symbol TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    previous_close REAL,
                    reference_price REAL,
                    opening_reference REAL,
                    cash_dividend REAL,
                    action_value REAL,
                    action_reason TEXT,
                    raw_payload_json TEXT NOT NULL,
                    source_dataset TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    PRIMARY KEY (symbol, event_date, event_type, source_dataset)
                );

                CREATE TABLE IF NOT EXISTS twse_data_audit_runs (
                    audit_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    snapshot_dir TEXT NOT NULL,
                    requested_start TEXT NOT NULL,
                    requested_end TEXT NOT NULL,
                    symbols_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    completed_request_keys_json TEXT NOT NULL,
                    summary_json TEXT,
                    error_text TEXT
                );
            """)

    def _fetch_json(self, url: str, params: dict) -> tuple[dict, str, str]:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        _require_ok(payload)
        final_url = getattr(response, "url", None) or url
        raw_content = getattr(response, "content", None)
        if raw_content is None:
            raw_content = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        return payload, final_url, hashlib.sha256(raw_content).hexdigest()

    @staticmethod
    def _fetched_at() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def fetch_market_month(self, month: str) -> int:
        query_date = f"{month.replace('-', '')}01"
        payload, source_url, payload_hash = self._fetch_json(
            FMTQIK_URL, {"response": "json", "date": query_date}
        )
        rows = parse_twse_market_month(payload)
        fetched_at = self._fetched_at()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO twse_market_calendar (
                    trade_date, traded_shares, traded_value_twd, transaction_count,
                    taiex, taiex_change, source_dataset, source_url, fetched_at,
                    payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, 'FMTQIK', ?, ?, ?)
            """, [
                (
                    row["trade_date"], row["traded_shares"], row["traded_value_twd"],
                    row["transaction_count"], row["taiex"], row["taiex_change"],
                    source_url, fetched_at, payload_hash,
                )
                for row in rows
            ])
        return len(rows)

    def fetch_stock_month(self, symbol: str, month: str) -> int:
        stock_no = symbol.split(".")[0]
        query_date = f"{month.replace('-', '')}01"
        payload, source_url, payload_hash = self._fetch_json(
            STOCK_DAY_URL,
            {"response": "json", "date": query_date, "stockNo": stock_no},
        )
        rows = parse_twse_stock_day(payload, symbol=symbol)
        fetched_at = self._fetched_at()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO twse_daily_raw (
                    symbol, trade_date, open, high, low, close, volume,
                    traded_value_twd, transaction_count, change_text, note,
                    source_dataset, source_url, fetched_at, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'STOCK_DAY', ?, ?, ?)
            """, [
                (
                    symbol, row["trade_date"], row["open"], row["high"], row["low"],
                    row["close"], row["volume"], row["traded_value_twd"],
                    row["transaction_count"], row["change_text"], row["note"],
                    source_url, fetched_at, payload_hash,
                )
                for row in rows
            ])
        return len(rows)

    def _store_actions(
        self,
        rows: list[dict],
        source_dataset: str,
        source_url: str,
        payload_hash: str,
    ) -> int:
        fetched_at = self._fetched_at()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO twse_corporate_actions (
                    symbol, event_date, event_type, previous_close, reference_price,
                    opening_reference, cash_dividend, action_value, action_reason,
                    raw_payload_json, source_dataset, source_url, fetched_at,
                    payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    row["symbol"], row["event_date"], row["event_type"],
                    row["previous_close"], row["reference_price"],
                    row["opening_reference"], row["cash_dividend"],
                    row["action_value"], row["action_reason"],
                    json.dumps(row["raw_row"], ensure_ascii=False), source_dataset,
                    source_url, fetched_at, payload_hash,
                )
                for row in rows
            ])
        return len(rows)

    def fetch_exright_year(self, year: int) -> int:
        payload, source_url, payload_hash = self._fetch_json(EXRIGHT_URL, {
            "response": "json",
            "startDate": f"{year}0101",
            "endDate": f"{year}1231",
        })
        return self._store_actions(
            parse_twse_exright(payload), "TWT49U", source_url, payload_hash
        )

    def fetch_reduction_year(self, year: int) -> int:
        payload, source_url, payload_hash = self._fetch_json(REDUCTION_URL, {
            "response": "json",
            "startDate": f"{year}0101",
            "endDate": f"{year}1231",
        })
        return self._store_actions(
            parse_twse_reduction(payload), "TWTAUU", source_url, payload_hash
        )

    def market_dates(self, start_date: str, end_date: str) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [row[0] for row in conn.execute(
                "SELECT trade_date FROM twse_market_calendar "
                "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
                (start_date, end_date),
            )]

    def raw_dates(self, symbol: str, dates: Iterable[str]) -> dict[str, dict]:
        values = sorted(set(dates))
        if not values:
            return {}
        placeholders = ",".join("?" for _ in values)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM twse_daily_raw WHERE symbol = ? "
                f"AND trade_date IN ({placeholders})",
                [symbol, *values],
            ).fetchall()
        return {row["trade_date"]: dict(row) for row in rows}

    def actions_for_symbols(
        self, symbols: Iterable[str], start_date: str, end_date: str
    ) -> list[dict]:
        stock_codes = sorted({symbol.split(".")[0] for symbol in symbols})
        if not stock_codes:
            return []
        placeholders = ",".join("?" for _ in stock_codes)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM twse_corporate_actions WHERE symbol IN ({placeholders}) "
                "AND event_date >= ? AND event_date <= ? ORDER BY event_date, symbol",
                [*stock_codes, start_date, end_date],
            ).fetchall()
        return [dict(row) for row in rows]

    def start_or_resume_audit(
        self,
        audit_id: str,
        snapshot_dir: str,
        start_date: str,
        end_date: str,
        symbols: list[str],
        plan: dict,
    ) -> set[str]:
        now = self._fetched_at()
        expected_symbols = json.dumps(symbols, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR IGNORE INTO twse_data_audit_runs (
                    audit_id, started_at, updated_at, status, dry_run, snapshot_dir,
                    requested_start, requested_end, symbols_json, plan_json,
                    completed_request_keys_json
                ) VALUES (?, ?, ?, 'in_progress', 0, ?, ?, ?, ?, ?, '[]')
            """, (
                audit_id, now, now, snapshot_dir, start_date, end_date,
                expected_symbols,
                json.dumps(plan, ensure_ascii=False),
            ))
            row = conn.execute(
                "SELECT snapshot_dir, requested_start, requested_end, symbols_json, "
                "completed_request_keys_json FROM twse_data_audit_runs "
                "WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        actual_scope = row[:4]
        expected_scope = (snapshot_dir, start_date, end_date, expected_symbols)
        if actual_scope != expected_scope:
            raise ValueError(
                f"audit_id {audit_id!r} already exists with a different scope"
            )
        return set(json.loads(row[4]))

    def checkpoint_audit(
        self,
        audit_id: str,
        completed_keys: set[str],
        plan: Optional[dict] = None,
        error_text: Optional[str] = None,
    ) -> None:
        assignments = [
            "updated_at = ?", "status = ?", "completed_request_keys_json = ?",
            "error_text = ?",
        ]
        params: list[Any] = [
            self._fetched_at(), "failed" if error_text else "in_progress",
            json.dumps(sorted(completed_keys), ensure_ascii=False), error_text,
        ]
        if plan is not None:
            assignments.append("plan_json = ?")
            params.append(json.dumps(plan, ensure_ascii=False))
        params.append(audit_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE twse_data_audit_runs SET {', '.join(assignments)} "
                "WHERE audit_id = ?",
                params,
            )

    def finish_audit(self, audit_id: str, completed_keys: set[str], summary: dict) -> None:
        now = self._fetched_at()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE twse_data_audit_runs
                SET updated_at = ?, completed_at = ?, status = 'completed',
                    completed_request_keys_json = ?, summary_json = ?, error_text = NULL
                WHERE audit_id = ?
            """, (
                now, now, json.dumps(sorted(completed_keys), ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False), audit_id,
            ))
