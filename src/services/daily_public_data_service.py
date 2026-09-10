"""Bounded installed collection and read-only views of explicit third-party data."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.collectors.daily_public_data import parse_daily_public_dataset
from src.domain.analysis_snapshot import canonical_json
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import utc_now_timestamp

DATASETS = ("TaiwanStockPrice", "TaiwanStockPER", "TaiwanStockFinancialStatements")
PARSER_VERSION = "daily-public-v1"


class DailyPublicDataService:
    datasets = DATASETS

    def source_metadata(self, dataset):
        return {"source": "FinMind", "official_exchange_source": False}

    def __init__(self, db_path: str):
        self.db_path = db_path

    def request_spec(self, symbol, dataset, today):
        code = parse_canonical_symbol(symbol)[1]
        params = {"dataset": dataset, "data_id": code,
                  "start_date": (today - timedelta(days=730 if dataset == DATASETS[2] else 366)).isoformat(),
                  "end_date": today.isoformat()}
        return f"finmind.{dataset}", "https://api.finmindtrade.com/api/v4/data?" + urlencode(params)

    def parse(self, dataset, payload, symbol, observed):
        return parse_daily_public_dataset(dataset, payload, symbol, observed)

    def refresh(self, symbol, operation_id, client, authorize, deadline):
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        errors = []
        for dataset in self.datasets:
            resource, url = self.request_spec(symbol, dataset, today)
            authorize(resource)
            cutoff = utc_now_timestamp()
            current = self.view(symbol, cutoff).get(dataset, {})
            checked = current.get("last_checked_at")
            if checked and current.get("last_update_status") == "available":
                checked_time = datetime.fromisoformat(checked.replace("Z", "+00:00"))
                if timedelta(0) <= datetime.now(timezone.utc) - checked_time < timedelta(minutes=30):
                    continue
            snapshot_id = None
            reason = None
            try:
                status, raw, _ = client.fetch(url, deadline_monotonic=deadline)
                if status != 200:
                    raise ValueError(f"source_http_{status}")
                observed = utc_now_timestamp()
                payload = json.loads(raw)
                normalized = self.parse(dataset, payload, symbol, observed)
                text = canonical_json(normalized)
                raw_hash = hashlib.sha256(raw).hexdigest()
                authorize(resource)
                with closing(sqlite3.connect(self.db_path)) as conn, conn:
                    existing = conn.execute(
                        "SELECT snapshot_id FROM daily_public_snapshots WHERE symbol=? AND dataset=? AND raw_sha256=? AND parser_version=?",
                        (symbol, dataset, raw_hash, PARSER_VERSION),
                    ).fetchone()
                    snapshot_id = existing[0] if existing else "daily_" + uuid4().hex
                    if not existing:
                        conn.execute("INSERT INTO daily_public_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", (
                            snapshot_id, symbol, dataset, observed, url, raw_hash,
                            raw.decode("utf-8"), text, hashlib.sha256(text.encode()).hexdigest(), PARSER_VERSION,
                        ))
            except (ValueError, sqlite3.Error, RuntimeError) as exc:
                reason = str(exc) if str(exc).startswith("source_http_") else "source_validation_or_storage_failed"
                errors.append(f"{dataset}:{reason}")
            except Exception as exc:
                # Network exceptions remain visible per source; authorization is
                # checked again before any write and must never be bypassed.
                reason = "source_timeout" if "timeout" in type(exc).__name__.lower() else "source_connection_failed"
                errors.append(f"{dataset}:{reason}")
            authorize(resource)
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute("INSERT INTO daily_public_attempts VALUES (?,?,?,?,?,?,?,?)", (
                    "attempt_" + uuid4().hex, operation_id, symbol, dataset, utc_now_timestamp(),
                    "failed" if reason else "available", reason, snapshot_id,
                ))
        return errors

    def view(self, symbol, cutoff):
        result = {}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='daily_public_snapshots'").fetchone():
                return {}
            for dataset in self.datasets:
                attempt = conn.execute(
                    "SELECT * FROM daily_public_attempts WHERE symbol=? AND dataset=? AND checked_at<=? ORDER BY checked_at DESC, attempt_id DESC LIMIT 1",
                    (symbol, dataset, cutoff),
                ).fetchone()
                row = conn.execute(
                    "SELECT * FROM daily_public_snapshots WHERE symbol=? AND dataset=? AND observed_at<=? ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1",
                    (symbol, dataset, cutoff),
                ).fetchone()
                item = {"status": "insufficient_data", "rows": [], "reason": "not_collected",
                        **self.source_metadata(dataset), "dataset": dataset}
                if row:
                    text = row["normalized_json"]
                    if (hashlib.sha256(text.encode()).hexdigest() != row["normalized_sha256"] or
                            hashlib.sha256(row["raw_json"].encode()).hexdigest() != row["raw_sha256"]):
                        item["reason"] = "snapshot_integrity_error"
                    else:
                        item = json.loads(text)
                        item.update(snapshot_id=row["snapshot_id"], raw_sha256=row["raw_sha256"],
                                    parser_version=row["parser_version"], source_url=row["source_url"])
                item.update(last_checked_at=attempt["checked_at"] if attempt else None,
                            last_update_status=attempt["status"] if attempt else "not_started",
                            last_update_reason=attempt["reason"] if attempt else None)
                result[dataset] = item
            return result
        finally:
            conn.close()
