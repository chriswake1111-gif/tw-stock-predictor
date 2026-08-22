"""Secret-safe structured audit logging for Phase 13 operator mutations."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.valuation import utc_now_timestamp
from src.services.universe_write_guard import UniverseOperatorContext


logger = logging.getLogger("tw_stock_predictor.universe_audit")


def write_universe_audit(
    context: UniverseOperatorContext,
    *,
    command: str,
    outcome: str,
    resource_id: str | None = None,
    resource_role: str | None = None,
    venue: str | None = None,
    channel: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Emit only operator/provenance dimensions, never payload secrets."""
    event: dict[str, Any] = {
        "audit_id": context.audit_id,
        "actor_id": context.actor_id,
        "run_id": context.run_id,
        "lock_id": context.lock_id,
        "command": str(command),
        "outcome": str(outcome),
        "server_timestamp": utc_now_timestamp(),
    }
    for key, value in {
        "resource_id": resource_id,
        "resource_role": resource_role,
        "venue": venue,
        "channel": channel,
        "reason": reason,
    }.items():
        if value not in (None, ""):
            event[key] = str(value)
    logger.info("universe_audit %s", event)
    return event


__all__ = ["logger", "write_universe_audit"]
