"""Guarded operator/CLI-only Universe ingestion composition."""

from __future__ import annotations

from typing import Any

from src.repositories.universe_repository import UniverseIdempotencyRequired, UniverseIngestionRepository, UniverseRawProvenanceRequired
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard


class UniverseIngestionService:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseIngestionRepository | None = None,
                 guard: UniverseWriteGuard | None = None):
        self.guard = guard or UniverseWriteGuard()
        self.repository = repository or UniverseIngestionRepository(db_path, guard=self.guard)

    def ingest_revision(self, *, context: UniverseOperatorContext,
                        idempotency_key: str | None = None,
                        **payload: Any) -> dict[str, Any]:
        self.guard.before_mutation(context)
        if idempotency_key is None or not str(idempotency_key).strip():
            raise UniverseIdempotencyRequired()
        raw_id = payload.get("raw_resource_revision_id") or (
            payload.get("payload", {}).get("raw_resource_revision_id")
            if isinstance(payload.get("payload"), dict) else None
        )
        raw_hash = payload.get("raw_payload_sha256") or (
            payload.get("payload", {}).get("raw_payload_sha256")
            if isinstance(payload.get("payload"), dict) else None
        )
        if not raw_id or not raw_hash:
            raise UniverseRawProvenanceRequired()
        call_kwargs = dict(payload)
        if "payload" in call_kwargs and isinstance(call_kwargs["payload"], dict):
            call_kwargs.pop("raw_resource_revision_id", None)
            call_kwargs.pop("raw_payload_sha256", None)
        return self.repository.add_revision(
            context=context, idempotency_key=str(idempotency_key).strip(), **call_kwargs
        )

    def ingest_resource_revision(self, *, context: UniverseOperatorContext,
                                 idempotency_key: str | None = None,
                                 **payload: Any) -> dict[str, Any]:
        self.guard.before_mutation(context)
        if idempotency_key is None or not str(idempotency_key).strip():
            raise UniverseIdempotencyRequired()
        raw_id = payload.get("raw_resource_revision_id") or (
            payload.get("payload", {}).get("raw_resource_revision_id")
            if isinstance(payload.get("payload"), dict) else None
        )
        raw_hash = payload.get("raw_payload_sha256") or (
            payload.get("payload", {}).get("raw_payload_sha256")
            if isinstance(payload.get("payload"), dict) else None
        )
        if not raw_id or not raw_hash:
            raise UniverseRawProvenanceRequired()
        call_kwargs = dict(payload)
        if "payload" in call_kwargs and isinstance(call_kwargs["payload"], dict):
            call_kwargs.pop("raw_resource_revision_id", None)
            call_kwargs.pop("raw_payload_sha256", None)
        return self.repository.add_resource_revision(
            context=context, idempotency_key=str(idempotency_key).strip(), **call_kwargs
        )


__all__ = ["UniverseIngestionService"]
