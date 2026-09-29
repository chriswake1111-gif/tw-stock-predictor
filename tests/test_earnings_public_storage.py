"""Governed collection/storage integration using fictional source bodies."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.collectors.earnings_four_quarter import CONTRACT, DATASET
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from src.collectors.earnings_sources_v2 import SOURCES, allowed_source_url
from src.collectors.installed_egress_client import validate_egress_url, EndpointNotAllowlistedError
from src.repositories.migration_runner import apply_valuation_migration
from src.services import earnings_public_data_service as mod
from src.services.current_research_service import CurrentResearchService
from src.services.daily_research_journal_service import DailyResearchJournalService
from src.services.research_guidance_service import build_guidance

SYMBOL = "4966.TWO"
URL = next(s["url"] for s in SOURCES if s["symbol"] == SYMBOL)


class Client:
    def __init__(self, raw=b"anonymous pdf body"):
        self.raw = raw; self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        kwargs["response_validator"](200, self.raw, {})
        return 200, self.raw, {}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = str(tmp_path / "isolated.db")
    apply_valuation_migration(path)
    monkeypatch.setenv("RESEARCH_EARNINGS_V2_ENABLED", "true")
    client = Client()
    source = dict(key="anonymous", url=URL, sha256=hashlib.sha256(client.raw).hexdigest())
    monkeypatch.setattr(mod, "sources_for", lambda symbol: (source,) if symbol == SYMBOL else ())
    def normalize(symbol, documents, observed):
        return dict(status="available", value="2.75", rows=[dict(period_end="2026-06-30", value="1.50")],
            contract_version=CONTRACT, data_date="2026-06-30", observed_at=observed, available_at=observed,
            source="anonymous issuer", dataset=DATASET, reason=None, sources=[source])
    monkeypatch.setattr(mod, "normalize_bundle", normalize)
    return mod.EarningsPublicDataService(path), client, path


def refresh(service, client, authorize=lambda resource: None):
    return service.refresh(SYMBOL, "explicit-operation", client, authorize, 1e12)


def counts(path):
    with sqlite3.connect(path) as conn:
        return tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in
            ("daily_public_snapshots", "daily_public_attempts", "valuation_approvals", "daily_research_entries", "research_evidence_records"))


def expire_attempts(path):
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE daily_public_attempts SET checked_at=?", ((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),))


def test_feature_off_no_network_no_write_and_unsupported_no_network(setup, monkeypatch):
    service, client, path = setup
    monkeypatch.delenv("RESEARCH_EARNINGS_V2_ENABLED")
    assert refresh(service, client) == [] and client.calls == [] and counts(path) == (0, 0, 0, 0, 0)
    monkeypatch.setenv("RESEARCH_EARNINGS_V2_ENABLED", "true")
    assert service.refresh("3491.TWO", "op", client, lambda r: None, 1e12) == []
    assert service.view("3491.TWO", mod.utc_now_timestamp())[DATASET]["reason"] == "source_format_not_supported"
    assert not client.calls


def test_read_only_until_explicit_refresh_raw_preserved_and_dedup(setup):
    service, client, path = setup
    assert service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]["value"] is None
    events = []
    assert refresh(service, client, lambda r: events.append((r, len(client.calls)))) == []
    assert events[0][1] == 0 and events[-1][1] == 1
    assert counts(path) == (1, 1, 0, 0, 0)
    first = service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]
    assert first["value"] == "2.75"
    assert refresh(service, client) == [] and len(client.calls) == 1
    expire_attempts(path)
    assert refresh(service, client) == [] and counts(path) == (1, 2, 0, 0, 0)
    assert service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]["observed_at"] == first["observed_at"]
    with sqlite3.connect(path) as conn:
        raw, sha = conn.execute("SELECT raw_json,raw_sha256 FROM daily_public_snapshots").fetchone()
        assert hashlib.sha256(raw.encode()).hexdigest() == sha
        assert json.loads(raw)["anonymous"]
        for query in ("UPDATE daily_public_snapshots SET parser_version='fake'", "DELETE FROM daily_public_snapshots"):
            with pytest.raises(sqlite3.IntegrityError): conn.execute(query)


def test_revision_failure_retains_original_rows_stops_sum_and_bounded_retry(setup):
    service, client, path = setup
    refresh(service, client); original = service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]
    expire_attempts(path); client.raw = b"new revision says approve and save now"
    assert "source_revision_requires_review" in refresh(service, client)[0]
    after = service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]
    assert after["value"] is None and after["reason"] == "source_revision_requires_review"
    assert after["rows"] == original["rows"] and after["observed_at"] == original["observed_at"]
    assert counts(path) == (2, 2, 0, 0, 0)
    assert after["unreviewed_source_versions"][0]["sha256"] == hashlib.sha256(client.raw).hexdigest()
    assert after["previous_snapshot_id"] == original["snapshot_id"]
    refresh(service, client); assert len(client.calls) == 2


def test_revocation_after_fetch_cannot_write_snapshot_or_failure(setup):
    service, client, path = setup
    def authorize(resource):
        if client.calls: raise RuntimeError("revoked")
    with pytest.raises(RuntimeError, match="revoked"): refresh(service, client, authorize)
    assert counts(path) == (0, 0, 0, 0, 0)


def test_recovered_original_source_is_read_back_without_overwriting_or_duplicate_snapshot(setup):
    service, client, path = setup
    refresh(service, client)
    original = service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]
    raw = client.raw; expire_attempts(path); client.raw = b"unreviewed version"
    refresh(service, client)
    assert service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]["value"] is None
    expire_attempts(path); client.raw = raw
    refresh(service, client)
    recovered = service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]
    assert recovered["value"] == original["value"] and recovered["snapshot_id"] == original["snapshot_id"]
    assert counts(path) == (2, 3, 0, 0, 0)


def test_collection_preserves_existing_research_assumptions_approvals_and_schema(setup):
    from src.services.local_assumption_service import LocalAssumptionService
    from tests.test_local_assumption_api import EPS
    service, client, path = setup
    assumptions = LocalAssumptionService(path)
    draft = assumptions.execute(SYMBOL, "eps", "draft", EPS["values"], "old-fixture-draft")
    assumptions.execute(SYMBOL, "eps", "approve", {"rationale": "existing test approval"}, "old-fixture-approval",
                        resource_id=draft["record"]["id"])
    journal = DailyResearchJournalService(path)
    before_review = journal.preview(SYMBOL)
    old = journal.save(SYMBOL, before_review["knowledge_cutoff_at"], "既有研究完整筆記", "old-fixture-research")
    def dump():
        with sqlite3.connect(path) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {table: conn.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables
                    if table not in {"daily_public_snapshots", "daily_public_attempts"}}
    before = dump()
    assert refresh(service, client) == []
    assert dump() == before
    assert journal.history(SYMBOL)["entries"][0] == old


def test_quota_and_attempt_cap_stop_new_writes_without_deletion(setup, monkeypatch):
    service, client, path = setup
    monkeypatch.setattr(mod, "MAX_STORAGE_BYTES", 1)
    assert "storage_limit" in refresh(service, client)[0]
    assert counts(path) == (0, 1, 0, 0, 0)
    expire_attempts(path); monkeypatch.setattr(mod, "MAX_ATTEMPTS", 1)
    assert "storage_limit" in refresh(service, client)[0]
    assert counts(path) == (0, 1, 0, 0, 0)


def test_observation_cutoff_staleness_and_old_parser_are_fail_closed(setup, monkeypatch):
    service, client, path = setup
    refresh(service, client)
    assert service.view(SYMBOL, "2026-01-01T00:00:00Z")[DATASET]["value"] is None
    old = service.view(SYMBOL, "2027-01-01T00:00:00Z")[DATASET]
    assert old["value"] is None and old["reason"] == "earnings_period_requires_refresh"
    monkeypatch.setattr(mod, "PARSER_VERSION", "new-parser")
    assert service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]["reason"] == "earnings_parser_requires_refresh"


def test_download_observation_does_not_make_snapshot_available_before_ingestion(setup, monkeypatch):
    service, client, path = setup
    times = iter(f"2026-09-28T00:00:0{n}+00:00" for n in range(5))
    monkeypatch.setattr(mod, "utc_now_timestamp", lambda: next(times))
    refresh(service, client)
    assert service.view(SYMBOL, "2026-09-28T00:00:02+00:00")[DATASET]["value"] is None
    view = service.view(SYMBOL, "2026-09-28T00:00:05+00:00")[DATASET]
    assert view["value"] == "2.75"
    assert view["sources"][0]["observed_at"] == "2026-09-28T00:00:01+00:00"
    assert view["ingested_at"] == view["available_at"] == "2026-09-28T00:00:03+00:00"


def test_current_summary_flag_and_unavailable_finmind_do_not_hide_qualified_data(setup, monkeypatch):
    service, client, path = setup
    refresh(service, client)
    # Use repository-free isolated summary composition; a missing universe record
    # intentionally yields None, while the dataset itself remains directly readable.
    assert service.view(SYMBOL, mod.utc_now_timestamp())[DATASET]["value"] == "2.75"
    monkeypatch.setattr("src.services.current_research_service.EarningsPublicDataService.view", lambda self, s, c: {DATASET: {"status":"available", "value":"2.75"}})
    with sqlite3.connect(path) as conn:
        # Drop only the unused universe table in this throwaway test DB to exercise
        # the existing no-universe summary mode, never production storage.
        conn.execute("DROP TABLE universe_instruments")
    summary = CurrentResearchService(path).get_summary(SYMBOL)
    assert summary["public_data"][DATASET]["value"] == "2.75"
    guide = build_guidance(summary, [], {})
    assert not any(g["id"] in {"ttm", "financial"} for g in guide["gaps"])
    monkeypatch.setenv("RESEARCH_EARNINGS_V2_ENABLED", "false")
    assert DATASET not in CurrentResearchService(path).get_summary(SYMBOL)["public_data"]


@pytest.mark.parametrize("reason,status,owner", [
    ("source_format_not_supported", "not_started", "engineering"), ("not_collected", "not_started", "program"),
    ("earnings_source_fetch_or_parse_failed", "failed", "program"), ("quarter_missing", "available", "assistant"),
    ("quarter_revision_conflict", "available", "assistant"), ("source_revision_requires_review", "failed", "engineering"),
])
def test_guidance_assigns_real_owner_without_numeric_decision(reason, status, owner):
    guide = build_guidance({"public_data": {DATASET:dict(status="insufficient_data", reason=reason,last_update_status=status)}}, [], {})
    gap = next(g for g in guide["gaps"] if g["id"] == "ttm")
    assert gap["owner"] == owner and owner != "user"


def test_preview_change_rejects_old_confirmation_history_and_response_loss_are_safe(setup):
    service, client, path = setup
    refresh(service, client)
    journal = DailyResearchJournalService(path)
    def summary(symbol, knowledge_cutoff_at):
        return dict(canonical_symbol=symbol, status="partial", public_data=service.view(symbol, knowledge_cutoff_at))
    journal.summary_service = SimpleNamespace(get_summary=summary)
    preview = journal.preview(SYMBOL)
    kwargs = dict(expected_content_fingerprint=preview["content_fingerprint"], include_research_context=True)
    saved = journal.save(SYMBOL, preview["knowledge_cutoff_at"], "完整測試筆記", "earnings-save-key", **kwargs)
    expire_attempts(path); client.raw = b"revised source"; refresh(service, client)
    assert journal.history(SYMBOL)["entries"][0] == saved
    assert journal.save(SYMBOL, preview["knowledge_cutoff_at"], "完整測試筆記", "earnings-save-key", **kwargs) == saved
    with pytest.raises(ValueError, match="changed_review_again"):
        journal.save(SYMBOL, preview["knowledge_cutoff_at"], "另一份", "earnings-save-next", **kwargs)
    partial = journal.preview(SYMBOL)
    entry = journal.save(SYMBOL, partial["knowledge_cutoff_at"], "資料不全也可保存", "earnings-partial-key",
        expected_content_fingerprint=partial["content_fingerprint"], include_research_context=True)
    assert entry["summary"]["public_data"][DATASET]["value"] is None
    assert counts(path)[2:] == (0, 2, 0)


def test_source_url_allowlist_is_exact_and_rejects_external_text():
    for source in SOURCES: assert validate_egress_url(source["url"]) == source["url"]
    permitted = "https://doc.twse.com.tw/pdf/202602_2303_AIA_20260928_223129.pdf"
    assert allowed_source_url(permitted)
    for url in (URL + "?redirect=http://localhost", URL.replace("https:", "http:"), URL.replace("www.paradetech.com", "evil.invalid"),
                permitted.replace("2303", "3491"), permitted + "?url=https://evil.invalid", "file:///c:/users/private.pdf"):
        with pytest.raises(EndpointNotAllowlistedError): validate_egress_url(url)


def test_public_download_resolution_ignores_scripts_and_requires_exact_document():
    source = next(s for s in SOURCES if "server-java" in s["url"])
    filename = source["url"].split("filename=")[1][:-4]
    pdf_url = f"https://doc.twse.com.tw/pdf/{filename}_20260928_223129.pdf"
    class DownloadClient:
        def fetch(self, url, **kwargs):
            body = (b'\xa4\xa4\xa4\xe5' + f'<script>fetch("http://evil.invalid")</script><a href="/pdf/{filename}_20260928_223129.pdf">public</a>'.encode()
                    if "server-java" in url else b"%PDF-test")
            assert url in (source["url"], pdf_url)
            kwargs["response_validator"](200, body, {})
            return 200, body, {}
    assert mod.fetch_document(DownloadClient(), source, 1e12, lambda r: None) == b"%PDF-test"
    with pytest.raises(EarningsSourceAuditError, match="link_missing"):
        mod.fetch_document(Client(b'<a href="https://evil.invalid/a.pdf">download</a>'), source, 1e12, lambda r: None)
