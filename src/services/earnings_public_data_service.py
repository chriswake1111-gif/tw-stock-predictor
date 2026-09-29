"""Explicit-operation, bounded source collection; no new tables or approvals."""
import base64
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from src.collectors.earnings_four_quarter import (
    CONTRACT, DATASET, MAX_BUNDLE_BYTES, MAX_DOCUMENT_BYTES, normalize_bundle,
)
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from src.collectors.earnings_coverage import coverage_for
from src.collectors.installed_egress_client import EgressHttpError, DeadlineExhaustedError
from src.collectors.earnings_sources_v2 import CATALOG_VERSION, allowed_source_url, sources_for
from src.domain.analysis_snapshot import canonical_json
from src.domain.valuation import utc_now_timestamp
from src.services.daily_public_data_service import DailyPublicDataService

RESOURCE = "issuer.verified-quarterly-earnings"
PARSER_VERSION = CONTRACT + ":" + CATALOG_VERSION
MAX_STORAGE_BYTES = 128 * 1024 * 1024
MAX_ATTEMPTS = 4096


def download_failure_reason(exc):
    # Never parse or disclose remote exception text, URLs, headers or credentials.
    response = getattr(exc.__cause__, "response", None)
    status = getattr(response, "status_code", None)
    return {401: "earnings_source_access_denied", 403: "earnings_source_access_denied",
            404: "earnings_source_not_found", 410: "earnings_source_not_found",
            429: "earnings_source_rate_limited"}.get(status, "earnings_source_fetch_failed")


def earnings_enabled():
    return os.environ.get("RESEARCH_EARNINGS_V2_ENABLED", "false").strip().lower() == "true"


def _limit_response(limit):
    def validate(status, body, headers):
        if status != 200 or not 0 < len(body) <= limit:
            raise EarningsSourceAuditError("earnings_response_invalid_or_oversized")
    return validate


def fetch_document(client, source, deadline, authorize):
    """URLs originate solely from the code-owned catalog; never from research text."""
    url = source["url"]
    if not allowed_source_url(url):
        raise EarningsSourceAuditError("earnings_source_not_allowed")
    if urlsplit(url).path == "/server-java/t57sb01":
        authorize(RESOURCE)
        _, html, _ = client.fetch(url, deadline_monotonic=deadline, max_retries=1,
                                  response_validator=_limit_response(64 * 1024))
        filename = parse_qs(urlsplit(url).query)["filename"][0][:-4]
        # The public form uses legacy Chinese encoding. Link targets are ASCII;
        # Latin-1 preserves every byte and never drops untrusted path characters.
        paths = set(re.findall(r"href=['\"](/pdf/[^'\"]+)['\"]", html.decode("latin-1")))
        paths = [p for p in paths if re.fullmatch(re.escape("/pdf/" + filename) + r"_[0-9]{8}_[0-9]{6}\.pdf", p)]
        if len(paths) != 1:
            raise EarningsSourceAuditError("earnings_download_link_missing")
        url = "https://doc.twse.com.tw" + paths[0]
        if not allowed_source_url(url):
            raise EarningsSourceAuditError("earnings_source_not_allowed")
    authorize(RESOURCE)
    _, raw, _ = client.fetch(url, deadline_monotonic=deadline, max_retries=1,
                            response_validator=_limit_response(MAX_DOCUMENT_BYTES))
    return raw


