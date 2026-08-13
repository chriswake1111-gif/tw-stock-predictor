"""Phase 12 research-review workflow contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from src.analysis_service import normalize_symbol
from src.domain.valuation import normalize_utc_timestamp


WORKFLOW_CONTRACT_VERSION = "research_review_queue_v1"
_STOCK_SYMBOL = re.compile(r"^[0-9]{4}\.(?:TW|TWO)$")


class MembershipState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ReviewState(str, Enum):
    NO_SNAPSHOT = "no_snapshot"
    BASELINE_NOT_SET = "baseline_not_set"
    COMPARABLE_WITH_DELTAS = "comparable_with_deltas"
    COMPARABLE_WITHOUT_DELTAS = "comparable_without_deltas"
    INCOMPARABLE_CONTRACT = "incomparable_contract"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    SNAPSHOT_INTEGRITY_ERROR = "snapshot_integrity_error"


class ResearchComparisonStatus(str, Enum):
    NOT_RUN = "not_run"
    COMPARABLE = "comparable"
    INCOMPARABLE_CONTRACT = "incomparable_contract"
    UNAVAILABLE = "unavailable"


def canonical_research_symbol(value: str) -> str:
    candidate = normalize_symbol(value)
    if not _STOCK_SYMBOL.fullmatch(candidate):
        raise ValueError("research_symbol_invalid")
    return candidate


@dataclass(frozen=True)
class ReviewAcknowledgment:
    watchlist_item_id: str
    acknowledged_snapshot_id: str
    comparison_cutoff_at: str
    idempotency_key: str

    def canonical_payload(self) -> dict[str, str]:
        values = {
            "watchlist_item_id": self.watchlist_item_id.strip(),
            "acknowledged_snapshot_id": self.acknowledged_snapshot_id.strip(),
            "comparison_cutoff_at": normalize_utc_timestamp(
                self.comparison_cutoff_at, "comparison_cutoff_at"
            ),
            "idempotency_key": self.idempotency_key.strip(),
        }
        if any(not value for value in values.values()):
            raise ValueError("review_acknowledgment_identifiers_required")
        return values


def comparison_has_deltas(
    *, comparison_status: ResearchComparisonStatus | str,
    stored_delta_count: int,
    current_context_delta_count: int,
) -> bool | None:
    try:
        status = ResearchComparisonStatus(comparison_status)
    except ValueError as exc:
        raise ValueError("research_comparison_status_invalid") from exc
    if status is not ResearchComparisonStatus.COMPARABLE:
        return None
    return stored_delta_count > 0 or current_context_delta_count > 0
