"""Central fail-closed boundary for Phase 13 Universe mutations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.installed_data_operations import InstalledWriteAuthorization


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

    def __init__(
        self,
        enabled: bool | None = None,
        authorization: InstalledWriteAuthorization | None = None,
        active_instance_id: str | None = None,
    ) -> None:
        if enabled is None:
            enabled = os.getenv(self.ENV_NAME, "false").strip().lower() == "true"
        self.enabled = bool(enabled)
        self.authorization = authorization
        self.active_instance_id = active_instance_id

    def require_enabled(
        self,
        context: UniverseOperatorContext | None = None,
        resource_id: str | None = None,
        current_instance_id: str | None = None,
    ) -> UniverseOperatorContext:
        authorized_via_capability = (
            self.authorization is not None
            and self.authorization.is_valid(
                current_instance_id or self.active_instance_id,
                resource_id,
            )
        )
        if not self.enabled and not authorized_via_capability:
            raise UniverseIngestionWritesDisabled()
        if context is None:
            raise UniverseOperatorContextRequired("actor_id")
        # Phase 13 mutations are operator-run artifacts.  An actor by itself
        # is not an auditable write context: every mutation must carry the
        # run, lock and audit references that identify the approved operation.
        for field_name in ("actor_id", "run_id", "lock_id", "audit_id"):
            value = getattr(context, field_name)
            if value is None or not str(value).strip():
                raise UniverseOperatorContextRequired(field_name)
        return UniverseOperatorContext(
            actor_id=str(context.actor_id).strip(),
            run_id=str(context.run_id).strip(),
            lock_id=str(context.lock_id).strip(),
            audit_id=str(context.audit_id).strip(),
        )

    # Names used by services/repositories and simple tests.
    def before_mutation(
        self,
        context: UniverseOperatorContext | None = None,
        resource_id: str | None = None,
        current_instance_id: str | None = None,
    ) -> UniverseOperatorContext:
        return self.require_enabled(context, resource_id=resource_id, current_instance_id=current_instance_id)

    def check(
        self,
        *,
        actor_id: str | None = None,
        run_id: str | None = None,
        lock_id: str | None = None,
        audit_id: str | None = None,
        resource_id: str | None = None,
        current_instance_id: str | None = None,
    ) -> UniverseOperatorContext:
        return self.require_enabled(
            UniverseOperatorContext(actor_id or "", run_id, lock_id, audit_id),
            resource_id=resource_id,
            current_instance_id=current_instance_id,
        )

    def assert_writes_allowed(
        self,
        context: UniverseOperatorContext | None = None,
        resource_id: str | None = None,
        current_instance_id: str | None = None,
    ) -> UniverseOperatorContext:
        return self.require_enabled(context, resource_id=resource_id, current_instance_id=current_instance_id)


__all__ = [
    "UniverseIngestionWritesDisabled",
    "UniverseOperatorContext",
    "UniverseOperatorContextRequired",
    "UniverseWriteError",
    "UniverseWriteGuard",
]
