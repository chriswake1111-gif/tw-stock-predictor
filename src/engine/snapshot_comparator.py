"""Deterministic, whitelist-driven comparison of immutable analysis snapshots."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any, Callable

from src.domain.snapshot_comparison import (
    MISSING,
    ChangeCategory,
    SnapshotDelta,
    StoredChangeType,
    canonical_decimal,
    canonical_timestamp,
    canonical_value,
    delta_sort_key,
)


SECTION_NAMES = (
    "valuation",
    "liquidity",
    "technical_support",
    "target_confluence",
    "deployment_plan",
    "screening",
)

LIQUIDITY_FIELDS = (
    "trade_date",
    "turnover_m1b_ratio_pct",
    "rolling_mean_20d_pct",
    "rolling_mean_60d_pct",
    "historical_percentile_5y",
    "historical_percentile_10y",
    "alert_level",
    "turnover_twd.total",
    "m1b_twd.value",
)

SCREENING_FIELDS = (
    "status",
    "reason",
    "research_result",
    "components",
    "missing_components",
    "profile",
)

REQUIRED_OUTPUT_SECTIONS = SECTION_NAMES + ("data_quality",)
COMPARISON_COLLECTIONS = (
    ("valuation", "target_matrix"),
    ("technical_support", "scenarios"),
    ("target_confluence", "overlap_ranges"),
    ("deployment_plan", "plans"),
)


def supports_snapshot_contract(snapshot: dict[str, Any]) -> bool:
    """Validate the persisted Phase 7-10 snapshot envelope fail-closed."""
    required_types = {
        "snapshot_id": str,
        "symbol": str,
        "knowledge_cutoff_at": str,
        "capture_mode": str,
        "model_version": str,
        "output": dict,
        "source_resource_versions": list,
        "used_rule_versions": dict,
    }
    if any(not isinstance(snapshot.get(field), kind) for field, kind in required_types.items()):
        return False
    output = snapshot["output"]
    model = output.get("model")
    if not isinstance(model, dict):
        return False
    try:
        envelope_cutoff = canonical_timestamp(snapshot["knowledge_cutoff_at"])
        output_cutoff = canonical_timestamp(output.get("knowledge_cutoff_at"))
    except ValueError:
        return False
    if (
        output.get("symbol") != snapshot["symbol"]
        or model.get("version") != snapshot["model_version"]
        or output_cutoff != envelope_cutoff
    ):
        return False
    if "capture_mode" in output and output["capture_mode"] != snapshot["capture_mode"]:
        return False
    if any(not isinstance(output.get(section), dict) for section in REQUIRED_OUTPUT_SECTIONS):
        return False
    if any(not isinstance(item, dict) for item in snapshot["source_resource_versions"]):
        return False
    for section, collection in COMPARISON_COLLECTIONS:
        value = output[section].get(collection, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            return False
    return True


def compatibility_reason(
    base: dict[str, Any], comparison: dict[str, Any], comparison_cutoff: str
) -> str | None:
    if not supports_snapshot_contract(base) or not supports_snapshot_contract(comparison):
        return "unsupported_comparison_snapshot_contract"
    if base["symbol"] != comparison["symbol"]:
        return "different_symbol"
    if base["model_version"] != comparison["model_version"]:
        return "different_model_version"
    if base["capture_mode"] != comparison["capture_mode"]:
        return "different_capture_mode"
    if comparison_cutoff < max(base["knowledge_cutoff_at"], comparison["knowledge_cutoff_at"]):
        return "comparison_cutoff_precedes_snapshot_cutoff"
    return None


def _path(value: dict[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def _stable_text(value: Any) -> str:
    return json.dumps(
        canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _identity(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    values = [record.get(field) for field in fields]
    if any(value is None or value == "" for value in values):
        return "record:" + _stable_text(record)
    return "|".join(str(value) for value in values)


def _absolute_delta(before: Any, after: Any) -> str | None:
    if before is MISSING or after is MISSING or before is None or after is None:
        return None
    try:
        return canonical_decimal(Decimal(str(after)) - Decimal(str(before)))
    except Exception:
        return None


class SnapshotComparator:
    """Compare only explicitly registered Phase 11 fields and record identities."""

    @staticmethod
    def _delta(
        *,
        change_type: StoredChangeType,
        section: str,
        identity: str,
        field_path: str,
        before: Any,
        after: Any,
        resource_type: str | None = None,
        numeric: bool = False,
    ) -> SnapshotDelta | None:
        canonical_before = canonical_value(before)
        canonical_after = canonical_value(after)
        if canonical_before == canonical_after:
            return None
        return SnapshotDelta(
            category=ChangeCategory.STORED_FACT,
            change_type=change_type.value,
            section=section,
            resource_type=resource_type,
            canonical_identity=identity,
            field_path=field_path,
            before=canonical_before,
            after=canonical_after,
            absolute_delta=_absolute_delta(before, after) if numeric else None,
        )

    def _dependencies(
        self, base: dict[str, Any], comparison: dict[str, Any]
    ) -> list[SnapshotDelta]:
        def key(item: dict[str, Any]) -> str:
            logical = item.get("logical_resource_id")
            stable = logical if logical not in {None, ""} else item.get("resource_id")
            return "|".join(
                [str(item.get("section", "")), str(item.get("resource_type", "")), str(stable)]
            )

        left = {key(item): item for item in base.get("source_resource_versions", [])}
        right = {key(item): item for item in comparison.get("source_resource_versions", [])}
        deltas: list[SnapshotDelta] = []
        for identity in sorted(left.keys() | right.keys()):
            before = left.get(identity, MISSING)
            after = right.get(identity, MISSING)
            record = after if after is not MISSING else before
            assert isinstance(record, dict)
            section = str(record.get("section", "dependencies"))
            resource_type = str(record.get("resource_type", "unknown"))
            if before is MISSING:
                change = StoredChangeType.DEPENDENCY_ADDED
                delta = self._delta(
                    change_type=change, section=section, identity=identity,
                    resource_type=resource_type, field_path="source_resource_versions",
                    before=before, after=after,
                )
                if delta:
                    deltas.append(delta)
                continue
            if after is MISSING:
                delta = self._delta(
                    change_type=StoredChangeType.DEPENDENCY_REMOVED,
                    section=section, identity=identity, resource_type=resource_type,
                    field_path="source_resource_versions", before=before, after=after,
                )
                if delta:
                    deltas.append(delta)
                continue
            assert isinstance(before, dict) and isinstance(after, dict)
            revision_before = {
                "resource_id": before.get("resource_id"),
                "revision_number": before.get("revision_number"),
            }
            revision_after = {
                "resource_id": after.get("resource_id"),
                "revision_number": after.get("revision_number"),
            }
            delta = self._delta(
                change_type=StoredChangeType.RESOURCE_REVISION_CHANGED,
                section=section, identity=identity, resource_type=resource_type,
                field_path="source_resource_versions.revision",
                before=revision_before, after=revision_after,
            )
            if delta:
                deltas.append(delta)
            delta = self._delta(
                change_type=StoredChangeType.APPROVAL_REFERENCE_CHANGED,
                section=section, identity=identity, resource_type=resource_type,
                field_path="source_resource_versions.approval_ids",
                before=sorted(before.get("approval_ids", [])),
                after=sorted(after.get("approval_ids", [])),
            )
            if delta:
                deltas.append(delta)
        return deltas

    def _map_changes(
        self,
        *,
        base: dict[str, Any],
        comparison: dict[str, Any],
        field: str,
        change_type: StoredChangeType,
        section: str,
    ) -> list[SnapshotDelta]:
        left = base.get(field, {}) or {}
        right = comparison.get(field, {}) or {}
        deltas = []
        for key in sorted(set(left) | set(right)):
            delta = self._delta(
                change_type=change_type,
                section=section,
                identity=str(key),
                field_path=f"{field}.{key}",
                before=left.get(key, MISSING),
                after=right.get(key, MISSING),
            )
            if delta:
                deltas.append(delta)
        return deltas

    def _record_list(
        self,
        *,
        base_records: list[dict[str, Any]],
        comparison_records: list[dict[str, Any]],
        identity: Callable[[dict[str, Any]], str],
        change_type: StoredChangeType,
        section: str,
        field_path: str,
    ) -> list[SnapshotDelta]:
        left: dict[str, list[dict[str, Any]]] = defaultdict(list)
        right: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in base_records:
            left[identity(record)].append(record)
        for record in comparison_records:
            right[identity(record)].append(record)
        result = []
        for group_key in sorted(left.keys() | right.keys()):
            left_group = sorted(left[group_key], key=_stable_text)
            right_group = sorted(right[group_key], key=_stable_text)
            if len(left_group) == len(right_group) == 1:
                pairs = [(group_key, left_group[0], right_group[0])]
            else:
                left_text = {_stable_text(item): item for item in left_group}
                right_text = {_stable_text(item): item for item in right_group}
                pairs = []
                for offset, record_text in enumerate(sorted(left_text.keys() | right_text.keys())):
                    pairs.append((f"{group_key}|exact:{offset}", left_text.get(record_text, MISSING), right_text.get(record_text, MISSING)))
            for item_identity, before, after in pairs:
                delta = self._delta(
                    change_type=change_type,
                    section=section,
                    identity=item_identity,
                    field_path=field_path,
                    before=before,
                    after=after,
                )
                if delta:
                    result.append(delta)
        return result

    def _technical_changes(
        self, base_output: dict[str, Any], comparison_output: dict[str, Any]
    ) -> list[SnapshotDelta]:
        def identity(row: dict[str, Any]) -> str:
            return _identity(
                {
                    "rule_id": _path(row, "rule_trace.rule_id"),
                    "semantic_role": row.get("semantic_role"),
                    "scenario_type": row.get("scenario_type"),
                },
                ("rule_id", "semantic_role", "scenario_type"),
            )

        def anchor_projection(row: dict[str, Any]) -> dict[str, Any]:
            trace = row.get("rule_trace") if isinstance(row.get("rule_trace"), dict) else {}
            return {
                field: row[field]
                for field in (
                    "anchor_set_revision_id", "anchor_revision_number",
                    "anchor_available_at", "anchor_ingested_at", "anchors", "anchor_ids",
                )
                if field in row
            } | {
                field: trace[field]
                for field in ("approval_id", "anchor_revision_ids")
                if field in trace
            }

        def range_projection(row: dict[str, Any]) -> dict[str, Any]:
            return {
                field: row[field]
                for field in ("calculated_level", "price_low", "price_high", "price", "unit", "price_unit")
                if field in row
            }

        def semantic_deltas(
            before_records: list[dict[str, Any]],
            after_records: list[dict[str, Any]],
            *,
            projection: Callable[[dict[str, Any]], dict[str, Any]],
            change_type: StoredChangeType,
            field_path: str,
        ) -> list[SnapshotDelta]:
            left: dict[str, list[dict[str, Any]]] = defaultdict(list)
            right: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for record in before_records:
                left[identity(record)].append(projection(record))
            for record in after_records:
                right[identity(record)].append(projection(record))
            result: list[SnapshotDelta] = []
            for group_key in sorted(left.keys() | right.keys()):
                left_group = sorted(left[group_key], key=_stable_text)
                right_group = sorted(right[group_key], key=_stable_text)
                if len(left_group) == len(right_group) == 1:
                    pairs = [(group_key, left_group[0], right_group[0])]
                else:
                    left_texts = [_stable_text(item) for item in left_group]
                    right_texts = [_stable_text(item) for item in right_group]
                    for common in sorted(set(left_texts) & set(right_texts)):
                        count = min(left_texts.count(common), right_texts.count(common))
                        for _ in range(count):
                            left_texts.remove(common)
                            right_texts.remove(common)
                    pairs = [
                        (f"{group_key}|removed:{offset}", json.loads(text), MISSING)
                        for offset, text in enumerate(sorted(left_texts))
                    ] + [
                        (f"{group_key}|added:{offset}", MISSING, json.loads(text))
                        for offset, text in enumerate(sorted(right_texts))
                    ]
                for item_identity, before, after in pairs:
                    delta = self._delta(
                        change_type=change_type,
                        section="technical_support",
                        identity=item_identity,
                        field_path=field_path,
                        before=before,
                        after=after,
                    )
                    if delta:
                        result.append(delta)
            return result

        base_scenarios = _path(base_output, "technical_support.scenarios")
        comparison_scenarios = _path(comparison_output, "technical_support.scenarios")
        base_records = base_scenarios if isinstance(base_scenarios, list) else []
        comparison_records = comparison_scenarios if isinstance(comparison_scenarios, list) else []
        deltas = semantic_deltas(
            base_records, comparison_records,
            projection=anchor_projection,
            change_type=StoredChangeType.TECHNICAL_ANCHOR_CHANGED,
            field_path="technical_support.scenarios.anchor_provenance",
        )
        for semantic_role, change_type in (
            ("target", StoredChangeType.TARGET_RANGE_CHANGED),
            ("support", StoredChangeType.SUPPORT_RANGE_CHANGED),
        ):
            deltas.extend(semantic_deltas(
                [item for item in base_records if item.get("semantic_role") == semantic_role],
                [item for item in comparison_records if item.get("semantic_role") == semantic_role],
                projection=range_projection,
                change_type=change_type,
                field_path="technical_support.scenarios.price_range",
            ))
        return deltas

    def compare(self, base: dict[str, Any], comparison: dict[str, Any]) -> list[dict[str, Any]]:
        deltas = self._dependencies(base, comparison)
        deltas.extend(self._map_changes(
            base=base, comparison=comparison, field="used_rule_versions",
            change_type=StoredChangeType.RULE_VERSION_REFERENCE_CHANGED,
            section="rules",
        ))
        for field in ("synthesis_profile_revision_id", "synthesis_profile_approval_id"):
            delta = self._delta(
                change_type=(
                    StoredChangeType.PROFILE_REVISION_CHANGED
                    if field.endswith("revision_id")
                    else StoredChangeType.APPROVAL_REFERENCE_CHANGED
                ),
                section="target_confluence", identity="synthesis_profile",
                field_path=field, before=base.get(field, MISSING),
                after=comparison.get(field, MISSING),
            )
            if delta:
                deltas.append(delta)

        base_output = base.get("output", {})
        comparison_output = comparison.get("output", {})
        for section in SECTION_NAMES:
            delta = self._delta(
                change_type=StoredChangeType.SECTION_STATUS_CHANGED,
                section=section, identity=section, field_path=f"{section}.status",
                before=_path(base_output, f"{section}.status"),
                after=_path(comparison_output, f"{section}.status"),
            )
            if delta:
                deltas.append(delta)
        delta = self._delta(
            change_type=StoredChangeType.DATA_QUALITY_STATUS_CHANGED,
            section="data_quality", identity="data_quality",
            field_path="data_quality.status",
            before=_path(base_output, "data_quality.status"),
            after=_path(comparison_output, "data_quality.status"),
        )
        if delta:
            deltas.append(delta)

        valuation_fields = (
            "observation_logical_series_id", "pe_logical_series_id", "eps_scenario",
            "fiscal_year", "status", "eps_value", "pe_value", "target_price", "formula",
            "rule_ids",
        )
        valuation_projection = lambda row: {
            field: row[field] for field in valuation_fields if field in row
        }
        deltas.extend(self._record_list(
            base_records=[
                valuation_projection(row)
                for row in (_path(base_output, "valuation.target_matrix") if isinstance(_path(base_output, "valuation.target_matrix"), list) else [])
            ],
            comparison_records=[
                valuation_projection(row)
                for row in (_path(comparison_output, "valuation.target_matrix") if isinstance(_path(comparison_output, "valuation.target_matrix"), list) else [])
            ],
            identity=lambda row: _identity(
                row,
                ("observation_logical_series_id", "pe_logical_series_id", "eps_scenario", "fiscal_year"),
            ),
            change_type=StoredChangeType.VALUATION_RANGE_CHANGED,
            section="valuation", field_path="valuation.target_matrix",
        ))

        deltas.extend(self._technical_changes(base_output, comparison_output))

        base_clusters = {str(row.get("cluster_id")): row for row in (_path(base_output, "target_confluence.overlap_ranges") if isinstance(_path(base_output, "target_confluence.overlap_ranges"), list) else [])}
        comparison_clusters = {str(row.get("cluster_id")): row for row in (_path(comparison_output, "target_confluence.overlap_ranges") if isinstance(_path(comparison_output, "target_confluence.overlap_ranges"), list) else [])}
        for cluster_id in sorted(base_clusters.keys() | comparison_clusters.keys()):
            if cluster_id not in base_clusters:
                change_type = StoredChangeType.CONFLUENCE_CLUSTER_ADDED
            elif cluster_id not in comparison_clusters:
                change_type = StoredChangeType.CONFLUENCE_CLUSTER_REMOVED
            else:
                change_type = StoredChangeType.CONFLUENCE_CLUSTER_CHANGED
            delta = self._delta(
                change_type=change_type, section="target_confluence",
                identity=cluster_id, field_path="target_confluence.overlap_ranges",
                before=base_clusters.get(cluster_id, MISSING),
                after=comparison_clusters.get(cluster_id, MISSING),
            )
            if delta:
                deltas.append(delta)

        deltas.extend(self._record_list(
            base_records=_path(base_output, "deployment_plan.plans") if isinstance(_path(base_output, "deployment_plan.plans"), list) else [],
            comparison_records=_path(comparison_output, "deployment_plan.plans") if isinstance(_path(comparison_output, "deployment_plan.plans"), list) else [],
            identity=lambda row: str(row.get("logical_campaign_id") or row.get("plan_revision_id")),
            change_type=StoredChangeType.DEPLOYMENT_SCENARIO_CHANGED,
            section="deployment_plan", field_path="deployment_plan.plans",
        ))

        for field in LIQUIDITY_FIELDS:
            delta = self._delta(
                change_type=StoredChangeType.LIQUIDITY_CONTEXT_CHANGED,
                section="liquidity", identity="market_liquidity",
                field_path=f"liquidity.{field}",
                before=_path(base_output, f"liquidity.{field}"),
                after=_path(comparison_output, f"liquidity.{field}"),
                numeric=field not in {"trade_date", "alert_level"},
            )
            if delta:
                deltas.append(delta)

        for field in SCREENING_FIELDS:
            if field == "status":
                continue
            delta = self._delta(
                change_type=StoredChangeType.SCREENING_RESULT_CHANGED,
                section="screening", identity="screening_result",
                field_path=f"screening.{field}",
                before=_path(base_output, f"screening.{field}"),
                after=_path(comparison_output, f"screening.{field}"),
            )
            if delta:
                deltas.append(delta)

        return [item.canonical_payload() for item in sorted(deltas, key=delta_sort_key)]
