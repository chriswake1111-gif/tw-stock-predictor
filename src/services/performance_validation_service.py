"""Phase 8 orchestration and descriptive historical summaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from statistics import median
from typing import Any

from src.domain.analysis_snapshot import sha256_json
from src.domain.performance_validation import (
    EVALUATOR_VERSION,
    EvaluationProfileApproval,
    EvaluationProfileRevision,
    EvaluationMembershipStatus,
    EvaluationRun,
    EvaluationRunSnapshot,
)
from src.domain.valuation import ApprovalStatus, utc_now_timestamp
from src.engine.historical_scenario_evaluator import HistoricalScenarioEvaluator
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.performance_validation_repository import PerformanceValidationRepository
from src.services.fixed_outcome_dataset import FixedPhase2OutcomeAdapter, PHASE2_UNIVERSE


PROFILE_LOGICAL_ID = "phase8-historical-scenario-mvp"
PROFILE_AVAILABLE_AT = "2026-08-09T16:00:00Z"
PROFILE_ACKNOWLEDGEMENT = "phase8_mvp_v1"


class PerformanceValidationService:
    def __init__(self, db_path: str):
        self.repository = PerformanceValidationRepository(db_path)
        self.snapshot_repository = AnalysisSnapshotRepository(db_path)
        self.evaluator = HistoricalScenarioEvaluator()
        self.outcome_adapter = FixedPhase2OutcomeAdapter()

    def _approved_profile(self, *, actor: str, idempotency_key: str) -> dict[str, Any]:
        profile = self.repository.add_profile_revision(
            EvaluationProfileRevision(
                logical_profile_id=PROFILE_LOGICAL_ID,
                revision_number=1,
                horizons_sessions=(20, 60),
                available_at=PROFILE_AVAILABLE_AT,
                created_by="phase8_authorized_bootstrap",
                rationale=(
                    "Explicit Phase 8 constrained MVP operational profile approved by the "
                    "implementation authorization; not an evidence-grade rule."
                ),
            ),
            f"profile:{idempotency_key}",
            ingested_at=PROFILE_AVAILABLE_AT,
        )
        self.repository.add_profile_approval(
            EvaluationProfileApproval(
                approval_id=f"phase8_profile_approval_{sha256_json({'profile': profile['id'], 'actor': actor})[:20]}",
                profile_revision_id=profile["id"],
                decision=ApprovalStatus.APPROVED,
                approved_by=actor,
                rationale="Protected admin acknowledged the explicit Phase 8 MVP profile.",
                approved_at=PROFILE_AVAILABLE_AT,
            ),
            f"profile-approval:{idempotency_key}",
            ingested_at=PROFILE_AVAILABLE_AT,
        )
        approved = self.repository.approved_profile_as_of(profile["id"], utc_now_timestamp())
        if approved is None or approved["verified_approval_id"] is None:
            raise ValueError("evaluation_profile_needs_human_approval")
        return approved

    def create_run(
        self,
        *,
        snapshot_ids: list[str],
        profile_acknowledgement: str,
        actor: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if profile_acknowledgement != PROFILE_ACKNOWLEDGEMENT:
            raise ValueError("phase8_evaluation_profile_acknowledgement_required")
        normalized_ids = sorted(set(item.strip() for item in snapshot_ids if item.strip()))
        if not normalized_ids or len(normalized_ids) != len(snapshot_ids):
            raise ValueError("snapshot_ids must be non-empty and unique")
        profile = self._approved_profile(actor=actor, idempotency_key=idempotency_key)
        dataset = self.outcome_adapter.load(ingested_at=PROFILE_AVAILABLE_AT)
        manifest = self.repository.add_manifest(
            dataset.manifest, f"outcome-manifest:{idempotency_key}"
        )
        snapshots = []
        for snapshot_id in normalized_ids:
            snapshot = self.snapshot_repository.get(snapshot_id)
            if snapshot is None:
                raise ValueError(f"analysis_snapshot_not_found: {snapshot_id}")
            if snapshot["symbol"] not in PHASE2_UNIVERSE:
                raise ValueError(f"snapshot_symbol_outside_phase2_cohort: {snapshot['symbol']}")
            snapshots.append(snapshot)
        snapshot_set_hash = sha256_json([
            {"snapshot_id": item["snapshot_id"], "output_sha256": item["output_sha256"]}
            for item in snapshots
        ])
        created_at = utc_now_timestamp()
        evaluations = []
        memberships = []
        for snapshot in snapshots:
            snapshot_results = self.evaluator.evaluate(
                snapshot=snapshot, profile=profile, dataset=dataset, created_at=created_at
            )
            evaluations.extend(snapshot_results)
            has_eligible_subjects = bool(snapshot_results)
            memberships.append(EvaluationRunSnapshot(
                snapshot_id=snapshot["snapshot_id"],
                symbol=snapshot["symbol"],
                membership_status=(
                    EvaluationMembershipStatus.EVALUATED
                    if has_eligible_subjects
                    else EvaluationMembershipStatus.NO_ELIGIBLE_SUBJECTS
                ),
                reason=(
                    None
                    if has_eligible_subjects
                    else "stored_snapshot_has_no_phase8_eligible_subjects"
                ),
                created_at=created_at,
            ))
        run = self.repository.add_run(
            EvaluationRun(
                evaluation_profile_revision_id=profile["id"],
                evaluator_version=EVALUATOR_VERSION,
                evaluation_origin_policy="separate_by_evaluation_origin",
                snapshot_set_hash=snapshot_set_hash,
                outcome_resource_manifest_id=manifest["manifest_id"],
                outcome_manifest_hash=manifest["dataset_hash"],
                universe_definition="phase2_fixed_14_symbol_research_cohort",
                created_at=created_at,
            ),
            memberships,
            evaluations,
            idempotency_key,
        )
        return {
            **run,
            "evaluation_profile": {
                "profile_revision_id": profile["id"],
                "approval_id": profile["verified_approval_id"],
                "acknowledgement": PROFILE_ACKNOWLEDGEMENT,
                "horizons_sessions": profile["horizons_sessions"],
            },
            "outcome_manifest": {
                "manifest_id": manifest["manifest_id"],
                "dataset_hash": manifest["dataset_hash"],
            },
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_run(run_id)

    def results_for_run(self, run_id: str) -> list[dict[str, Any]] | None:
        return self.repository.results_for_run(run_id)

    def results_for_snapshot(self, snapshot_id: str) -> list[dict[str, Any]]:
        return self.repository.results_for_snapshot(snapshot_id)

    @staticmethod
    def _median(rows: list[dict[str, Any]], field: str) -> str | None:
        values = [Decimal(row[field]) for row in rows if row.get(field) is not None]
        if not values:
            return None
        return format(Decimal(median(values)).normalize(), "f")

    def summary(self, run_id: str) -> dict[str, Any] | None:
        rows = self.repository.results_for_run(run_id)
        if rows is None:
            return None
        memberships = self.repository.memberships_for_run(run_id)
        assert memberships is not None
        unique_subjects = {
            (row["snapshot_id"], row["subject_type"], row["subject_id"])
            for row in rows
        }
        status_counts = Counter(row["terminal_outcome"] for row in rows)
        represented_symbols = sorted({item["symbol"] for item in memberships})
        snapshots_with_subjects = sum(
            item["membership_status"] == "evaluated" for item in memberships
        )
        warning_snapshot_ids = {
            row["snapshot_id"] for row in rows
            if row["quality_status"] == "quality_warning"
        }
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(
                row["evaluation_origin"], row["horizon_sessions"], row["method_family"],
                row["evidence_strength"], row["subject_type"],
            )].append(row)
        summaries = []
        for key in sorted(groups, key=lambda item: tuple("" if value is None else str(value) for value in item)):
            group = groups[key]
            target_group = [item for item in group if item["semantic_role"] == "target"]
            valid = [
                item for item in target_group
                if item["terminal_outcome"] in {"target_reached", "expired"}
                and item["quality_status"] == "available"
            ]
            numerator = sum(item["terminal_outcome"] == "target_reached" for item in valid)
            denominator = len(valid)
            summaries.append({
                "evaluation_origin": key[0], "horizon_sessions": key[1],
                "method_family": key[2], "evidence_strength": key[3],
                "subject_type": key[4], "n": len(group),
                "numerator": numerator, "denominator": denominator,
                "historical_target_reach_rate": (
                    format((Decimal(numerator) / Decimal(denominator)).normalize(), "f")
                    if denominator else None
                ),
                "median_forward_return": self._median(valid, "forward_return"),
                "median_excess_return": self._median(valid, "excess_return"),
                "median_upside_excursion": self._median(valid, "maximum_upside_excursion"),
                "median_downside_excursion": self._median(valid, "maximum_downside_excursion"),
                "status_counts": dict(sorted(Counter(item["terminal_outcome"] for item in group).items())),
            })
        return {
            "evaluation_run_id": run_id,
            "coverage": {
                "cohort_symbol_count": len(PHASE2_UNIVERSE),
                "cohort_symbols_represented": len(represented_symbols),
                "represented_symbols": represented_symbols,
                "requested_snapshot_count": len(memberships),
                "snapshots_with_eligible_subjects": snapshots_with_subjects,
                "snapshots_without_eligible_subjects": (
                    len(memberships) - snapshots_with_subjects
                ),
                "eligible_subject_count": len(unique_subjects),
                "evaluated_subject_count": len(unique_subjects),
                "evaluation_record_count": len(rows),
                "target_candidates": len({x for x in unique_subjects if x[1] == "target_candidate"}),
                "target_clusters": len({x for x in unique_subjects if x[1] == "target_cluster"}),
                "support_candidates": len({x for x in unique_subjects if x[1] == "support_candidate"}),
                "quality_warning_snapshot_count": len(warning_snapshot_ids),
                "quality_warning_evaluation_record_count": sum(
                    row["quality_status"] == "quality_warning" for row in rows
                ),
                "evaluation_record_status_counts": dict(sorted(status_counts.items())),
            },
            "groups": summaries,
            "disclosures": [
                "manual_14_symbol_cohort_not_representative_of_all_twse_tpex",
                "historical_reconstruction_not_actual_historical_publication",
                "observed_historical_frequency_not_future_probability",
                "evidence_strength_not_prediction_probability",
            ],
        }
