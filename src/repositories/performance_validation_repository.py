"""Append-only persistence for Phase 8 profiles, manifests, and evaluations."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.domain.analysis_snapshot import canonical_json, sha256_json
from src.domain.performance_validation import (
    EvaluationProfileApproval,
    EvaluationProfileRevision,
    EvaluationRun,
    EvaluationRunSnapshot,
    OutcomeResourceManifest,
    ScenarioEvaluation,
)
from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


class PerformanceValidationRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _bind(
        conn: sqlite3.Connection,
        key: str,
        fingerprint: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO evaluation_idempotency_keys VALUES (?,?,?,?,?)",
            (key, fingerprint, resource_type, resource_id, utc_now_timestamp()),
        )

    @staticmethod
    def _binding(
        conn: sqlite3.Connection,
        key: str,
        fingerprint: str,
        resource_type: str,
    ) -> sqlite3.Row | None:
        row = conn.execute(
            "SELECT * FROM evaluation_idempotency_keys WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row and (
            row["payload_fingerprint"] != fingerprint
            or row["resource_type"] != resource_type
        ):
            raise ValueError("idempotency key was already used with a different payload")
        return row

    @staticmethod
    def _require_key(key: str) -> str:
        candidate = str(key).strip()
        if not candidate:
            raise ValueError("idempotency_key is required")
        return candidate

    @staticmethod
    def _profile(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["horizons_sessions"] = json.loads(item.pop("horizons_sessions_json"))
        return item

    @staticmethod
    def _manifest(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["calendar_resource"] = json.loads(item.pop("calendar_resource_json"))
        item["benchmark_resource"] = json.loads(item.pop("benchmark_resource_json"))
        item["symbol_resources"] = json.loads(item.pop("symbol_resources_json"))
        return item

    @staticmethod
    def _evaluation(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["subject_metadata"] = json.loads(item.pop("subject_metadata_json"))
        if item["target_reached"] is not None:
            item["target_reached"] = bool(item["target_reached"])
        return item

    @staticmethod
    def _memberships(
        conn: sqlite3.Connection, run_id: str
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM evaluation_run_snapshots WHERE evaluation_run_id = ? "
            "ORDER BY snapshot_id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_profile_revision(
        self,
        revision: EvaluationProfileRevision,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_key(idempotency_key)
        payload = revision.canonical_payload()
        fingerprint = sha256_json(payload)
        record_id = f"evaluation_profile_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(ingested_at or utc_now_timestamp(), "ingested_at")
        if ingested < payload["available_at"]:
            raise ValueError("ingested_at cannot precede profile available_at")
        with self._connect() as conn:
            binding = self._binding(conn, key, fingerprint, "profile_revision")
            if binding:
                row = conn.execute(
                    "SELECT * FROM evaluation_profile_revisions WHERE id = ?",
                    (binding["resource_id"],),
                ).fetchone()
                if row is None:
                    raise RuntimeError("idempotency ledger references a missing profile")
                return {**self._profile(row), "created": False}
            duplicate = conn.execute(
                "SELECT * FROM evaluation_profile_revisions WHERE payload_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if duplicate:
                self._bind(conn, key, fingerprint, "profile_revision", duplicate["id"])
                return {**self._profile(duplicate), "created": False}
            previous = conn.execute(
                "SELECT * FROM evaluation_profile_revisions WHERE logical_profile_id = ? ORDER BY revision_number DESC LIMIT 1",
                (payload["logical_profile_id"],),
            ).fetchone()
            if payload["revision_number"] == 1:
                if previous is not None:
                    raise ValueError("logical evaluation profile already has revision 1")
            else:
                if previous is None or previous["id"] != payload["previous_revision_id"]:
                    raise ValueError("previous_revision_id must reference the latest evaluation profile")
                if payload["revision_number"] != previous["revision_number"] + 1:
                    raise ValueError("evaluation profile revision_number must increment by one")
            conn.execute(
                """
                INSERT INTO evaluation_profile_revisions (
                    id,payload_fingerprint,logical_profile_id,revision_number,
                    previous_revision_id,horizons_sessions_json,start_policy,
                    start_price_policy,end_price_policy,target_touch_policy,
                    already_in_range_policy,benchmark_policy,outcome_completeness_policy,
                    calculation_quantum,display_quantum,available_at,ingested_at,
                    status,created_at,created_by,rationale
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, fingerprint, payload["logical_profile_id"],
                    payload["revision_number"], payload["previous_revision_id"],
                    canonical_json(payload["horizons_sessions"]), payload["start_policy"],
                    payload["start_price_policy"], payload["end_price_policy"],
                    payload["target_touch_policy"], payload["already_in_range_policy"],
                    payload["benchmark_policy"], payload["outcome_completeness_policy"],
                    payload["calculation_quantum"], payload["display_quantum"],
                    payload["available_at"], ingested, payload["status"], ingested,
                    payload["created_by"], payload["rationale"],
                ),
            )
            self._bind(conn, key, fingerprint, "profile_revision", record_id)
            row = conn.execute(
                "SELECT * FROM evaluation_profile_revisions WHERE id = ?", (record_id,)
            ).fetchone()
            return {**self._profile(row), "created": True}

    def add_profile_approval(
        self,
        approval: EvaluationProfileApproval,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_key(idempotency_key)
        payload = approval.canonical_payload()
        fingerprint = sha256_json(payload)
        event_id = f"evaluation_profile_approval_event_{fingerprint[:20]}"
        ingested = normalize_utc_timestamp(ingested_at or utc_now_timestamp(), "ingested_at")
        if ingested < payload["approved_at"]:
            raise ValueError("ingested_at cannot precede approved_at")
        with self._connect() as conn:
            binding = self._binding(conn, key, fingerprint, "profile_approval")
            if binding:
                row = conn.execute(
                    "SELECT * FROM evaluation_profile_approvals WHERE approval_event_id = ?",
                    (binding["resource_id"],),
                ).fetchone()
                if row is None:
                    raise RuntimeError("idempotency ledger references a missing approval")
                return {**dict(row), "created": False}
            duplicate = conn.execute(
                "SELECT * FROM evaluation_profile_approvals WHERE payload_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if duplicate:
                self._bind(conn, key, fingerprint, "profile_approval", duplicate["approval_event_id"])
                return {**dict(duplicate), "created": False}
            profile = conn.execute(
                "SELECT * FROM evaluation_profile_revisions WHERE id = ?",
                (payload["profile_revision_id"],),
            ).fetchone()
            if profile is None:
                raise ValueError("evaluation profile revision does not exist")
            if payload["approved_at"] < profile["available_at"]:
                raise ValueError("approval cannot precede profile available_at")
            if payload["decision"] == ApprovalStatus.APPROVED.value and profile["status"] != "available":
                raise ValueError("a revoked evaluation profile cannot be approved")
            previous = conn.execute(
                "SELECT approved_at FROM evaluation_profile_approvals WHERE profile_revision_id = ? ORDER BY approved_at DESC, ingested_at DESC, approval_event_id DESC LIMIT 1",
                (payload["profile_revision_id"],),
            ).fetchone()
            if previous and payload["approved_at"] < previous["approved_at"]:
                raise ValueError("approval events cannot be backdated")
            conn.execute(
                "INSERT INTO evaluation_profile_approvals VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    event_id, payload["approval_id"], fingerprint,
                    payload["profile_revision_id"], payload["decision"],
                    payload["approved_by"], payload["rationale"],
                    payload["approved_at"], ingested,
                ),
            )
            self._bind(conn, key, fingerprint, "profile_approval", event_id)
            row = conn.execute(
                "SELECT * FROM evaluation_profile_approvals WHERE approval_event_id = ?",
                (event_id,),
            ).fetchone()
            return {**dict(row), "created": True}

    def approved_profile_as_of(
        self, profile_revision_id: str, knowledge_cutoff_at: str
    ) -> dict[str, Any] | None:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            profile = conn.execute(
                "SELECT * FROM evaluation_profile_revisions WHERE id = ? AND available_at <= ? AND ingested_at <= ?",
                (profile_revision_id, cutoff, cutoff),
            ).fetchone()
            if profile is None:
                return None
            approval = conn.execute(
                "SELECT * FROM evaluation_profile_approvals WHERE profile_revision_id = ? AND approved_at <= ? AND ingested_at <= ? ORDER BY approved_at DESC, ingested_at DESC, approval_event_id DESC LIMIT 1",
                (profile_revision_id, cutoff, cutoff),
            ).fetchone()
            item = self._profile(profile)
            item["effective_approval_status"] = approval["decision"] if approval else None
            item["verified_approval_id"] = (
                approval["approval_id"]
                if approval and approval["decision"] == ApprovalStatus.APPROVED.value
                else None
            )
            return item

    def add_manifest(
        self, manifest: OutcomeResourceManifest, idempotency_key: str
    ) -> dict[str, Any]:
        key = self._require_key(idempotency_key)
        payload = manifest.canonical_payload()
        fingerprint = sha256_json(payload)
        with self._connect() as conn:
            binding = self._binding(conn, key, fingerprint, "outcome_manifest")
            if binding:
                row = conn.execute(
                    "SELECT * FROM outcome_resource_manifests WHERE manifest_id = ?",
                    (binding["resource_id"],),
                ).fetchone()
                if row is None:
                    raise RuntimeError("idempotency ledger references a missing manifest")
                return {**self._manifest(row), "created": False}
            duplicate = conn.execute(
                "SELECT * FROM outcome_resource_manifests WHERE payload_fingerprint = ? OR manifest_id = ?",
                (fingerprint, payload["manifest_id"]),
            ).fetchone()
            if duplicate:
                if duplicate["payload_fingerprint"] != fingerprint:
                    raise ValueError("manifest_id is already bound to different content")
                self._bind(conn, key, fingerprint, "outcome_manifest", duplicate["manifest_id"])
                return {**self._manifest(duplicate), "created": False}
            conn.execute(
                """
                INSERT INTO outcome_resource_manifests (
                    manifest_id,payload_fingerprint,manifest_version,dataset_name,
                    provider,dataset_hash,date_start,date_end,universe_definition,
                    calendar_resource_json,calendar_hash,benchmark_resource_json,
                    benchmark_hash,ohlc_adjustment_contract,corporate_action_contract,
                    symbol_resources_json,ingested_at,created_at,
                    outcome_observed_through_session
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload["manifest_id"], fingerprint, payload["manifest_version"],
                    payload["dataset_name"], payload["provider"], payload["dataset_hash"],
                    payload["date_start"], payload["date_end"], payload["universe_definition"],
                    canonical_json(payload["calendar_resource"]), payload["calendar_hash"],
                    canonical_json(payload["benchmark_resource"]), payload["benchmark_hash"],
                    payload["ohlc_adjustment_contract"], payload["corporate_action_contract"],
                    canonical_json(payload["symbol_resources"]), payload["ingested_at"],
                    payload["created_at"], payload["outcome_observed_through_session"],
                ),
            )
            self._bind(conn, key, fingerprint, "outcome_manifest", payload["manifest_id"])
            row = conn.execute(
                "SELECT * FROM outcome_resource_manifests WHERE manifest_id = ?",
                (payload["manifest_id"],),
            ).fetchone()
            return {**self._manifest(row), "created": True}

    def get_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM outcome_resource_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
            return self._manifest(row) if row else None

    def add_run(
        self,
        run: EvaluationRun,
        memberships: list[EvaluationRunSnapshot],
        evaluations: list[ScenarioEvaluation],
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = self._require_key(idempotency_key)
        semantic = run.semantic_payload()
        fingerprint = sha256_json(semantic)
        run_id = f"evaluation_run_{fingerprint[:24]}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            binding = self._binding(conn, key, fingerprint, "evaluation_run")
            if binding:
                row = conn.execute(
                    "SELECT * FROM evaluation_runs WHERE evaluation_run_id = ?",
                    (binding["resource_id"],),
                ).fetchone()
                if row is None:
                    raise RuntimeError("idempotency ledger references a missing evaluation run")
                return {
                    **dict(row), "created": False,
                    "snapshot_memberships": self._memberships(conn, row["evaluation_run_id"]),
                    "results": self._results(conn, row["evaluation_run_id"]),
                }
            duplicate = conn.execute(
                "SELECT * FROM evaluation_runs WHERE run_fingerprint = ?", (fingerprint,)
            ).fetchone()
            if duplicate:
                self._bind(conn, key, fingerprint, "evaluation_run", duplicate["evaluation_run_id"])
                return {
                    **dict(duplicate), "created": False,
                    "snapshot_memberships": self._memberships(
                        conn, duplicate["evaluation_run_id"]
                    ),
                    "results": self._results(conn, duplicate["evaluation_run_id"]),
                }
            profile = conn.execute(
                "SELECT id FROM evaluation_profile_revisions WHERE id = ?",
                (semantic["evaluation_profile_revision_id"],),
            ).fetchone()
            manifest = conn.execute(
                "SELECT manifest_id,dataset_hash FROM outcome_resource_manifests WHERE manifest_id = ?",
                (semantic["outcome_resource_manifest_id"],),
            ).fetchone()
            if profile is None or manifest is None:
                raise ValueError("evaluation profile and outcome manifest must exist")
            if manifest["dataset_hash"] != semantic["outcome_manifest_hash"]:
                raise ValueError("outcome manifest hash does not match stored manifest")
            conn.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, semantic["evaluation_profile_revision_id"],
                    semantic["evaluator_version"], semantic["evaluation_origin_policy"],
                    semantic["snapshot_set_hash"], semantic["outcome_resource_manifest_id"],
                    semantic["outcome_manifest_hash"], semantic["universe_definition"],
                    normalize_utc_timestamp(run.created_at, "created_at"), fingerprint,
                    semantic["status"],
                ),
            )
            membership_payloads = [item.canonical_payload() for item in memberships]
            membership_by_snapshot = {
                item["snapshot_id"]: item for item in membership_payloads
            }
            if not membership_payloads or len(membership_by_snapshot) != len(membership_payloads):
                raise ValueError("evaluation run memberships must be non-empty and unique")
            evaluation_snapshot_ids = {
                item.snapshot_id for item in evaluations
            }
            if not evaluation_snapshot_ids.issubset(membership_by_snapshot):
                raise ValueError("scenario evaluation snapshot is missing run membership")
            for snapshot_id, membership in membership_by_snapshot.items():
                snapshot_row = conn.execute(
                    "SELECT symbol FROM analysis_snapshots WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()
                if snapshot_row is None or snapshot_row["symbol"] != membership["symbol"]:
                    raise ValueError("run membership must reference the exact snapshot symbol")
                has_evaluations = snapshot_id in evaluation_snapshot_ids
                expected_status = "evaluated" if has_evaluations else "no_eligible_subjects"
                if membership["membership_status"] != expected_status:
                    raise ValueError(
                        "completed run membership status must match eligible evaluations"
                    )
                conn.execute(
                    "INSERT INTO evaluation_run_snapshots VALUES (?,?,?,?,?,?)",
                    (
                        run_id, snapshot_id, membership["symbol"],
                        membership["membership_status"], membership["reason"],
                        membership["created_at"],
                    ),
                )
            for evaluation in evaluations:
                payload = evaluation.canonical_payload()
                if payload["outcome_resource_manifest_id"] != semantic["outcome_resource_manifest_id"]:
                    raise ValueError("scenario evaluation manifest must match its run")
                evaluation_semantic = evaluation.canonical_payload()
                evaluation_semantic.pop("created_at")
                calc = sha256_json({
                    "run_fingerprint": fingerprint,
                    "evaluation": evaluation_semantic,
                })
                evaluation_id = f"scenario_evaluation_{calc[:24]}"
                conn.execute(
                    """
                    INSERT INTO scenario_evaluations VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        evaluation_id, run_id, payload["snapshot_id"], payload["symbol"],
                        payload["evaluation_origin"], payload["subject_type"], payload["subject_id"],
                        payload["method_family"], payload["semantic_role"], payload["evidence_strength"],
                        canonical_json(payload["subject_metadata"]), payload["knowledge_cutoff_at"],
                        payload["horizon_sessions"], payload["evaluation_start_session"],
                        payload["evaluation_end_session"], payload["market_sessions_skipped"],
                        payload["start_price"], payload["target_low"], payload["target_high"],
                        payload["target_position_at_start"],
                        None if payload["target_reached"] is None else int(payload["target_reached"]),
                        payload["first_target_reached_at"], payload["trading_sessions_to_target"],
                        payload["maximum_upside_excursion"], payload["maximum_downside_excursion"],
                        payload["directional_mfe"], payload["directional_mae"],
                        payload["forward_return"], payload["benchmark_return"], payload["excess_return"],
                        payload["terminal_outcome"], payload["quality_status"], payload["benchmark_status"],
                        payload["invalidation_status"], payload["invalidation_reason"],
                        payload["outcome_resource_manifest_id"], calc, payload["created_at"],
                    ),
                )
            self._bind(conn, key, fingerprint, "evaluation_run", run_id)
            row = conn.execute(
                "SELECT * FROM evaluation_runs WHERE evaluation_run_id = ?", (run_id,)
            ).fetchone()
            return {
                **dict(row), "created": True,
                "snapshot_memberships": self._memberships(conn, run_id),
                "results": self._results(conn, run_id),
            }

    def _results(self, conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM scenario_evaluations WHERE evaluation_run_id = ? ORDER BY snapshot_id, subject_type, subject_id, horizon_sessions",
            (run_id,),
        ).fetchall()
        return [self._evaluation(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evaluation_runs WHERE evaluation_run_id = ?", (run_id,)
            ).fetchone()
            return {
                **dict(row),
                "snapshot_memberships": self._memberships(conn, run_id),
                "results": self._results(conn, run_id),
            } if row else None

    def memberships_for_run(self, run_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM evaluation_runs WHERE evaluation_run_id = ?", (run_id,)
            ).fetchone()
            return self._memberships(conn, run_id) if exists else None

    def results_for_run(self, run_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM evaluation_runs WHERE evaluation_run_id = ?", (run_id,)
            ).fetchone()
            return self._results(conn, run_id) if exists else None

    def results_for_snapshot(self, snapshot_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scenario_evaluations WHERE snapshot_id = ? ORDER BY created_at, horizon_sessions, subject_type, subject_id",
                (snapshot_id,),
            ).fetchall()
            return [self._evaluation(row) for row in rows]
