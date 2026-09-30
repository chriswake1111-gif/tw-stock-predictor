"""Append-only bounded evidence area, separate from financial and approval tables."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.collectors.wave_session_sources import MAX_BODY, parse_source, request_spec
from src.domain.analysis_snapshot import canonical_json
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp

MAX_STORAGE_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 3 * 1024 * 1024


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_observation(normalized, fetched):
    observed_day = datetime.fromisoformat(fetched.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
    if normalized and any(f["kind"] in {"stock_traded", "market_open"} and f["date"] > observed_day for f in normalized["facts"]):
        raise ValueError("wave_session_future_trade_fact")


def connection(path, *, write=False):
    # mode=rw never creates a missing DB; no constructor/read performs migrations.
    conn = sqlite3.connect(Path(path).resolve().as_uri()+("?mode=rw" if write else "?mode=ro"), uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if not write:
        conn.execute("PRAGMA query_only=ON")
    return conn


def decode(row):
    text = row["payload_json"]
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES or digest(text) != row["content_sha256"]:
        raise ValueError("wave_session_integrity_error")
    payload = json.loads(text)
    spec = payload["spec"]
    if spec != request_spec(row["source_id"], row["symbol"], row["scope_start"], row["scope_end"]):
        raise ValueError("wave_session_binding_error")
    if payload["fetched_at"] != row["fetched_at"] or payload["status"] not in {"accepted", "partial", "failed", "revoked"}:
        raise ValueError("wave_session_metadata_error")
    if digest(payload["raw_text"]) != payload["raw_sha256"]:
        raise ValueError("wave_session_raw_integrity_error")
    if payload["status"] in {"accepted", "partial"}:
        if parse_source(spec, json.loads(payload["raw_text"])) != payload["normalized"]:
            raise ValueError("wave_session_normalized_integrity_error")
    elif payload["normalized"] is not None:
        raise ValueError("wave_session_failed_record_has_facts")
    validate_observation(payload["normalized"], payload["fetched_at"])
    return dict(payload, reference=row["record_id"], content_sha256=row["content_sha256"], previous_id=row["previous_id"])


class WaveSessionRepository:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def by_request(self, key):
        with closing(connection(self.db_path)) as conn:
            row = conn.execute("SELECT * FROM wave_session_evidence WHERE request_key=?", (key,)).fetchone()
            return decode(row) if row else None

    def append(self, spec, *, key, fetched_at, raw_text="", status="accepted", reason="", previous_id=None,
               response_sha256=None, http_status=None):
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError("wave_session_request_key_required")
        if spec != request_spec(spec["source_id"], spec["symbol"], spec["start"], spec["end"]):
            raise ValueError("wave_session_source_not_registered")
        fetched = normalize_utc_timestamp(fetched_at, "fetched_at")
        if fetched > utc_now_timestamp():
            raise ValueError("wave_session_future_observation")
        if len(raw_text.encode("utf-8")) > MAX_BODY or len(reason) > 200 or status not in {"accepted", "partial", "failed", "revoked"}:
            raise ValueError("wave_session_payload_invalid")
        if status in {"failed", "revoked"} and not reason:
            raise ValueError("wave_session_failure_reason_required")
        normalized = parse_source(spec, json.loads(raw_text)) if status in {"accepted", "partial"} else None
        validate_observation(normalized, fetched)
        if response_sha256 is not None and (not isinstance(response_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", response_sha256)):
            raise ValueError("wave_session_response_hash_invalid")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("wave_session_http_status_invalid")
        payload = dict(spec=spec, fetched_at=fetched, status=status, reason=reason,
                       raw_text=raw_text, raw_sha256=digest(raw_text), normalized=normalized)
        if response_sha256 is not None or http_status is not None:
            payload.update(response_sha256=response_sha256, http_status=http_status)
        text = canonical_json(payload)
        size = len(text.encode("utf-8"))+4096
        if size > MAX_RECORD_BYTES:
            raise ValueError("wave_session_record_too_large")
        with closing(connection(self.db_path, write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM wave_session_evidence WHERE request_key=?", (key,)).fetchone()
            if existing:
                if existing["content_sha256"] != digest(text):
                    raise ValueError("wave_session_idempotency_conflict")
                return decode(existing)
            last = conn.execute("SELECT * FROM wave_session_evidence WHERE symbol=? AND source_id=? AND scope_start=? AND scope_end=? ORDER BY sequence DESC LIMIT 1",
                (spec["symbol"], spec["source_id"], spec["start"], spec["end"])).fetchone()
            if previous_id is not None and (not last or last["record_id"] != previous_id):
                raise ValueError("wave_session_revision_changed")
            if last and fetched < last["fetched_at"]:
                raise ValueError("wave_session_observation_out_of_order")
            used = conn.execute("SELECT COALESCE(SUM(payload_bytes),0) FROM wave_session_evidence").fetchone()[0]
            if used+size > MAX_STORAGE_BYTES:
                raise ValueError("wave_session_storage_full")
            record_id = "ws_"+uuid4().hex
            conn.execute("""INSERT INTO wave_session_evidence
                (record_id,symbol,source_id,scope_start,scope_end,request_key,fetched_at,previous_id,content_sha256,payload_json,payload_bytes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (record_id, spec["symbol"], spec["source_id"], spec["start"], spec["end"],
                    key, fetched, last["record_id"] if last else None, digest(text), text, size))
            return decode(conn.execute("SELECT * FROM wave_session_evidence WHERE record_id=?", (record_id,)).fetchone())

    @staticmethod
    def read(conn, symbol, start, end, cutoff):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wave_session_evidence'").fetchone():
            return []
        rows = conn.execute("""WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY source_id,scope_start,scope_end ORDER BY sequence DESC) AS rank_no
            FROM wave_session_evidence WHERE symbol=? AND scope_start<=? AND scope_end>=? AND fetched_at<=?)
            SELECT * FROM ranked WHERE rank_no=1 ORDER BY sequence DESC LIMIT 481""", (symbol, end, start, cutoff)).fetchall()
        # Bound version history too; do not silently truncate important revisions.
        if len(rows) > 480:
            raise ValueError("wave_session_read_limit")
        return [decode(row) for row in rows]
