"""Immutable current-view research notes, distinct from historical eligibility."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from src.domain.analysis_snapshot import canonical_json, sha256_json
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository, SnapshotIntegrityError
from src.services.current_research_service import CurrentResearchService
from src.services.local_assumption_service import LocalAssumptionService
from src.services.research_evidence_service import ResearchEvidenceService, guidance_enabled
from src.services.research_guidance_service import build_guidance

ASSISTANT_CONTRACT = "tw_stock_research_assistant_v1"


def _current_content(value):
    """Ignore query-clock echoes only, never source/ingestion/approval dates."""
    if isinstance(value, dict):
        return {k: _current_content(v) for k, v in value.items()
                if k not in {"knowledge_cutoff_at", "source_data_as_of"}}
    if isinstance(value, list):
        return [_current_content(v) for v in value]
    return value


def comparable_facts(summary):
    market = summary.get("market_context", {})
    facts = {"official_close": {"value": market.get("official_close"), "date": market.get("settled_trade_date"),
                               "source": summary.get("venue"), "unit": "TWD_per_share"}}
    for dataset, fields in (("TaiwanStockPER", ("pe", "pb", "yield_ratio")),
                            ("TaiwanStockPrice", ("volume",))):
        data = summary.get("public_data", {}).get(dataset, {})
        rows = data.get("rows", [])
        for field in fields:
            facts[field] = {"value": rows[-1].get(field) if rows else None,
                            "date": rows[-1].get("date") if rows else None,
                            "source": data.get("source"), "unit": "shares" if field == "volume" else "ratio"}
    return facts


def compare_entries(previous, current):
    if previous is None:
        return {"status": "no_previous", "facts": [], "assumptions_changed": False}
    before, after = previous["summary"], current["summary"]
    old, new = comparable_facts(before), comparable_facts(after)
    facts = []
    for key, value in new.items():
        prior = old[key]
        comparable = (prior["value"] is not None and value["value"] is not None
                      and prior["source"] == value["source"] and prior["unit"] == value["unit"]
                      and bool(prior["date"]) and bool(value["date"]) and prior["date"] <= value["date"])
        facts.append({"field": key, "before": prior, "after": value,
                      "delta": value["value"] - prior["value"] if comparable else None,
                      "status": "comparable" if comparable else "not_comparable"})
    # Older entries hashed query-clock echoes inside technical traces. Compare
    # their stored model content without those echoes; never rewrite old rows.
    def model_content(summary):
        return _current_content({k: summary.get(k) for k in ("valuation_context", "technical_context")})
    changed = model_content(before) != model_content(after)
    return {"status": "available", "facts": facts, "assumptions_changed": changed,
            "model_comparison_status": "assumptions_changed" if changed else "same_assumptions",
            "previous_date": before.get("market_context", {}).get("settled_trade_date"),
            "current_date": after.get("market_context", {}).get("settled_trade_date")}


class DailyResearchJournalService:
    def __init__(self, db_path):
        self.db_path = db_path
        self.summary_service = CurrentResearchService(db_path)

    @staticmethod
    def _decode(row):
        payload = json.loads(row["payload_json"])
        if sha256_json(payload) != row["payload_sha256"]:
            raise SnapshotIntegrityError("daily_research_integrity_error")
        return {"entry_id": row["entry_id"], "created_at": row["created_at"],
                "analysis_snapshot_id": row["analysis_snapshot_id"], **payload}

    def _payload(self, symbol, cutoff, note):
        summary = self.summary_service.get_summary(symbol, knowledge_cutoff_at=cutoff)
        if summary is None:
            summary = {"canonical_symbol": symbol, "knowledge_cutoff_at": cutoff,
                       "status": "insufficient_data", "reason": "local_quote_not_available"}
        # IDs embedded in governed engine output identify actual applied inputs;
        # changing/revoking an approval must not look like a market-price change.
        assumptions = {k: summary.get(k) for k in ("valuation_context", "technical_context")}
        return {"contract_version": "daily_research_journal_v1", "symbol": symbol,
                "knowledge_cutoff_at": cutoff, "summary": summary, "note": note,
                "model_version": summary.get("audit_reference", {}).get("model_version"),
                "assumption_fingerprint": sha256_json(_current_content(assumptions)),
                "historical_eligibility": "not_asserted", "status": "partial"}

    def _review(self, symbol, cutoff, payload, research_year=None, prefer_saved_year=False):
        previous = self.history(symbol, 1)["entries"]
        previous = previous[0] if previous else None
        if prefer_saved_year and previous:
            saved_year = (previous.get("research_context") or {}).get("selected_year")
            if type(saved_year) is int and 1900 <= saved_year <= 2200:
                research_year = saved_year
        latest = self._payload(symbol, utc_now_timestamp(), "")
        assumptions = LocalAssumptionService(self.db_path).list(symbol)["items"]
        guard = {"contract": ASSISTANT_CONTRACT, "reviewed": {**payload, "note": ""},
                 "latest": _current_content(latest["summary"]), "assumptions": assumptions,
                 "previous_entry_id": previous["entry_id"] if previous else None}
        extra = {}
        if guidance_enabled():
            basis = sha256_json({"latest": _current_content(latest["summary"]),
                                 "assumptions": assumptions, "previous_entry_id": guard["previous_entry_id"]})
            evidence = ResearchEvidenceService(self.db_path).guidance_evidence(symbol, cutoff=cutoff)
            guidance = build_guidance(payload["summary"], assumptions, evidence, research_year)
            # An active assumption can intentionally retain an older source.
            # Freeze that exact version too; do not silently replace its basis.
            guidance["assumption_evidence"] = [
                {"assumption_id": a["id"], "approval": a.get("approval"),
                 "evidence": ResearchEvidenceService(self.db_path).get(symbol, a["candidate_id"])}
                for a in assumptions if a.get("candidate_id") and not a.get("superseded")]
            brief = next((i for i in evidence["all_items"] if i["kind"] == "brief" and i["fiscal_year"] in {None, guidance["selected_year"]}), None)
            guidance["note_draft"] = brief["note_draft"] if brief and brief["base_review_fingerprint"] == basis else ""
            guidance["note_draft_stale"] = bool(brief and brief["base_review_fingerprint"] != basis)
            latest_evidence = ResearchEvidenceService(self.db_path).guidance_evidence(symbol)
            guard["research_context"] = guidance
            guard["latest_evidence"] = [(i["record_id"], i["content_sha256"]) for i in latest_evidence["all_items"]]
            extra = {"guidance": guidance, "financial_content_fingerprint": basis}
        return {"contract_version": ASSISTANT_CONTRACT, "symbol": symbol,
                "knowledge_cutoff_at": cutoff, "current": {**payload, "note": ""},
                "previous": previous, "comparison": compare_entries(previous, payload),
                "assumptions": assumptions, "content_fingerprint": sha256_json(guard),
                "review_revision_fingerprint": sha256_json(_current_content(guard)), **extra}

    def preview(self, symbol, research_year=None, *, prefer_saved_year=False):
        parse_canonical_symbol(symbol)
        # Readers below use their own connections. Reserve the writer while
        # composing the review so they all observe the same committed state.
        database = Path(self.db_path).resolve().as_uri() + "?mode=rw" if prefer_saved_year else self.db_path
        with closing(sqlite3.connect(database, uri=prefer_saved_year)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            cutoff = normalize_utc_timestamp(utc_now_timestamp(), "knowledge_cutoff_at")
            return self._review(symbol, cutoff, self._payload(symbol, cutoff, ""), research_year,
                                prefer_saved_year=prefer_saved_year)

    def save(self, symbol, cutoff, note, key, expected_content_fingerprint=None,
             research_year=None, include_research_context=False):
        parse_canonical_symbol(symbol)
        cutoff = normalize_utc_timestamp(cutoff, "knowledge_cutoff_at")
        if cutoff > utc_now_timestamp():
            raise ValueError("future_knowledge_cutoff")
        if len(note) > 4000 or not 8 <= len(key) <= 128:
            raise ValueError("invalid_journal_request")
        request = dict(symbol=symbol, cutoff=cutoff, note=note)
        if include_research_context:
            if expected_content_fingerprint is None:
                raise ValueError("guided_save_requires_preview")
            request.update(research_year=research_year, include_research_context=True)
        if expected_content_fingerprint is not None:
            request["expected_content_fingerprint"] = expected_content_fingerprint
        fingerprint = sha256_json(request)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM daily_research_entries WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise ValueError("research_idempotency_conflict")
                return self._decode(existing)
            payload = self._payload(symbol, cutoff, note)
            if expected_content_fingerprint is not None:
                reviewed = self._review(symbol, cutoff, payload, research_year)
                if reviewed["content_fingerprint"] != expected_content_fingerprint:
                    raise ValueError("research_content_changed_review_again")
                if include_research_context:
                    if "guidance" not in reviewed:
                        raise ValueError("research_guidance_disabled")
                    payload["research_context"] = reviewed["guidance"]
            # Link only an exact existing analysis. A partial observation never
            # enters the historical-analysis snapshot population by implication.
            row = conn.execute("SELECT snapshot_id FROM analysis_snapshots WHERE symbol=? AND knowledge_cutoff_at=? ORDER BY created_at DESC LIMIT 1", (symbol, cutoff)).fetchone()
            snapshot_id = None
            if row:
                snapshot = AnalysisSnapshotRepository(self.db_path, auto_migrate=False).get_with_connection(conn, row[0])
                snapshot_id = snapshot["snapshot_id"]
            entry_id = "daily_research_" + uuid4().hex
            conn.execute("INSERT INTO daily_research_entries VALUES (?,?,?,?,?,?,?,?,?)", (
                entry_id, symbol, cutoff, utc_now_timestamp(), fingerprint, key,
                canonical_json(payload), sha256_json(payload), snapshot_id))
            return self._decode(conn.execute("SELECT * FROM daily_research_entries WHERE entry_id=?", (entry_id,)).fetchone())

    def history(self, symbol, limit=20):
        parse_canonical_symbol(symbol)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            entries = [self._decode(r) for r in conn.execute(
                "SELECT * FROM daily_research_entries WHERE symbol=? ORDER BY created_at DESC,entry_id DESC LIMIT ?", (symbol, limit))]
        return {"symbol": symbol, "entries": entries,
                "comparison": compare_entries(entries[1] if len(entries) > 1 else None, entries[0]) if entries else None}

    def overview(self, limit=25, after_symbol=""):
        cutoff = utc_now_timestamp()
        with closing(sqlite3.connect(self.db_path)) as conn:
            symbols = [r[0] for r in conn.execute("SELECT symbol FROM research_watchlist_items WHERE membership_state='active' AND symbol>? ORDER BY symbol LIMIT ?", (after_symbol, limit+1))]
        items = []
        for symbol in symbols[:limit]:
            try:
                current = self._payload(symbol, cutoff, "")
                history = self.history(symbol, 1)["entries"]
                previous = history[0] if history else None
            except (ValueError, sqlite3.Error, SnapshotIntegrityError):
                items.append({"symbol": symbol, "current": {"summary": {}, "note": "", "status": "unavailable", "knowledge_cutoff_at": cutoff},
                              "previous": None, "comparison": {"status": "unavailable", "facts": [], "assumptions_changed": False},
                              "reason": "research_item_unavailable"})
                continue
            items.append({"symbol": symbol, "current": current, "previous": previous,
                          "comparison": compare_entries(previous, current)})
        return {"server_time": cutoff, "items": items,
                "next_symbol": symbols[limit-1] if len(symbols) > limit else None}
