"""Phase 19 Installed Data Synchronization / Local Data Operations V1 domain models.

Defines the scoped domain capability model (InstalledWriteAuthorization),
parent operation status/stages, item statuses, and immutable operation contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

from src.domain.data_foundation import normalize_utc_timestamp
from src.domain.valuation import utc_now_timestamp

INSTALLED_OPERATIONS_ALLOWED_RESOURCES = frozenset({
    "twse.trading-calendar",
    "twse.t187ap03_L",
    "tpex.mopsfin_t187ap03_O",
    "twse.isin.security_classification",
    "twse.eod.stock_day_all",
    "tpex.eod.daily_close_quotes",
    "twse.market-turnover",
    "tpex.market-turnover",
    "cbc.m1b",
})


class InstalledOperationType(str, Enum):
    SYNC = "sync"
    BOOTSTRAP = "bootstrap"
    ENABLE_SYMBOL = "enable_symbol"


class InstalledOperationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class InstalledOperationStage(str, Enum):
    PREREQUISITES_CALENDAR = "prerequisites_calendar"
    UNIVERSE = "universe"
    CLASSIFICATION = "classification"
    EOD = "eod"
    TURNOVER_AND_CBC = "turnover_and_cbc"
    PROJECTION = "projection"
    IDLE = "idle"
    COMPLETED = "completed"


class InstalledItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    SCHEMA_CHANGED = "schema_changed"
    UNRECOGNIZED = "unrecognized"


class InstalledReadiness(str, Enum):
    NOT_INITIALIZED = "not_initialized"
    PARTIAL = "partial"
    READY = "ready"
    STALE = "stale"


class InstalledOperationError(Exception):
    """Base error for installed data operations."""


class OperationAuthorizationRevoked(PermissionError):
    """Raised when write authorization capability is revoked, expired, or mismatched."""


class OperationActiveConflict(InstalledOperationError):
    """Raised when an operation is triggered while another active operation is running."""


class OperationNotFound(InstalledOperationError):
    """Raised when the requested operation cannot be found."""


class OperationStateInvalid(InstalledOperationError):
    """Raised when attempting an invalid state transition."""


class OperationCancelled(InstalledOperationError):
    """Raised when the running operation was cancelled by operator or user."""


class OperationDeadlineExhausted(InstalledOperationError):
    """Raised when operation timeout / deadline budget is exceeded."""


@dataclass
class InstalledWriteAuthorization:
    """Scoped domain capability authorizing write mutations for installed operations."""

    operation_id: str
    instance_id: str
    actor_id: str = "installed_user"
    allowed_resource_ids: frozenset[str] = field(
        default_factory=lambda: INSTALLED_OPERATIONS_ALLOWED_RESOURCES
    )
    lease_expires_at: str = field(
        default_factory=lambda: utc_now_timestamp()
    )
    revoked: bool = False

    def is_valid(
        self,
        current_instance_id: str | None = None,
        resource_id: str | None = None,
    ) -> bool:
        if self.revoked:
            return False
        if current_instance_id is None or current_instance_id != self.instance_id:
            return False
        if resource_id is None or resource_id not in self.allowed_resource_ids:
            return False
        try:
            expiry = datetime.fromisoformat(self.lease_expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return expiry > datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return False

    def revoke(self) -> None:
        self.revoked = True

    def refresh_lease(self, new_lease_expires_at: str) -> None:
        self.lease_expires_at = normalize_utc_timestamp(
            new_lease_expires_at, "lease_expires_at"
        )


@dataclass(frozen=True)
class InstalledOperationRow:
    operation_id: str
    operation_type: str
    status: str
    current_stage: str
    lease_owner_id: str
    lease_expires_at: str
    target_symbols_json: str
    created_at: str
    updated_at: str
    completed_at: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class InstalledItemRow:
    item_id: str
    operation_id: str
    stage: str
    resource_id: str
    status: str
    raw_resource_revision_id: str | None = None
    ingestion_run_id: str | None = None
    ingestion_run_item_id: str | None = None
    attempt_count: int = 1
    created_at: str = ""
    completed_at: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
