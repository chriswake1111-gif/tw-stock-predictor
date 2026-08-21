"""Read-only orchestration for the Universe identity/context surface."""

from __future__ import annotations

from typing import Any

from src.domain.universe import UniverseVenue, validate_knowledge_cutoff_at
from src.repositories.universe_repository import UniverseRepository


class UniverseService:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseRepository | None = None):
        # Deliberately do not instantiate ProductionIngestionService or migrate/register rows.
        self.repository = repository or UniverseRepository(db_path)

    def instrument(self, canonical_symbol: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.repository.get_by_canonical(canonical_symbol, knowledge_cutoff_at=validate_knowledge_cutoff_at(knowledge_cutoff_at), current=current)

    def get_instrument(self, canonical_symbol: str, **kwargs: Any) -> dict[str, Any]:
        return self.instrument(canonical_symbol, **kwargs)

    def resolve(self, *, official_code: str, venue: UniverseVenue | str, knowledge_cutoff_at: str,
                current: bool = False) -> dict[str, Any]:
        return self.repository.resolve(official_code=official_code, venue=venue,
                                      knowledge_cutoff_at=validate_knowledge_cutoff_at(knowledge_cutoff_at), current=current)

    def list(self, *, knowledge_cutoff_at: str, query: str | None = None,
             venue: UniverseVenue | str | None = None, security_type: str | None = None,
             listing_status: str | None = None, limit: int = 25, cursor: str | None = None,
             current: bool = False) -> dict[str, Any]:
        return self.repository.list_instruments(knowledge_cutoff_at=validate_knowledge_cutoff_at(knowledge_cutoff_at), query=query, venue=venue,
                                                security_type=security_type, listing_status=listing_status, limit=limit, cursor=cursor, current=current)

    def list_instruments(self, **kwargs: Any) -> dict[str, Any]:
        return self.list(**kwargs)


__all__ = ["UniverseService"]
