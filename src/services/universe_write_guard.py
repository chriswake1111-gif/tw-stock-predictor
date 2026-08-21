"""Central fail-closed boundary for Phase 13 Universe mutations."""

from __future__ import annotations

import os
from dataclasses import dataclass


class UniverseWriteError(RuntimeError):
    code = "universe_ingestion_write_rejected"


class UniverseIngestionWritesDisabled(UniverseWriteError):
    code = "universe_ingestion_writes_disabled"

    def __init__(self) -> None:
        super().__init__(self.code)


class UniverseOperatorContextRequired(UniverseWriteError):
    code = "universe_operator_context_required"

    def __init__(self, missing: str = "actor_id") -> None:
        self.missing = missing
        super().__init__(f"{self.code}:{missing}")


@dataclass(frozen=True)
class UniverseOperatorContext:
    actor_id: str
    run_id: str | None = None
    lock_id: str | None = None
    audit_id: str | None = None


class UniverseWriteGuard:
    ENV_NAME = "UNIVERSE_INGESTION_WRITES_ENABLED"

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = os.getenv(self.ENV_NAME, "false").strip().lower() == "true"
        self.enabled = bool(enabled)

    def require_enabled(self, context: UniverseOperatorContext | None = None) -> UniverseOperatorContext:
        if not self.enabled:
            raise UniverseIngestionWritesDisabled()
        if context is None or not context.actor_id or not context.actor_id.strip():
            raise UniverseOperatorContextRequired("actor_id")
        return context

    # Names used by services/repositories and simple tests.
    def before_mutation(self, context: UniverseOperatorContext | None = None) -> UniverseOperatorContext:
        return self.require_enabled(context)

    def check(self, *, actor_id: str | None = None, run_id: str | None = None,
              lock_id: str | None = None, audit_id: str | None = None) -> UniverseOperatorContext:
        return self.require_enabled(UniverseOperatorContext(actor_id or "", run_id, lock_id, audit_id))

    def assert_writes_allowed(self, context: UniverseOperatorContext | None = None) -> UniverseOperatorContext:
        return self.require_enabled(context)


__all__ = [
    "UniverseIngestionWritesDisabled", "UniverseOperatorContext", "UniverseOperatorContextRequired",
    "UniverseWriteError", "UniverseWriteGuard",
]