class EarningsPublicDataService(DailyPublicDataService):
    datasets = (DATASET,)

    def source_metadata(self, dataset):
        return dict(source="公司原始財報及季度發布資料", official_exchange_source=False, value=None)

    def select_snapshot(self, conn, symbol, dataset, cutoff):
        # A failed revision remains preserved. A later successful fetch can
        # reference the older, identical good bytes without creating a duplicate.
        return conn.execute(
            "SELECT s.* FROM daily_public_attempts a JOIN daily_public_snapshots s ON a.snapshot_id=s.snapshot_id "
            "AND a.symbol=s.symbol AND a.dataset=s.dataset WHERE a.symbol=? AND a.dataset=? "
            "AND a.checked_at<=? AND s.observed_at<=? ORDER BY a.checked_at DESC,a.attempt_id DESC LIMIT 1",
            (symbol, dataset, cutoff, cutoff)).fetchone()

    def view(self, symbol, cutoff):
        item = super().view(symbol, cutoff).get(DATASET, dict(status="insufficient_data",
            rows=[], dataset=DATASET, reason="not_collected", **self.source_metadata(DATASET)))
        if not sources_for(symbol):
            item.update(status="insufficient_data", value=None, reason="source_format_not_supported")
        elif item.get("parser_version") and item["parser_version"] != PARSER_VERSION:
            item.update(status="quality_warning", value=None, reason="earnings_parser_requires_refresh")
        elif item.get("contract_version") == CONTRACT and item.get("data_date"):
            age = datetime.fromisoformat(cutoff.replace("Z", "+00:00")).date() - datetime.fromisoformat(item["data_date"]).date()
            item["is_stale"] = age.days > 135
            if item["is_stale"]:
                item.update(status="stale", value=None, reason="earnings_period_requires_refresh")
        if item.get("last_update_status") == "failed":
            item.update(status="quality_warning", value=None, reason=item.get("last_update_reason") or "earnings_update_failed")
        coverage = coverage_for(symbol, cutoff)
        if coverage:
            item["source_coverage"] = coverage
            if not sources_for(symbol) and coverage["status"] == "evidence_incomplete":
                item.update(status="insufficient_data", value=None, reason="quarter_source_evidence_incomplete")
        return {DATASET: item}

    def refresh(self, symbol, operation_id, client, authorize, deadline):
        if not earnings_enabled() or not sources_for(symbol):
            return []
        authorize(RESOURCE)
        now = utc_now_timestamp()
        current = self.view(symbol, now)[DATASET]
        checked = current.get("last_checked_at")
        if checked and current.get("parser_version", PARSER_VERSION) == PARSER_VERSION:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(checked.replace("Z", "+00:00"))
            minimum = timedelta(hours=24) if current.get("last_update_status") == "available" else timedelta(minutes=30)
            if timedelta(0) <= age < minimum:
                return []
        documents = {}
        source_times = {}
        reason = None
        normalized = None
        collecting = True
        try:
            for source in sources_for(symbol):
                documents[source["key"]] = fetch_document(client, source, deadline, authorize)
                source_times[source["key"]] = utc_now_timestamp()
                if sum(map(len, documents.values())) > MAX_BUNDLE_BYTES:
                    raise EarningsSourceAuditError("source_bundle_size_limit")
                if hashlib.sha256(documents[source["key"]]).hexdigest() != source["sha256"]:
                    raise EarningsSourceAuditError("source_revision_requires_review")
            observed = utc_now_timestamp()
            collecting = False
            normalized = normalize_bundle(symbol, documents, observed)
            for source in normalized.get("sources", []):
                source["observed_at"] = source_times[source["key"]]
        except EarningsSourceAuditError as exc:
            reason = str(exc)
        except EgressHttpError as exc:
            reason = download_failure_reason(exc)
        except DeadlineExhaustedError:
            reason = "earnings_source_timeout"
        except Exception:
            # No raw remote text, URLs supplied by a page, or network credentials in reports.
            reason = "earnings_source_fetch_failed" if collecting else "earnings_source_parse_failed"
        if reason == "source_revision_requires_review":
            # Preserve the newly observed bytes as an explicitly unqualified
            # immutable version. Never parse them or promote old rows to a new sum.
            observed = utc_now_timestamp()
            excluded = {"snapshot_id", "raw_sha256", "parser_version", "source_url", "last_checked_at",
                        "last_update_status", "last_update_reason", "is_stale", "unreviewed_source_versions"}
            normalized = {k: deepcopy(v) for k, v in current.items() if k not in excluded}
            normalized.update(status="quality_warning", value=None, reason=reason, contract_version=CONTRACT,
                available_at=observed, observed_at=current.get("observed_at") or observed,
                previous_snapshot_id=current.get("snapshot_id"),
                unreviewed_source_versions=[dict(key=s["key"], url=s["url"],
                    sha256=hashlib.sha256(documents[s["key"]]).hexdigest(), observed_at=source_times[s["key"]])
                    for s in sources_for(symbol) if s["key"] in documents])
        # A revoked operation must escape before either the snapshot or attempt write.
        authorize(RESOURCE)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            size = conn.execute(
                "SELECT COALESCE(SUM(length(CAST(raw_json AS BLOB))+length(CAST(normalized_json AS BLOB))),0) "
                "FROM daily_public_snapshots WHERE dataset=?", (DATASET,)).fetchone()
            size = size[0]
            attempts = conn.execute("SELECT COUNT(*) FROM daily_public_attempts WHERE dataset=?", (DATASET,)).fetchone()[0]
            if attempts >= MAX_ATTEMPTS:
                return [DATASET + ":earnings_storage_limit"]
            snapshot_id = None
            if normalized is not None:
                normalized["ingested_at"] = utc_now_timestamp()
                normalized["available_at"] = max(observed, normalized["ingested_at"])
                raw_text = canonical_json({key: base64.b64encode(raw).decode("ascii") for key, raw in sorted(documents.items())})
                raw_hash = hashlib.sha256(raw_text.encode()).hexdigest()
                existing = conn.execute(
                    "SELECT snapshot_id FROM daily_public_snapshots WHERE symbol=? AND dataset=? AND raw_sha256=? AND parser_version=?",
                    (symbol, DATASET, raw_hash, PARSER_VERSION)).fetchone()
                text = canonical_json(normalized)
                if existing:
                    snapshot_id = existing[0]
                elif size + len(raw_text.encode()) + len(text.encode()) > MAX_STORAGE_BYTES:
                    reason = "earnings_storage_limit"
                else:
                    snapshot_id = "earnings_" + uuid4().hex
                    conn.execute("INSERT INTO daily_public_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", (
                        snapshot_id, symbol, DATASET, normalized["available_at"], sources_for(symbol)[0]["url"], raw_hash, raw_text,
                        text, hashlib.sha256(text.encode()).hexdigest(), PARSER_VERSION))
            conn.execute("INSERT INTO daily_public_attempts VALUES (?,?,?,?,?,?,?,?)", (
                "attempt_" + uuid4().hex, operation_id, symbol, DATASET, utc_now_timestamp(),
                "failed" if reason else "available", reason, snapshot_id))
        return [DATASET + ":" + reason] if reason else []
