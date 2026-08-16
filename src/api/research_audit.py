"""Secret-safe structured audit records for Phase 12 Research write commands."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


logger = logging.getLogger("tw_stock_predictor.research_audit")


def write_research_audit(
    *, correlation_id: str, command_type: str, item_id: str | None,
    event_id: str | None, symbol: str | None, outcome: str, reason: str,
    server_timestamp: datetime,
) -> None:
    timestamp = server_timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "correlation_id": correlation_id,
        "command_type": command_type,
        "item_id": item_id,
        "event_id": event_id,
        "symbol": symbol,
        "outcome": outcome,
        "reason": reason,
        "server_timestamp": timestamp,
    }
    logger.info(json.dumps(record, sort_keys=True, separators=(",", ":")))
