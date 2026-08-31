"""Allowlisted immutable Phase 17 context provenance and change mapping."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from src.domain.daily_research_review_context import (
    DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
    DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION,
    DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
    DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
    canonical_json,
    normalize_daily_reasons,
)


def _pick(source: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    value = source or {}
    return {key: value.get(key) for key in keys}


_IDENTITY_KEYS = (
    "canonical_symbol", "venue", "identity_status", "identity_ref", "identity_epoch"
)
_SOURCE_KEYS = (
    "provider", "dataset", "resource", "record_id", "source_revision",
    "availability_status", "ingestion_status", "status", "available_at", "ingested_at",
)
_EOD_KEYS = (
    "provider", "dataset", "resource", "record_id", "venue", "market_date",
    "observation_date", "available_at", "ingested_at", "classification", "unit",
    "freshness_status", "status", "eligible", "correction_id", "revision", "revoked",
    "digest",
)
_COVERAGE_KEYS = (
    "contract_version", "provider", "dataset", "resource", "record_id", "market_date",
    "venue", "canonical_symbol", "status", "partial", "unknown", "blocked",
    "denominator_candidate_count", "denominator_expected_count",
    "denominator_excluded_count", "denominator_unresolved_count",
    "source_observation_orphan_count", "aggregate_completeness_proven", "digest",
)
_PHASE16_ITEM_KEYS = (
    "contract_version", "d_k_policy_version", "market_date", "knowledge_cutoff_at",
    "internal_venue_scope", "canonical_symbol", "venue", "status", "source_state",
    "quality_status", "quality_reasons", "provenance_ref",
)
_PHASE16_AGGREGATE_KEYS = (
    "contract_version", "scope", "status", "aggregate_completeness_proven",
    "per_venue_status",
)


def build_daily_context_reference(
    *,
    market_date: str,
    knowledge_cutoff_at: str,
    phase13: Mapping[str, Any],
    phase14: Mapping[str, Any],
    phase15: Mapping[str, Any],
    phase16_item: Mapping[str, Any],
    phase16_aggregate: Mapping[str, Any],
    context_reasons: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    reference: dict[str, Any] = {
        "integration_contract_version": DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION,
        "daily_contract_version": DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
        "workflow_time_policy_version": DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
        "snapshot_selection_policy_version": DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
        "market_date": market_date,
        "knowledge_cutoff_at": knowledge_cutoff_at,
        "internal_venue_scope": "TWSE_TPEX",
        "phase13": {
            "identity": _pick(phase13.get("identity"), _IDENTITY_KEYS),
            "lifecycle": _pick(phase13.get("lifecycle"), ("listing_status",)),
            "trading": _pick(phase13.get("trading"), ("trading_state",)),
            "source_state": _pick(phase13.get("source_state"), _SOURCE_KEYS),
        },
        "phase14": {"eod": _pick(phase14.get("eod", phase14), _EOD_KEYS)},
        "phase15": {
            "coverage": _pick(phase15.get("coverage", phase15), _COVERAGE_KEYS)
        },
        "phase16": {
            "item": _pick(phase16_item, _PHASE16_ITEM_KEYS),
            "aggregate": _pick(phase16_aggregate, _PHASE16_AGGREGATE_KEYS),
        },
        "context_reasons": normalize_daily_reasons(context_reasons),
    }
    reference["context_digest"] = hashlib.sha256(
        canonical_json(reference).encode("utf-8")
    ).hexdigest()
    return reference


def verify_daily_context_reference(reference: Mapping[str, Any]) -> bool:
    digest = reference.get("context_digest")
    if not isinstance(digest, str):
        return False
    payload = dict(reference)
    payload.pop("context_digest", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest() == digest


def _required_mapping(
    baseline: Mapping[str, Any], current: Mapping[str, Any], path: tuple[str, ...]
) -> tuple[Any, Any, bool]:
    old: Any = baseline
    new: Any = current
    for key in path:
        if not isinstance(old, Mapping) or key not in old:
            return None, None, False
        if not isinstance(new, Mapping) or key not in new:
            return None, None, False
        old = old[key]
        new = new[key]
    return old, new, True


def context_change_reasons(
    baseline_reference: Mapping[str, Any] | None,
    current_reference: Mapping[str, Any],
) -> list[str]:
    if not baseline_reference or not verify_daily_context_reference(baseline_reference):
        return ["context_provenance_missing"]
    current_identity = current_reference.get("phase13", {}).get("identity", {})
    if current_identity.get("identity_status") in {"unresolved", "conflict"}:
        return ["identity_unresolved"]

    mappings = (
        ("identity_state_changed", ("phase13", "identity")),
        ("lifecycle_state_changed", ("phase13", "lifecycle", "listing_status")),
        ("trading_state_changed", ("phase13", "trading", "trading_state")),
        ("source_state_changed", ("phase13", "source_state")),
        ("eod_context_changed", ("phase14", "eod")),
        ("coverage_state_changed", ("phase15", "coverage")),
    )
    reasons: list[str] = []
    for reason, path in mappings:
        old, new, complete = _required_mapping(baseline_reference, current_reference, path)
        if not complete:
            return ["context_provenance_missing"]
        if old != new:
            reasons.append(reason)
    status = current_reference.get("phase16", {}).get("item", {}).get("status")
    typed = {"partial": "data_partial", "unknown": "data_unknown", "blocked": "data_blocked"}
    if status in typed:
        reasons.append(typed[status])
    return normalize_daily_reasons(reasons)


__all__ = [
    "build_daily_context_reference",
    "context_change_reasons",
    "verify_daily_context_reference",
]
