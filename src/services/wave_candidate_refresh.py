"""Explicit bounded collector; never invoked by a read or a scheduler."""
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import time
from zoneinfo import ZoneInfo

from src.collectors.installed_egress_client import InstalledEgressClient
from src.collectors.wave_candidate_sources import VERSION, plan
from src.domain.valuation import utc_now_timestamp
from src.repositories.wave_candidate_repository import WaveCandidateRepository
from src.repositories.wave_session_repository import connection, digest
from src.services.wave_candidate_service import enabled, price_binding, qualify


def refresh(db_path, symbol, start, end, *, request_key, execute=False, force=False, client=None):
    if not enabled():
        raise ValueError("wave_candidates_disabled")
    if not isinstance(request_key, str) or not 8 <= len(request_key) <= 128:
        raise ValueError("wave_candidate_request_key_required")
    if end >= datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat():
        raise ValueError("wave_candidate_completed_dates_required")
    specs = plan(symbol, start, end)
    repository = WaveCandidateRepository(db_path)
    with closing(connection(db_path)) as conn:
        conn.execute("BEGIN")
        ready = repository.ready(conn)
        binding = price_binding(conn, db_path, symbol)
        if binding and binding["bounds"] != dict(start=start, end=end):
            raise ValueError("wave_candidate_requested_range_mismatch")
        current = repository.latest(conn, symbol)
    result = dict(contract_version="wave_candidate_refresh_v1", symbol=symbol, requested_range=dict(start=start, end=end),
                  mode="execute" if execute else "preview", migration_ready=ready, request_count=len(specs),
                  formal_research_written=False, historical_backtest_eligible=False)
    prior = repository.by_request(request_key) if ready else None
    if prior:
        if prior["symbol"] != symbol or prior["range"] != result["requested_range"]:
            raise ValueError("wave_candidate_idempotency_conflict")
        return dict(result, status="replayed", package_ref=prior["package_ref"])
    if current and current["range"] == result["requested_range"] and current["price_binding"] == binding and not force:
        age = datetime.fromisoformat(utc_now_timestamp().replace("Z", "+00:00"))-datetime.fromisoformat(current["known_at"].replace("Z", "+00:00"))
        if age < timedelta(hours=24):
            return dict(result, status="retained_recent", package_ref=current["package_ref"])
    if not execute:
        return dict(result, status="planned", sources=specs)
    if not ready:
        raise ValueError("wave_candidate_migration_required")
    deadline = time.monotonic()+180
    transport = client or InstalledEgressClient()
    sources = []
    for spec in specs:
        raw, status, http, response_hash = "", "accepted", None, None
        try:
            if time.monotonic() >= deadline:
                raise ValueError("wave_candidate_deadline")
            http, body, _ = transport.fetch(spec["url"], deadline_monotonic=deadline, max_retries=1)
            if http != 200 or len(body) > 2*1024*1024:
                raise ValueError("wave_candidate_source_http_or_size")
            response_hash = hashlib.sha256(body).hexdigest()
            raw = body.decode("utf-8-sig")
        except Exception:
            # Do not persist transport exceptions, credentials, or failed HTML.
            status, raw = "failed", ""
        sources.append(dict(spec=spec, fetched_at=utc_now_timestamp(), status=status,
                            raw_text=raw, raw_sha256=digest(raw), response_sha256=response_hash, http_status=http))
    payload = dict(version=VERSION, symbol=symbol, range=dict(start=start, end=end), price_binding=binding,
                   status="accepted" if all(s["status"] == "accepted" for s in sources) else "failed", sources=sources)
    saved = repository.append(payload, request_key)
    rows, checks = qualify(saved)
    return dict(result, status="checked" if rows else "incomplete", package_ref=saved["package_ref"],
                row_count=len(rows), checks=checks)
