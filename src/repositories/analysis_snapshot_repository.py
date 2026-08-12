"""Append-only storage for exact Phase 7 analysis outputs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.domain.analysis_snapshot import AnalysisSnapshot, canonical_json, sha256_json
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


class SnapshotIntegrityError(RuntimeError):
    """Persisted snapshot output no longer matches its stored SHA-256."""


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
        semantic_output = dict(semantic_payload["output"])
        semantic_output["snapshot_id"] = None
        semantic_payload["output"] = semantic_output
        fingerprint = sha256_json(semantic_payload)
        snapshot_id = f"analysis_snapshot_{fingerprint[:24]}"
        stored_output = dict(payload["output"])
        stored_output["snapshot_id"] = snapshot_id
        payload["output"] = stored_output
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

    def get_with_connection(
        self, conn: sqlite3.Connection, snapshot_id: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM analysis_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        result = self._decode(row)
        if sha256_json(result["output"]) != result["output_sha256"]:
            raise SnapshotIntegrityError(
                "stored analysis snapshot output failed integrity verification"
            )
        return result

    def get(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self.get_with_connection(conn, snapshot_id)

    @staticmethod
    def _decode_before(before: str | None) -> tuple[str, str] | None:
        if before is None:
            return None
        created_at, separator, snapshot_id = before.strip().partition("|")
        if not separator or not created_at or not snapshot_id:
            raise ValueError("before must be '<created_at>|<snapshot_id>'")
        return normalize_utc_timestamp(created_at, "before.created_at"), snapshot_id

    def list_summaries(
        self,
        *,
        symbol: str | None = None,
        capture_mode: str | None = None,
        before: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return deterministic immutable snapshot metadata without duplicating outputs."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        cursor = self._decode_before(before)
        clauses: list[str] = []
        parameters: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        if capture_mode is not None:
            clauses.append("capture_mode = ?")
            parameters.append(capture_mode)
        if cursor is not None:
            clauses.append(
                "(created_at < ? OR (created_at = ? AND snapshot_id < ?))"
            )
            parameters.extend([cursor[0], cursor[0], cursor[1]])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit + 1)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT snapshot_id,symbol,knowledge_cutoff_at,capture_mode,
                       model_version,created_at,supersedes_snapshot_id,
                       output_json,output_sha256
                FROM analysis_snapshots
                {where}
                ORDER BY created_at DESC, snapshot_id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        summaries = []
        for row in visible:
            output = json.loads(row["output_json"])
            if sha256_json(output) != row["output_sha256"]:
                raise SnapshotIntegrityError(
                    "stored analysis snapshot output failed integrity verification"
                )
            summaries.append({
                "snapshot_id": row["snapshot_id"],
                "symbol": row["symbol"],
                "knowledge_cutoff_at": row["knowledge_cutoff_at"],
                "capture_mode": row["capture_mode"],
                "model_version": row["model_version"],
                "created_at": row["created_at"],
                "supersedes_snapshot_id": row["supersedes_snapshot_id"],
                "analysis_status": output.get("status"),
            })
        next_before = None
        if has_more and summaries:
            last = summaries[-1]
            next_before = f"{last['created_at']}|{last['snapshot_id']}"
        return {
            "items": summaries,
            "next_before": next_before,
        }
