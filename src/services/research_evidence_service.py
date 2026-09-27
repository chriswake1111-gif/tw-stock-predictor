"""Bounded, append-only local research evidence; no networking or model writes."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.domain.analysis_snapshot import canonical_json, sha256_json
from src.domain.research_evidence import EvidenceInput, GUIDANCE_CONTRACT
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp

MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


def guidance_enabled():
    return os.environ.get("RESEARCH_GUIDANCE_ENABLED", "true").strip().lower() == "true"


class ResearchEvidenceService:
    def __init__(self, db_path):
        self.db_path = db_path

    @staticmethod
    def decode(row):
        payload = json.loads(row["payload_json"])
        if sha256_json(payload) != row["content_sha256"]:
            raise ValueError("research_evidence_integrity_error")
        return {"record_id": row["record_id"], "series_id": row["series_id"],
                "revision": row["revision"], "recorded_at": row["recorded_at"],
                "content_sha256": row["content_sha256"], "symbol": row["symbol"], **payload}

    def append(self, symbol, payload, key):
        parse_canonical_symbol(symbol)
        if not guidance_enabled():
            raise ValueError("research_guidance_disabled")
        if not 8 <= len(key) <= 128:
            raise ValueError("idempotency_key_required")
        item = EvidenceInput.model_validate(payload)
        data = item.model_dump(mode="json")
        serialized = canonical_json(data)
        payload_size = len(serialized.encode("utf-8"))
        if payload_size > 16 * 1024:
            raise ValueError("evidence_record_too_large")
        # Logical quota includes a conservative row/index allocation. SQLite's
        # shared database/WAL file size is not this isolated area's byte usage.
        size = payload_size + 4096
        fingerprint = sha256_json(data)
        from src.services.local_assumption_service import _COMMAND_LOCK
        with _COMMAND_LOCK, closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT r.* FROM research_evidence_records r JOIN research_evidence_requests k ON k.record_id=r.record_id WHERE k.request_key=?", (key,)).fetchone()
            if existing:
                if existing["symbol"] != symbol or existing["content_sha256"] != fingerprint:
                    raise ValueError("research_idempotency_conflict")
                return self.decode(existing)
            used = conn.execute("SELECT COALESCE(SUM(payload_bytes),0) FROM research_evidence_records").fetchone()[0]
            used += conn.execute("SELECT COALESCE(SUM(length(CAST(request_key AS BLOB))+length(record_id)+512),0) FROM research_evidence_requests").fetchone()[0]
            request_size = len(key.encode("utf-8")) + 41 + 512
            existing = conn.execute("SELECT * FROM research_evidence_records WHERE symbol=? AND content_sha256=?", (symbol, fingerprint)).fetchone()
            if existing:
                if used + request_size > MAX_EVIDENCE_BYTES:
                    raise ValueError("research_evidence_storage_full")
                conn.execute("INSERT INTO research_evidence_requests VALUES (?,?)", (key, existing["record_id"]))
                return self.decode(existing)
            series, revision = "evidence_series_" + uuid4().hex, 1
            if item.previous_id:
                previous = conn.execute("SELECT * FROM research_evidence_records WHERE record_id=? AND symbol=?", (item.previous_id, symbol)).fetchone()
                if not previous:
                    raise ValueError("evidence_previous_not_found")
                old = self.decode(previous)
                if (old["kind"], old["topic"]) != (item.kind, item.topic):
                    raise ValueError("evidence_revision_kind_mismatch")
                if conn.execute("SELECT 1 FROM research_evidence_records WHERE previous_id=?", (item.previous_id,)).fetchone():
                    raise ValueError("research_evidence_revision_conflict")
                series, revision = old["series_id"], old["revision"] + 1
            if used + size + request_size > MAX_EVIDENCE_BYTES:
                raise ValueError("research_evidence_storage_full")
            record_id = "evidence_" + uuid4().hex
            conn.execute("INSERT INTO research_evidence_records VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (record_id, symbol, series, revision, item.previous_id, utc_now_timestamp(),
                          serialized, fingerprint, key, size))
            conn.execute("INSERT INTO research_evidence_requests VALUES (?,?)", (key, record_id))
            return self.decode(conn.execute("SELECT * FROM research_evidence_records WHERE record_id=?", (record_id,)).fetchone())

    def list(self, symbol, *, cutoff=None, include_history=False, limit=100, before=None):
        parse_canonical_symbol(symbol)
        cutoff = normalize_utc_timestamp(cutoff or utc_now_timestamp(), "cutoff")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            history = "" if include_history else "AND NOT EXISTS (SELECT 1 FROM research_evidence_records n WHERE n.series_id=r.series_id AND n.revision>r.revision AND n.recorded_at<=?)"
            params = [symbol, cutoff] + ([] if include_history else [cutoff])
            cursor = ""
            if before:
                row = conn.execute("SELECT recorded_at,record_id FROM research_evidence_records WHERE record_id=? AND symbol=?", (before, symbol)).fetchone()
                if row is None:
                    raise ValueError("evidence_cursor_invalid")
                cursor = " AND (r.recorded_at,r.record_id)<(?,?)"
                params.extend(row)
            rows = conn.execute(f"SELECT r.* FROM research_evidence_records r WHERE symbol=? AND recorded_at<=? {history}{cursor} ORDER BY recorded_at DESC, record_id DESC LIMIT ?", (*params, limit + 1)).fetchall()
            items = [self.decode(r) for r in rows[:limit]]
        return {"contract_version": GUIDANCE_CONTRACT, "symbol": symbol, "items": items,
                "next_cursor": items[-1]["record_id"] if len(rows) > limit else None}

    def get(self, symbol, record_id, *, require_current=False):
        parse_canonical_symbol(symbol)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM research_evidence_records WHERE record_id=? AND symbol=?", (record_id, symbol)).fetchone()
            if row is None:
                raise ValueError("evidence_not_found_for_symbol")
            if require_current and conn.execute("SELECT 1 FROM research_evidence_records WHERE series_id=? AND revision>?", (row["series_id"], row["revision"])).fetchone():
                raise ValueError("research_evidence_changed_review_again")
            return self.decode(row)

    def candidate_values(self, symbol, record_id):
        item = self.get(symbol, record_id, require_current=True)
        return self.prepare_candidate(item)

    @staticmethod
    def prepare_candidate(item):
        EvidenceInput.model_validate({k: v for k, v in item.items() if k in EvidenceInput.model_fields})
        record_id = item["record_id"]
        if item["kind"] != "candidate" or item["review_status"] not in {"reviewable", "limited"}:
            raise ValueError("evidence_not_selectable")
        if item["published_date"] > datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat():
            raise ValueError("candidate_not_yet_published")
        source = f'{item["publisher"]}；{item["title"]}；{item["published_date"]}；{item["source_url"]}；{item["locator"]}；來源類別：{item["source_type"]}；閱讀範圍：{item["reading_scope"]}'
        rationale = (f'來源說法：{item["summary"]}；助理整理：{item["interpretation"]}；口徑：{item["basis"]}；'
                     f'限制：{item["limitations"]}；重查：{item["recheck_when"]}；查證版本：{record_id}')
        kind = item["topic"]
        if kind == "eps":
            values = dict(fiscal_year=item["fiscal_year"], eps_base=item["value"], source=source,
                          source_date=item["published_date"], rationale=rationale)
        elif kind == "pe":
            values = dict(fiscal_year=item["fiscal_year"], label=item["title"], pe_value=item["value"], rationale=source + "；" + rationale)
        else:
            values = dict(rule_id=item["rule_id"], anchors=item["anchors"], source=source, rationale=rationale)
        if any(len(v) > 1000 for v in values.values() if isinstance(v, str)):
            raise ValueError("candidate_requires_concise_evidence_revision")
        if kind == "anchor":
            from src.domain.technical_anchor import AnchorPoint, AnchorRole, ManualAnchorSetRevision
            ManualAnchorSetRevision(logical_anchor_set_id="validation", symbol=item["symbol"],
                revision_number=1, available_at=utc_now_timestamp(), created_by="validation",
                evidence_basis_rule_id=item["rule_id"], source=source, source_note=rationale,
                anchors=tuple(AnchorPoint(AnchorRole(p["role"]), p["price"], p["market_date"]) for p in item["anchors"])).canonical_payload()
        return kind, values

    def guidance_evidence(self, symbol, cutoff=None):
        """Read current versions completely; retain only concise references in saved context."""
        page = self.list(symbol, cutoff=cutoff)
        items = list(page["items"])
        cursor = page["next_cursor"]
        while cursor:
            more = self.list(symbol, cutoff=cutoff, before=cursor)
            items.extend(more["items"])
            cursor = more["next_cursor"]
        page["all_items"] = items
        return page

    def lookup_reuse(self, symbol, fiscal_year, scope, *, force=False, new_information=False):
        # A scheduling hint, never a claim that evidence or approval is current.
        if force or new_information:
            return {"reuse": False, "reason": "explicit_recheck"}
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        cursor = None
        while True:
            page = self.list(symbol, before=cursor)
            for item in page["items"]:
                if datetime.fromisoformat(item["recorded_at"].replace("Z", "+00:00")) < since:
                    return {"reuse": False, "reason": "no_recent_matching_lookup"}
                if (item["kind"] == "lookup" and item["fiscal_year"] == fiscal_year
                        and item["lookup_scope"] == scope and item["lookup_outcome"] == "no_qualified_source"):
                    return {"reuse": True, "reason": "no_qualified_source_within_24h", "record_id": item["record_id"], "checked_at": item["recorded_at"]}
            cursor = page["next_cursor"]
            if not cursor:
                return {"reuse": False, "reason": "no_recent_matching_lookup"}
