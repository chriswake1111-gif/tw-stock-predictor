"""Deterministic TGT-01 target-family overlap synthesis."""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _decimal(value: Any, field_name: str) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field_name} must be a finite positive decimal")
    return number


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class TargetConfluenceEngine:
    """Combine trusted candidates without treating cells as independent methods."""

    @staticmethod
    def _normalize(candidate: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        required = {
            "candidate_id", "method_family", "rule_id", "rule_version",
            "evidence_level", "implementation_mode", "semantic_role",
            "source_resource_ids", "dependency_keys", "approval_ids",
        }
        missing = required.difference(candidate)
        if missing:
            raise ValueError(f"candidate is missing required fields: {sorted(missing)}")
        if candidate["semantic_role"] not in {"target", "support"}:
            raise ValueError("Phase 7 candidate role must be target or support")
        if candidate["method_family"] not in profile["allowed_method_families"]:
            raise ValueError("candidate method family is not approved by the synthesis profile")
        low_value = candidate.get("price_low", candidate.get("price"))
        high_value = candidate.get("price_high", candidate.get("price"))
        low = _decimal(low_value, "candidate price_low")
        high = _decimal(high_value, "candidate price_high")
        if low > high:
            raise ValueError("candidate price_low cannot exceed price_high")
        if low == high:
            tolerance = Decimal(profile["overlap_tolerance"])
            low = low * (Decimal("1") - tolerance)
            high = high * (Decimal("1") + tolerance)
        quantum = Decimal(profile["calculation_quantum"])
        low = low.quantize(quantum, rounding=ROUND_HALF_UP)
        high = high.quantize(quantum, rounding=ROUND_HALF_UP)
        return {
            **candidate,
            "candidate_id": str(candidate["candidate_id"]),
            "price_low": low,
            "price_high": high,
            "source_resource_ids": sorted(set(candidate["source_resource_ids"])),
            "dependency_keys": sorted(set(candidate["dependency_keys"])),
            "approval_ids": sorted(set(candidate["approval_ids"])),
        }

    @staticmethod
    def _candidate_public(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            **candidate,
            "price_low": _text(candidate["price_low"]),
            "price_high": _text(candidate["price_high"]),
        }

    @staticmethod
    def _active_sets(targets: list[dict[str, Any]]) -> list[tuple[str, ...]]:
        boundaries = sorted(
            {item["price_low"] for item in targets}
            | {item["price_high"] for item in targets}
        )
        probes = set(boundaries)
        for left, right in zip(boundaries, boundaries[1:]):
            if left < right:
                probes.add((left + right) / Decimal("2"))
        sets = {
            tuple(sorted(
                item["candidate_id"]
                for item in targets
                if item["price_low"] <= probe <= item["price_high"]
            ))
            for probe in probes
        }
        sets.discard(tuple())
        maximal = [
            candidate_set
            for candidate_set in sets
            if not any(
                set(candidate_set) < set(other)
                for other in sets
            )
        ]
        return sorted(set(maximal))

    @staticmethod
    def _components(
        candidates: list[dict[str, Any]], families: list[str]
    ) -> tuple[int, list[str]]:
        family_dependencies = {
            family: {
                dependency
                for item in candidates
                if item["method_family"] == family
                for dependency in item["dependency_keys"]
            }
            for family in families
        }
        graph = {family: set() for family in families}
        shared: set[str] = set()
        for index, left in enumerate(families):
            for right in families[index + 1:]:
                overlap = family_dependencies[left].intersection(
                    family_dependencies[right]
                )
                if overlap:
                    graph[left].add(right)
                    graph[right].add(left)
                    shared.update(overlap)
        seen: set[str] = set()
        components = 0
        for family in families:
            if family in seen:
                continue
            components += 1
            pending = [family]
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(sorted(graph[current] - seen))
        return components, sorted(shared)

    @staticmethod
    def _strength(count: int, policy: list[dict[str, Any]]) -> str | None:
        strength = None
        for threshold in sorted(
            policy,
            key=lambda item: item["minimum_independent_target_components"],
        ):
            if count >= int(threshold["minimum_independent_target_components"]):
                strength = threshold["label"]
        return strength

    def evaluate(
        self,
        *,
        candidates: list[dict[str, Any]],
        profile: dict[str, Any],
        rule_trace: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_by_id: dict[str, dict[str, Any]] = {}
        experimental = []
        for candidate in candidates:
            if candidate.get("method_family") not in profile["allowed_method_families"]:
                experimental.append({
                    **candidate,
                    "excluded_from_evidence_strength": True,
                    "exclusion_reason": "method_family_not_allowed_by_profile",
                })
                continue
            if (
                candidate.get("evidence_level") == "U"
                or candidate.get("implementation_mode")
                in {"unsupported", "legacy_experimental"}
            ):
                experimental.append({
                    **candidate,
                    "excluded_from_evidence_strength": True,
                })
                continue
            item = self._normalize(candidate, profile)
            previous = normalized_by_id.get(item["candidate_id"])
            if previous and previous != item:
                raise ValueError("candidate_id is bound to conflicting candidate content")
            normalized_by_id[item["candidate_id"]] = item
        normalized = [normalized_by_id[key] for key in sorted(normalized_by_id)]
        targets = [item for item in normalized if item["semantic_role"] == "target"]
        supports = [item for item in normalized if item["semantic_role"] == "support"]
        target_by_id = {item["candidate_id"]: item for item in targets}
        clusters = []
        clustered_ids: set[str] = set()
        rejected_dependency_sets = []
        for active_ids in self._active_sets(targets):
            active = [target_by_id[candidate_id] for candidate_id in active_ids]
            families = sorted({item["method_family"] for item in active})
            if len(families) < 2:
                continue
            independent_count, shared = self._components(active, families)
            if independent_count < 2:
                rejected_dependency_sets.append({
                    "candidate_ids": list(active_ids),
                    "shared_dependencies": shared,
                    "reason": "independent_target_methods_below_two",
                })
                continue
            lower = max(item["price_low"] for item in active)
            upper = min(item["price_high"] for item in active)
            cluster_key = "|".join(active_ids)
            cluster_id = (
                "target_cluster_"
                + hashlib.sha256(cluster_key.encode("utf-8")).hexdigest()[:20]
            )
            strength = self._strength(
                independent_count, profile["evidence_strength_policy"]
            )
            clusters.append({
                "cluster_id": cluster_id,
                "price_low": _text(lower),
                "price_high": _text(upper),
                "price_unit": "TWD_per_share",
                "candidate_count": len(active),
                "support_count": len(families),
                "independent_method_count": independent_count,
                "evidence_strength": strength,
                "target_method_families": families,
                "candidate_ids": list(active_ids),
                "shared_dependencies": shared,
            })
            clustered_ids.update(active_ids)
        clusters.sort(key=lambda item: (
            Decimal(item["price_low"]),
            Decimal(item["price_high"]),
            item["cluster_id"],
        ))
        alignments = []
        for cluster in clusters:
            cluster_low = Decimal(cluster["price_low"])
            cluster_high = Decimal(cluster["price_high"])
            for support in supports:
                low = max(cluster_low, support["price_low"])
                high = min(cluster_high, support["price_high"])
                if low <= high:
                    alignments.append({
                        "cluster_id": cluster["cluster_id"],
                        "support_candidate_id": support["candidate_id"],
                        "overlap_low": _text(low),
                        "overlap_high": _text(high),
                    })
        max_independent = max(
            (item["independent_method_count"] for item in clusters), default=0
        )
        strength_order = {None: -1, "low": 0, "moderate": 1, "high": 2}
        max_strength = max(
            (item["evidence_strength"] for item in clusters),
            key=lambda value: strength_order[value],
            default=None,
        )
        max_support = max((item["support_count"] for item in clusters), default=0)
        max_candidates = max((item["candidate_count"] for item in clusters), default=0)
        rules_used = [rule_trace] if clusters else []
        return {
            "status": "available" if clusters else "insufficient_data",
            "reason": None if clusters else "independent_target_methods_below_two",
            "overlap_ranges": clusters,
            "candidate_count": max_candidates,
            "support_count": max_support,
            "independent_method_count": max_independent,
            "evidence_strength": max_strength,
            "max_candidate_count": max_candidates,
            "max_support_count": max_support,
            "max_independent_method_count": max_independent,
            "max_evidence_strength": max_strength,
            "summary_policy": "maximum_cluster_strength",
            "supporting_methods": [self._candidate_public(item) for item in normalized],
            "shared_dependencies": sorted({
                dependency
                for cluster in clusters
                for dependency in cluster["shared_dependencies"]
            }),
            "cross_role_alignment": sorted(
                alignments,
                key=lambda item: (item["cluster_id"], item["support_candidate_id"]),
            ),
            "non_overlapping_candidates": [
                self._candidate_public(item)
                for item in targets
                if item["candidate_id"] not in clustered_ids
            ],
            "dependency_collapsed_candidates": rejected_dependency_sets,
            "contradicting_methods": [],
            "experimental_candidates": experimental,
            "synthesis_profile_revision_id": profile["id"],
            "synthesis_profile_approval_id": profile["verified_approval_id"],
            "rule_trace": rule_trace if clusters else None,
            "rules_used": rules_used,
            "limitations": [
                "scenario_synthesis_not_probability",
                "semantic_contradiction_not_yet_defined",
                "support_does_not_increase_target_strength",
                "no_automatic_target_selection",
            ],
        }
