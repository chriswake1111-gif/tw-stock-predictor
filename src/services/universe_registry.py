"""Read-only registry policy facade for approved Phase 13 resource roles."""

from __future__ import annotations

from typing import Any

from src.collectors.universe_collectors import APPROVED_RESOURCE_KEYS, EXCLUDED_RESOURCE_KEYS
from src.repositories.universe_repository import UniverseStorageUnavailable


class UniverseResourceRegistry:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path

    def list_policies(self) -> list[dict[str, Any]]:
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""SELECT p.*, r.logical_resource_key, r.provider_id, r.market
                                      FROM universe_resource_policies p JOIN data_resources r ON r.resource_id=p.resource_id
                                      ORDER BY r.market, r.logical_resource_key""").fetchall()
        except sqlite3.Error as exc:
            raise UniverseStorageUnavailable(str(exc)) from exc
        return [dict(row) for row in rows]

    @staticmethod
    def validate_resource_key(resource_key: str) -> str:
        key = str(resource_key).strip()
        if key in EXCLUDED_RESOURCE_KEYS or "quote" in key.lower() or "price" in key.lower():
            raise ValueError("quote_source_excluded")
        if key not in APPROVED_RESOURCE_KEYS:
            raise ValueError("universe_source_not_approved")
        return key


__all__ = ["UniverseResourceRegistry"]
