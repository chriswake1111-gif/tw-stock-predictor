"""Append-only storage for exact Phase 7 analysis outputs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.domain.analysis_snapshot import AnalysisSnapshot, canonical_json, sha256_json
from src.domain.valuation import utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


class AnalysisSnapshotRepository:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["used_rule_versions"] = json.loads(item.pop("used_rule_versions_json"))
        item["source_resource_versions"] = json.loads(
            item.pop("source_resource_versions_json")
        )
        item["manual_approval_ids"] = json.loads(item.pop("manual_approval_ids_json"))
        item["output"] = json.loads(item.pop("output_json"))
        return item

    def add(self, snapshot: AnalysisSnapshot, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = snapshot.canonical_payload()
        semantic_payload = dict(payload)
        semantic_payload.pop("created_at")
        fingerprint = sha256_json(semantic_payload)
        snapshot_id = f"analysis_snapshot_{fingerprint[:24]}"
        output_sha256 = sha256_json(payload["output"])
        with self._connect() as conn:
            binding = conn.execute(
                "SELECT * FROM analysis_snapshot_idempotency_keys WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if binding:
                if binding["payload_fingerprint"] != fingerprint:
                    raise ValueError("idempotency key was already used with a different payload")
                row = conn.execute(
                    "SELECT * FROM analysis_snapshots WHERE snapshot_id = ?",
                    (binding["snapshot_id"],),
                ).fetchone()
                if row is None:
                    raise RuntimeError("snapshot idempotency ledger references a missing snapshot")
                return {**self._decode(row), "created": False}
            duplicate = conn.execute(
                "SELECT * FROM analysis_snapshots WHERE payload_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if duplicate:
                conn.execute(
                    "INSERT INTO analysis_snapshot_idempotency_keys VALUES (?,?,?,?)",
                    (idempotency_key, fingerprint, duplicate["snapshot_id"], utc_now_timestamp()),
                )
                return {**self._decode(duplicate), "created": False}
            if payload["supersedes_snapshot_id"]:
                parent = conn.execute(
                    "SELECT symbol FROM analysis_snapshots WHERE snapshot_id = ?",
                    (payload["supersedes_snapshot_id"],),
                ).fetchone()
                if parent is None:
                    raise ValueError("supersedes_snapshot_id does not exist")
                if parent["symbol"] != payload["symbol"]:
                    raise ValueError("superseded snapshot must have the same symbol")
            conn.execute(
                """
                INSERT INTO analysis_snapshots (
                    snapshot_id,payload_fingerprint,symbol,knowledge_cutoff_at,
                    capture_mode,model_version,synthesis_profile_revision_id,
                    synthesis_profile_approval_id,used_rule_versions_json,
                    source_resource_versions_json,manual_approval_ids_json,
                    output_json,output_sha256,created_at,supersedes_snapshot_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id,
                    fingerprint,
                    payload["symbol"],
                    payload["knowledge_cutoff_at"],
                    payload["capture_mode"],
                    payload["model_version"],
                    payload["synthesis_profile_revision_id"],
                    payload["synthesis_profile_approval_id"],
                    canonical_json(payload["used_rule_versions"]),
                    canonical_json(payload["source_resource_versions"]),
                    canonical_json(payload["manual_approval_ids"]),
                    canonical_json(payload["output"]),
                    output_sha256,
                    payload["created_at"],
                    payload["supersedes_snapshot_id"],
                ),
            )
            conn.execute(
                "INSERT INTO analysis_snapshot_idempotency_keys VALUES (?,?,?,?)",
                (idempotency_key, fingerprint, snapshot_id, utc_now_timestamp()),
            )
            row = conn.execute(
                "SELECT * FROM analysis_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            assert row is not None
            return {**self._decode(row), "created": True}

    def get(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            return None
        result = self._decode(row)
        if sha256_json(result["output"]) != result["output_sha256"]:
            raise RuntimeError("stored analysis snapshot output failed integrity verification")
        return result
