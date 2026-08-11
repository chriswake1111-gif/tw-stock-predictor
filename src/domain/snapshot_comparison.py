"""Pure Phase 11 snapshot-comparison contracts and canonicalization rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


COMPARISON_POLICY_VERSION = "1.0"
COMPARISON_SNAPSHOT_CONTRACT = "analysis_snapshot_v1"


class ComparisonStatus(str, Enum):
    AVAILABLE = "available"
    INCOMPARABLE_CONTRACT = "incomparable_contract"


class ChangeCategory(str, Enum):
    STORED_FACT = "stored_fact"
    CURRENT_CONTEXT = "current_context"


class StoredChangeType(str, Enum):
    DEPENDENCY_ADDED = "dependency_added"
    DEPENDENCY_REMOVED = "dependency_removed"
    RESOURCE_REVISION_CHANGED = "resource_revision_changed"
    APPROVAL_REFERENCE_CHANGED = "approval_reference_changed"
    PROFILE_REVISION_CHANGED = "profile_revision_changed"
    RULE_VERSION_REFERENCE_CHANGED = "rule_version_reference_changed"
    SECTION_STATUS_CHANGED = "section_status_changed"
    DATA_QUALITY_STATUS_CHANGED = "data_quality_status_changed"
    VALUATION_RANGE_CHANGED = "valuation_range_changed"
    TECHNICAL_ANCHOR_CHANGED = "technical_anchor_changed"
    TARGET_RANGE_CHANGED = "target_range_changed"
    SUPPORT_RANGE_CHANGED = "support_range_changed"
    SCREENING_RESULT_CHANGED = "screening_result_changed"
    LIQUIDITY_CONTEXT_CHANGED = "liquidity_context_changed"
    CONFLUENCE_CLUSTER_ADDED = "confluence_cluster_added"
    CONFLUENCE_CLUSTER_REMOVED = "confluence_cluster_removed"
    CONFLUENCE_CLUSTER_CHANGED = "confluence_cluster_changed"
    DEPLOYMENT_SCENARIO_CHANGED = "deployment_scenario_changed"


class CurrentContextChangeType(str, Enum):
    FRESHNESS_STATUS_CHANGED = "freshness_status_changed"
    APPROVAL_REVOKED = "approval_revoked"
    APPROVAL_ELIGIBILITY_CHANGED = "approval_eligibility_changed"
    DEPENDENCY_BLOCKED = "dependency_blocked"
    DEPENDENCY_UNKNOWN = "dependency_unknown"
    PUBLICATION_EVIDENCE_CHANGED = "publication_evidence_changed"


class _MissingValue:
    __slots__ = ()


MISSING = _MissingValue()


def canonical_decimal(value: Any) -> str:
    """Return a finite, exponent-free decimal string with stable zero semantics."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("comparison decimal must be finite") from exc
    if not number.is_finite():
        raise ValueError("comparison decimal must be finite")
    if number == 0:
        return "0"
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def canonical_timestamp(value: Any) -> str:
    """Require a timezone and normalize an ISO-8601 timestamp to UTC Z."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("comparison timestamp is required")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ValueError("comparison timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("comparison timestamp must include timezone")
    normalized = parsed.astimezone(timezone.utc)
    rendered = normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return rendered.replace(".000000Z", "Z")


def canonical_value(value: Any, *, value_kind: str = "scalar") -> Any:
    """Canonicalize only a registry-declared semantic value."""
    if value is MISSING:
        return {"state": "missing"}
    if value is None:
        return None
    if value_kind == "decimal":
        return canonical_decimal(value)
    if value_kind == "timestamp":
        return canonical_timestamp(value)
    if value_kind == "set":
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("set-semantic comparison value must be a list")
        items = [canonical_value(item) for item in value]
        return sorted(items, key=repr)
    if isinstance(value, dict):
        return {
            str(key): canonical_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (float, Decimal)):
        return canonical_decimal(value)
    raise ValueError(f"unsupported comparison value type: {type(value).__name__}")


@dataclass(frozen=True)
class SnapshotReference:
    snapshot_id: str
    symbol: str
    knowledge_cutoff_at: str
    capture_mode: str
    model_version: str
    output_sha256: str

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "SnapshotReference":
        return cls(
            snapshot_id=snapshot["snapshot_id"],
            symbol=snapshot["symbol"],
            knowledge_cutoff_at=canonical_timestamp(snapshot["knowledge_cutoff_at"]),
            capture_mode=snapshot["capture_mode"],
            model_version=snapshot["model_version"],
            output_sha256=snapshot["output_sha256"],
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "capture_mode": self.capture_mode,
            "model_version": self.model_version,
            "output_sha256": self.output_sha256,
        }


@dataclass(frozen=True)
class SnapshotDelta:
    category: ChangeCategory
    change_type: str
    section: str
    canonical_identity: str
    field_path: str
    before: Any
    after: Any
    resource_type: str | None = None
    absolute_delta: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        payload = {
            "category": self.category.value,
            "change_type": self.change_type,
            "section": self.section,
            "resource_type": self.resource_type,
            "canonical_identity": self.canonical_identity,
            "field_path": self.field_path,
            "before": self.before,
            "after": self.after,
        }
        if self.absolute_delta is not None:
            payload["absolute_delta"] = self.absolute_delta
        return payload


def delta_sort_key(delta: SnapshotDelta) -> tuple[str, ...]:
    return (
        delta.category.value,
        delta.change_type,
        delta.section,
        delta.resource_type or "",
        delta.canonical_identity,
        delta.field_path,
    )
