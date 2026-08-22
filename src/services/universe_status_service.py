"""Pure Universe status matrix shared by exact lookup and list composition."""

from __future__ import annotations

from typing import Any, Iterable

from src.domain.universe import (
    ACTIONABLE_HUMAN_REASONS,
    FreshnessStatus,
    UniverseStatus,
    UniverseStatusInput,
    compose_universe_status,
    universe_status_matrix_v1,
)


def evaluate_universe_status(
    identity_reference: dict[str, Any] | None,
    *,
    freshness: str = FreshnessStatus.UNKNOWN.value,
    current_complete: bool = False,
    reasons: Iterable[str] = (),
) -> dict[str, Any]:
    return universe_status_matrix_v1(
        UniverseStatusInput(
            identity_reference=identity_reference,
            operational_freshness=freshness,
            current_complete=current_complete,
            reasons=tuple(dict.fromkeys(str(reason) for reason in reasons if reason)),
        )
    )


def compose_list_status(items: list[dict[str, Any]], scoped_venues: Iterable[str]) -> str:
    if not items:
        return UniverseStatus.INSUFFICIENT_DATA.value
    scoped = {str(v).upper() for v in scoped_venues}
    statuses = [item.get("status") for item in items]
    if any(status == UniverseStatus.NEEDS_HUMAN_INPUT.value for status in statuses):
        return UniverseStatus.NEEDS_HUMAN_INPUT.value
    if any(status in {UniverseStatus.PARTIAL.value, UniverseStatus.INSUFFICIENT_DATA.value} for status in statuses):
        return UniverseStatus.PARTIAL.value
    item_venues = {(item.get("identity_reference") or {}).get("venue") for item in items}
    if scoped - item_venues:
        return UniverseStatus.PARTIAL.value
    return UniverseStatus.AVAILABLE.value


def is_actionable_reason(reason: str) -> bool:
    return reason in ACTIONABLE_HUMAN_REASONS


class UniverseStatusService:
    """Small class facade for callers that prefer service injection."""

    policy_version = "universe_status_matrix_v1"

    def evaluate(self, identity_reference: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
        return evaluate_universe_status(identity_reference, **kwargs)

    def compose(self, items: list[dict[str, Any]], scoped_venues: Iterable[str]) -> str:
        return compose_list_status(items, scoped_venues)


__all__ = ["UniverseStatusService", "evaluate_universe_status", "compose_list_status", "is_actionable_reason"]
