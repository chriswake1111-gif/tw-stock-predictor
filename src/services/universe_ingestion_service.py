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
        if not payload.get("raw_resource_revision_id") or not payload.get("raw_payload_sha256"):
            raise UniverseRawProvenanceRequired()
        return self.repository.add_revision(
            context=context, idempotency_key=str(idempotency_key).strip(), **payload
        )

    def ingest_resource_revision(self, *, context: UniverseOperatorContext,
                                 idempotency_key: str | None = None,
                                 **payload: Any) -> dict[str, Any]:
        self.guard.before_mutation(context)
        if idempotency_key is None or not str(idempotency_key).strip():
            raise UniverseIdempotencyRequired()
        if not payload.get("raw_resource_revision_id") or not payload.get("raw_payload_sha256"):
            raise UniverseRawProvenanceRequired()
        return self.repository.add_resource_revision(
            context=context, idempotency_key=str(idempotency_key).strip(), **payload
        )


__all__ = ["UniverseIngestionService"]
