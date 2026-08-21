"""Guarded operator/CLI-only Universe ingestion composition."""

from __future__ import annotations

from typing import Any

from src.repositories.universe_repository import UniverseIngestionRepository
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard


class UniverseIngestionService:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseIngestionRepository | None = None,
                 guard: UniverseWriteGuard | None = None):
        self.guard = guard or UniverseWriteGuard()
        self.repository = repository or UniverseIngestionRepository(db_path, guard=self.guard)

    def ingest_revision(self, *, context: UniverseOperatorContext, **payload: Any) -> dict[str, Any]:
        self.guard.before_mutation(context)
        return self.repository.add_revision(context=context, **payload)


__all__ = ["UniverseIngestionService"]
