"""Append-only evidence bundles. Reading never creates or migrates a database."""
from contextlib import closing
from datetime import datetime
import json
from uuid import uuid4

from src.collectors.wave_candidate_sources import VERSION, plan
from src.domain.analysis_snapshot import canonical_json
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.wave_session_repository import connection, digest

MAX_PACKAGE_BYTES = 6 * 1024 * 1024
MAX_STORAGE_BYTES = 64 * 1024 * 1024


def validate(payload):
    if payload.get("version") != VERSION or payload.get("status") not in {"accepted", "failed", "revoked"}:
        raise ValueError("wave_candidate_package_invalid")
    expected = plan(payload["symbol"], **payload["range"])
    sources = payload["sources"]
    if not isinstance(sources, list) or [s["spec"] for s in sources] != expected:
        raise ValueError("wave_candidate_sources_incomplete")
    for source in sources:
        raw = source["raw_text"]
        if len(raw.encode("utf-8")) > 2*1024*1024 or digest(raw) != source["raw_sha256"]:
            raise ValueError("wave_candidate_source_integrity_error")
        fetched = normalize_utc_timestamp(source["fetched_at"], "fetched_at")
        if fetched != source["fetched_at"] or fetched > utc_now_timestamp():
            raise ValueError("wave_candidate_source_time_invalid")
        if source["status"] not in {"accepted", "failed"}:
            raise ValueError("wave_candidate_source_status_invalid")


def decode(row):
    raw = row["payload_json"]
    if len(raw.encode("utf-8")) > MAX_PACKAGE_BYTES or digest(raw) != row["content_sha256"]:
        raise ValueError("wave_candidate_integrity_error")
    payload = json.loads(raw)
    validate(payload)
    if payload["symbol"] != row["symbol"] or any(s["fetched_at"] > row["recorded_at"] for s in payload["sources"]):
        raise ValueError("wave_candidate_binding_error")
    return dict(payload, package_ref=row["package_id"], content_sha256=row["content_sha256"],
                known_at=row["recorded_at"], previous_id=row["previous_id"])


class WaveCandidateRepository:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    @staticmethod
    def ready(conn):
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wave_candidate_packages'").fetchone())

    @staticmethod
    def latest(conn, symbol):
        if not WaveCandidateRepository.ready(conn):
            return None
        row = conn.execute("SELECT * FROM wave_candidate_packages WHERE symbol=? ORDER BY sequence DESC LIMIT 1", (symbol,)).fetchone()
        return decode(row) if row else None

    def by_request(self, key):
        with closing(connection(self.db_path)) as conn:
            if not self.ready(conn):
                return None
            row = conn.execute("SELECT * FROM wave_candidate_packages WHERE request_key=?", (key,)).fetchone()
            return decode(row) if row else None

    def append(self, payload, key):
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError("wave_candidate_request_key_required")
        validate(payload)
        raw = canonical_json(payload)
        size = len(raw.encode("utf-8"))+4096
        if size > MAX_PACKAGE_BYTES:
            raise ValueError("wave_candidate_package_too_large")
        with closing(connection(self.db_path, write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM wave_candidate_packages WHERE request_key=?", (key,)).fetchone()
            if row:
                if row["content_sha256"] != digest(raw):
                    raise ValueError("wave_candidate_idempotency_conflict")
                return decode(row)
            used = conn.execute("SELECT COALESCE(SUM(payload_bytes),0) FROM wave_candidate_packages").fetchone()[0]
            if used+size > MAX_STORAGE_BYTES:
                raise ValueError("wave_candidate_storage_full")
            previous = self.latest(conn, payload["symbol"])
            identifier = "wp_"+uuid4().hex
            conn.execute("INSERT INTO wave_candidate_packages (package_id,symbol,request_key,recorded_at,previous_id,content_sha256,payload_json,payload_bytes) VALUES (?,?,?,?,?,?,?,?)",
                (identifier, payload["symbol"], key, utc_now_timestamp(), previous["package_ref"] if previous else None, digest(raw), raw, size))
            return decode(conn.execute("SELECT * FROM wave_candidate_packages WHERE package_id=?", (identifier,)).fetchone())
