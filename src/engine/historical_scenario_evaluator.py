"""Pure Phase 8 evaluator for exact immutable Phase 7 snapshot output."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from src.domain.analysis_snapshot import CaptureMode
from src.domain.performance_validation import (
    EvaluationOrigin,
    EvaluationSubjectType,
    ScenarioEvaluation,
)
from src.services.fixed_outcome_dataset import OutcomeBar, VerifiedOutcomeDataset


TAIPEI = ZoneInfo("Asia/Taipei")


def _origin(capture_mode: str) -> EvaluationOrigin:
    if capture_mode == CaptureMode.LIVE_REFRESH.value:
        return EvaluationOrigin.PROSPECTIVE_SNAPSHOT
    if capture_mode == CaptureMode.HISTORICAL_RECONSTRUCTION.value:
        return EvaluationOrigin.HISTORICAL_RECONSTRUCTION
    raise ValueError("unsupported Phase 7 snapshot capture_mode")


def _quantized(value: Decimal, quantum: Decimal) -> str:
    number = Decimal(str(value))
    return format(number.quantize(quantum, rounding=ROUND_HALF_UP).normalize(), "f")


class HistoricalScenarioEvaluator:
    """Evaluate stored candidate semantics without importing any model service."""

    @staticmethod
    def subjects(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        confluence = snapshot.get("output", {}).get("target_confluence", {})
        subjects: list[dict[str, Any]] = []
        for item in confluence.get("supporting_methods", []):
            family = item.get("method_family")
            role = item.get("semantic_role")
            if family not in {"VAL-01", "FB-03", "FB-04"}:
                continue
            if (family == "FB-04") != (role == "support"):
                continue
            subjects.append({
                "subject_type": (
                    EvaluationSubjectType.SUPPORT_CANDIDATE
                    if role == "support" else EvaluationSubjectType.TARGET_CANDIDATE
                ),
                "subject_id": item["candidate_id"],
                "method_family": family,
                "semantic_role": role,
                "price_low": item.get("price_low", item.get("price")),
                "price_high": item.get("price_high", item.get("price")),
                "evidence_strength": None,
                "metadata": {
                    "rule_id": item.get("rule_id"),
                    "rule_version": item.get("rule_version"),
                    "source_resource_ids": item.get("source_resource_ids", []),
                    "approval_ids": item.get("approval_ids", []),
                },
            })
        for cluster in confluence.get("overlap_ranges", []):
            subjects.append({
                "subject_type": EvaluationSubjectType.TARGET_CLUSTER,
                "subject_id": cluster["cluster_id"],
                "method_family": "TGT-01",
                "semantic_role": "target",
                "price_low": cluster["price_low"],
                "price_high": cluster["price_high"],
                "evidence_strength": cluster.get("evidence_strength"),
                "metadata": {
                    key: cluster.get(key)
                    for key in (
                        "candidate_ids", "target_method_families", "shared_dependencies",
                        "independent_method_count", "evidence_strength",
                    )
                },
            })
        return sorted(subjects, key=lambda item: (item["subject_type"].value, item["subject_id"]))

    def evaluate(
        self,
        *,
        snapshot: dict[str, Any],
        profile: dict[str, Any],
        dataset: VerifiedOutcomeDataset,
        created_at: str,
    ) -> list[ScenarioEvaluation]:
        if profile.get("verified_approval_id") is None:
            raise ValueError("evaluation_profile_needs_human_approval")
        horizons = tuple(profile["horizons_sessions"])
        if horizons != (20, 60) and horizons != [20, 60]:
            raise ValueError("unsupported evaluation profile horizons")
        symbol = snapshot["symbol"].upper()
        symbol_bars = dataset.bars_by_symbol.get(symbol)
        if symbol_bars is None:
            return []
        origin = _origin(snapshot["capture_mode"])
        cutoff = datetime.fromisoformat(snapshot["knowledge_cutoff_at"].replace("Z", "+00:00"))
        cutoff_local_date = cutoff.astimezone(TAIPEI).date().isoformat()
        calendar_index = {date: index for index, date in enumerate(dataset.calendar)}
        symbol_by_date = {bar.date: bar for bar in symbol_bars}
        benchmark_by_date = {bar.date: bar for bar in dataset.benchmark_bars}
        eligible_sessions = [date for date in dataset.calendar if date > cutoff_local_date]
        start_date = next((date for date in eligible_sessions if date in symbol_by_date), None)
        skipped = None if start_date is None else eligible_sessions.index(start_date)
        resource = next(
            item for item in dataset.manifest.symbol_resources if item["symbol"] == symbol
        )
        results: list[ScenarioEvaluation] = []
        for subject in self.subjects(snapshot):
            for horizon in horizons:
                results.append(self._evaluate_subject(
                    snapshot=snapshot,
                    subject=subject,
                    horizon=horizon,
                    dataset=dataset,
                    symbol_by_date=symbol_by_date,
                    benchmark_by_date=benchmark_by_date,
                    calendar_index=calendar_index,
                    start_date=start_date,
                    skipped=skipped,
                    origin=origin,
                    quality_status=resource["quality_status"],
                    quantum=Decimal(profile["calculation_quantum"]),
                    created_at=created_at,
                ))
        return results

    def _evaluate_subject(
        self,
        *,
        snapshot: dict[str, Any],
        subject: dict[str, Any],
        horizon: int,
        dataset: VerifiedOutcomeDataset,
        symbol_by_date: dict[str, OutcomeBar],
        benchmark_by_date: dict[str, OutcomeBar],
        calendar_index: dict[str, int],
        start_date: str | None,
        skipped: int | None,
        origin: EvaluationOrigin,
        quality_status: str,
        quantum: Decimal,
        created_at: str,
    ) -> ScenarioEvaluation:
        common = dict(
            snapshot_id=snapshot["snapshot_id"], symbol=snapshot["symbol"],
            evaluation_origin=origin, subject_type=subject["subject_type"],
            subject_id=subject["subject_id"], method_family=subject["method_family"],
            semantic_role=subject["semantic_role"], subject_metadata=dict(subject["metadata"]),
            evidence_strength=subject["evidence_strength"],
            knowledge_cutoff_at=snapshot["knowledge_cutoff_at"], horizon_sessions=horizon,
            outcome_resource_manifest_id=dataset.manifest.manifest_id, created_at=created_at,
            quality_status=quality_status, benchmark_status="insufficient_data",
            market_sessions_skipped=skipped,
        )
        if start_date is None:
            return ScenarioEvaluation(**common, terminal_outcome="insufficient_future_data")
        start_index = calendar_index[start_date]
        end_index = start_index + horizon
        if end_index >= len(dataset.calendar):
            return ScenarioEvaluation(
                **common, terminal_outcome="insufficient_future_data",
                evaluation_start_session=start_date,
                start_price=_quantized(symbol_by_date[start_date].open, quantum),
            )
        end_date = dataset.calendar[end_index]
        if end_date > dataset.manifest.outcome_observed_through_session:
            return ScenarioEvaluation(
                **common, terminal_outcome="pending",
                evaluation_start_session=start_date,
                evaluation_end_session=end_date,
                start_price=_quantized(symbol_by_date[start_date].open, quantum),
            )
        expected_dates = dataset.calendar[start_index:end_index + 1]
        if any(date not in symbol_by_date for date in expected_dates):
            return ScenarioEvaluation(
                **common, terminal_outcome="insufficient_future_data",
                evaluation_start_session=start_date, evaluation_end_session=end_date,
                start_price=_quantized(symbol_by_date[start_date].open, quantum),
            )
        bars = [symbol_by_date[date] for date in expected_dates]
        start = bars[0].open
        low, high = Decimal(str(subject["price_low"])), Decimal(str(subject["price_high"]))
        if low > high:
            raise ValueError("stored snapshot subject has an invalid range")
        if low <= start <= high:
            position = "already_in_range"
        elif start < low:
            position = "above_start"
        else:
            position = "below_start"
        maximum_up = max(bar.high for bar in bars) / start - 1
        maximum_down = min(bar.low for bar in bars) / start - 1
        end_return = bars[-1].close / start - 1
        benchmark_status = "available"
        benchmark_return = None
        if start_date not in benchmark_by_date or end_date not in benchmark_by_date:
            benchmark_status = "insufficient_data"
        else:
            benchmark_return = (
                benchmark_by_date[end_date].close / benchmark_by_date[start_date].open - 1
            )
        common.update({
            "benchmark_status": benchmark_status,
            "evaluation_start_session": start_date,
            "evaluation_end_session": end_date,
            "start_price": _quantized(start, quantum),
            "target_low": _quantized(low, quantum),
            "target_high": _quantized(high, quantum),
            "target_position_at_start": position,
            "maximum_upside_excursion": _quantized(maximum_up, quantum),
            "maximum_downside_excursion": _quantized(maximum_down, quantum),
            "forward_return": _quantized(end_return, quantum),
            "benchmark_return": (
                _quantized(benchmark_return, quantum) if benchmark_return is not None else None
            ),
            "excess_return": (
                _quantized(end_return - benchmark_return, quantum)
                if benchmark_return is not None else None
            ),
        })
        if subject["semantic_role"] == "support":
            touched = any(bar.low <= high and bar.high >= low for bar in bars)
            breached = min(bar.low for bar in bars) < low if start >= high else None
            common["subject_metadata"].update({
                "support_touched": touched,
                "support_held": touched and breached is False,
                "support_breached": breached,
            })
            return ScenarioEvaluation(**common, terminal_outcome="not_applicable")
        if position == "already_in_range":
            return ScenarioEvaluation(**common, terminal_outcome="already_in_range", target_reached=False)
        first = next(
            ((index, bar) for index, bar in enumerate(bars) if bar.low <= high and bar.high >= low),
            None,
        )
        reached = first is not None
        if position == "above_start":
            directional_mfe, directional_mae = maximum_up, maximum_down
        else:
            directional_mfe, directional_mae = -maximum_down, -maximum_up
        observed_outcome = "target_reached" if reached else "expired"
        if quality_status == "quality_warning":
            common["subject_metadata"]["observed_outcome_excluded_from_rate"] = observed_outcome
            return ScenarioEvaluation(
                **common,
                terminal_outcome="quality_warning",
                target_reached=None,
                directional_mfe=_quantized(directional_mfe, quantum),
                directional_mae=_quantized(directional_mae, quantum),
            )
        return ScenarioEvaluation(
            **common,
            terminal_outcome=observed_outcome,
            target_reached=reached,
            first_target_reached_at=first[1].date if first else None,
            trading_sessions_to_target=first[0] if first else None,
            directional_mfe=_quantized(directional_mfe, quantum),
            directional_mae=_quantized(directional_mae, quantum),
        )
