"""Installed local commands; existing domain services retain all approval rules."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing

from src.domain.analysis_snapshot import canonical_json
from src.domain.technical_anchor import AnchorPoint, AnchorRole, ManualAnchorSetRevision
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import (
    ApprovalResourceType, ApprovalStatus, ForwardEPSObservation, ForwardEPSSourceType,
    PEScenario, PEScope, utc_now_timestamp,
)
from src.engine.fibonacci_scenarios import calculate_equal_amplitude, calculate_retracement_0382
from src.services.forward_eps_service import ForwardEPSService
from src.services.technical_scenario_service import TechnicalScenarioService

_COMMAND_LOCK = threading.RLock()
_TABLES = {"eps": "forward_eps_observations", "pe": "pe_scenarios", "anchor": "technical_anchor_revisions"}


class LocalAssumptionService:
    def __init__(self, db_path):
        self.db_path = db_path
        self.valuation = ForwardEPSService(db_path, auto_migrate=False)
        self.technical = TechnicalScenarioService(db_path, auto_migrate=False)

    def _resource(self, symbol, kind, resource_id):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(f"SELECT * FROM {_TABLES[kind]} WHERE id=? AND symbol=?", (resource_id, symbol)).fetchone()
            if row is None:
                raise ValueError("assumption_not_found_for_symbol")
            return dict(row)

    def list(self, symbol):
        parse_canonical_symbol(symbol)
        result = []
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            for kind, table in _TABLES.items():
                series = "logical_anchor_set_id" if kind == "anchor" else "logical_series_id"
                for row in conn.execute(f"SELECT * FROM {table} WHERE symbol=? ORDER BY available_at DESC, revision_number DESC", (symbol,)):
                    item = dict(row)
                    newer = conn.execute(f"SELECT 1 FROM {table} WHERE {series}=? AND revision_number>?", (item[series], item["revision_number"])).fetchone()
                    if kind == "anchor":
                        decision = conn.execute("SELECT decision,approval_id,approved_at FROM technical_anchor_approvals WHERE anchor_revision_id=? ORDER BY approved_at DESC,ingested_at DESC LIMIT 1", (item["id"],)).fetchone()
                        item["anchors"] = json.loads(item.pop("anchors_json"))
                    else:
                        decision = conn.execute("SELECT decision,approval_id,available_at FROM valuation_approvals WHERE resource_type=? AND resource_id=? ORDER BY available_at DESC,ingested_at DESC,approval_event_id DESC LIMIT 1", ("forward_eps" if kind == "eps" else "pe_scenario", item["id"])).fetchone()
                    item.update(kind=kind, superseded=bool(newer), approval=dict(decision) if decision else None)
                    for private in ("idempotency_key", "payload_fingerprint"):
                        item.pop(private, None)
                    result.append(item)
        return {"symbol": symbol, "server_time": utc_now_timestamp(), "items": result}

    def _draft(self, symbol, kind, values, timestamp, key, previous_id=None):
        previous = self._resource(symbol, kind, previous_id) if previous_id else None
        series_field = "logical_anchor_set_id" if kind == "anchor" else "logical_series_id"
        series = previous[series_field] if previous else "local_" + hashlib.sha256(key.encode()).hexdigest()[:24]
        common = dict(revision_number=previous["revision_number"] + 1 if previous else 1,
                      revision_of=previous_id, symbol=symbol, available_at=timestamp)
        if kind == "eps":
            obj = ForwardEPSObservation(logical_series_id=series, source_type=ForwardEPSSourceType.MANUAL,
                fiscal_year=values["fiscal_year"], eps_base=values["eps_base"],
                source_name=values["source"], published_at=values["source_date"],
                quality_note=values["rationale"], **common)
        elif kind == "pe":
            obj = PEScenario(logical_series_id=series, label=values["label"], pe_value=values["pe_value"],
                rationale=values["rationale"], evidence_level="U", scope=PEScope.SYMBOL,
                approval_status=ApprovalStatus.DRAFT, **common)
        else:
            obj = ManualAnchorSetRevision(logical_anchor_set_id=series,
                evidence_basis_rule_id=values["rule_id"],
                anchors=tuple(AnchorPoint(AnchorRole(p["role"]), p["price"], p["market_date"]) for p in values["anchors"]),
                created_by="local_user", source=values["source"], source_note=values["rationale"], **common)
        obj.canonical_payload()
        return obj

    def preview(self, symbol, kind, values, previous_id=None):
        parse_canonical_symbol(symbol)
        obj = self._draft(symbol, kind, values, utc_now_timestamp(), "preview", previous_id)
        payload = obj.canonical_payload()
        calculation = None
        if kind == "anchor":
            p = {a["role"]: a["price"] for a in payload["anchors"]}
            calculation = (calculate_equal_amplitude(p["origin"], p["swing_end"], p["projection_origin"])
                           if payload["evidence_basis_rule_id"] == "FB-03"
                           else calculate_retracement_0382(p["origin"], p["swing_end"]))
        return {"status": "preview_only", "inputs": payload, "calculation": calculation,
                "approval_required": True, "official_affiliation": False}

    def execute(self, symbol, kind, action, values, key, previous_id=None, resource_id=None):
        parse_canonical_symbol(symbol)
        if kind not in _TABLES or action not in {"draft", "approve", "revoke"}:
            raise ValueError("unsupported_assumption_command")
        if not key or not 8 <= len(key) <= 128:
            raise ValueError("idempotency_key_required")
        request = canonical_json(dict(symbol=symbol, kind=kind, action=action, values=values,
                                      previous_id=previous_id, resource_id=resource_id))
        # The installed product has one writer process. Persist the first accepted
        # timestamp before calling existing repositories so interrupted retries use
        # their original identity rather than creating a backdated/new approval.
        with _COMMAND_LOCK:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT request_json,accepted_at,result_json FROM local_research_commands WHERE command_key=?", (key,)).fetchone()
                if row:
                    if row[0] != request:
                        raise ValueError("research_idempotency_conflict")
                    if row[2]:
                        return json.loads(row[2])
                    timestamp = row[1]
                else:
                    timestamp = utc_now_timestamp()
                    conn.execute("INSERT INTO local_research_commands VALUES (?,?,?,NULL)", (key, request, timestamp))
            operation_key = "local:" + key
            if action == "draft":
                obj = self._draft(symbol, kind, values, timestamp, key, previous_id)
                result = (self.valuation.ingest_forward_eps(obj, operation_key) if kind == "eps" else
                          self.valuation.ingest_pe_scenario(obj, operation_key) if kind == "pe" else
                          self.technical.ingest(obj, operation_key))
            else:
                resource = self._resource(symbol, kind, resource_id)
                decision = ApprovalStatus.APPROVED if action == "approve" else ApprovalStatus.REVOKED
                if kind == "anchor":
                    result = self.technical.record_approval(anchor_revision_id=resource_id, decision=decision,
                        rule_id=resource["evidence_basis_rule_id"], rationale=values["rationale"],
                        approved_at=timestamp, approved_by="local_user", idempotency_key=operation_key)
                else:
                    result = self.valuation.record_approval(
                        resource_type=ApprovalResourceType.FORWARD_EPS if kind == "eps" else ApprovalResourceType.PE_SCENARIO,
                        resource_id=resource_id, decision=decision, rule_id="VAL-02" if kind == "eps" else "VAL-04",
                        rationale=values["rationale"], available_at=timestamp, approved_by="local_user", idempotency_key=operation_key)
            for private in ("idempotency_key", "payload_fingerprint", "created"):
                result.pop(private, None)
            response = {"status": "draft" if action == "draft" else decision.value,
                        "kind": kind, "record": result, "accepted_at": timestamp}
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute("UPDATE local_research_commands SET result_json=? WHERE command_key=? AND result_json IS NULL", (canonical_json(response), key))
            return response
