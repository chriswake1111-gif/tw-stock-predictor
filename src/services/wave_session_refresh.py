"""Explicit bounded operator refresh. No startup, scheduler or UI read hooks."""
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import time
from zoneinfo import ZoneInfo

from src.collectors.installed_egress_client import InstalledEgressClient
from src.collectors.wave_session_sources import MAX_BODY, plan_requests, parse_source
from src.domain.valuation import utc_now_timestamp
from src.repositories.wave_session_repository import WaveSessionRepository, connection, validate_observation
from src.services.wave_session_coverage import enabled


def refresh(db_path, symbol, start, end, *, request_key, execute=False, force=False, client=None, source_ids=None):
    if not enabled():
        raise ValueError("wave_session_evidence_disabled")
    if not isinstance(request_key, str) or not 8 <= len(request_key) <= 100:
        raise ValueError("wave_session_request_key_required")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    if end > today:
        raise ValueError("wave_session_future_range")
    specs = plan_requests(symbol, start, end)
    if source_ids is not None:
        if not source_ids or not set(source_ids) <= {s["source_id"] for s in specs}:
            raise ValueError("wave_session_source_scope_mismatch")
        specs = [s for s in specs if s["source_id"] in source_ids]
    now = utc_now_timestamp()
    with closing(connection(db_path)) as conn:
        conn.execute("BEGIN")
        current = WaveSessionRepository.read(conn, symbol, start, end, now)
        ready = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='wave_session_evidence' AND type='table'").fetchone())
    if execute and not ready:
        raise ValueError("wave_session_migration_required")
    repository = WaveSessionRepository(db_path)
    deadline = time.monotonic()+180
    transport = client
    items = []
    for spec in specs:
        suffix = hashlib.sha256((symbol+spec["url"]+spec["start"]+spec["end"]).encode()).hexdigest()
        key = hashlib.sha256((request_key+suffix).encode()).hexdigest()
        prior = repository.by_request(key) if ready else None
        if prior:
            if prior["spec"] != spec:
                raise ValueError("wave_session_idempotency_conflict")
            items.append(dict(source_id=spec["source_id"], start=spec["start"], end=spec["end"], status="replayed", reference=prior["reference"]))
            continue
        latest = next((r for r in current if r["spec"] == spec), None)
        if latest and not force and datetime.fromisoformat(now.replace("Z", "+00:00"))-datetime.fromisoformat(latest["fetched_at"].replace("Z", "+00:00")) < timedelta(hours=24):
            items.append(dict(source_id=spec["source_id"], start=spec["start"], end=spec["end"], status="retained_recent", reference=latest["reference"]))
            continue
        if not execute:
            items.append(dict(spec, status="planned"))
            continue
        if time.monotonic() >= deadline:
            items.append(dict(source_id=spec["source_id"], start=spec["start"], end=spec["end"], status="not_attempted_deadline"))
            continue
        raw, status, reason = "", "accepted", ""
        response_sha256, http_status = None, None
        transport = transport or InstalledEgressClient()
        try:
            code, body, _ = transport.fetch(spec["url"], deadline_monotonic=deadline, max_retries=1)
            response_sha256, http_status = hashlib.sha256(body).hexdigest(), code
            if code != 200 or len(body) > MAX_BODY:
                raise ValueError("official_http_or_size_error")
            raw = body.decode("utf-8-sig")
            validate_observation(parse_source(spec, json.loads(raw)), utc_now_timestamp())
        except Exception:
            # Never persist proxy credentials, arbitrary exception text, or HTML.
            status, reason, raw = "failed", "official_fetch_or_contract_failed", ""
        fetched = utc_now_timestamp()
        record = repository.append(spec, key=key, fetched_at=fetched, raw_text=raw, status=status, reason=reason,
                                   response_sha256=response_sha256, http_status=http_status)
        items.append(dict(source_id=spec["source_id"], start=spec["start"], end=spec["end"], status=status, reference=record["reference"]))
    return dict(contract_version="wave_session_refresh_v1", symbol=symbol, start=start, end=end,
        mode="execute" if execute else "preview", migration_ready=ready, request_count=len(specs), items=items,
        automatic_candidates_eligible=False, formal_research_written=False)
