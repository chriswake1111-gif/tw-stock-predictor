from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


PHASE13_RESOURCES = {
    "twse-universe-master": "twse-universe-official",
    "twse-universe-termination": "twse-universe-official",
    "tpex-universe-master": "tpex-universe-official",
    "tpex-universe-operational": "tpex-universe-official",
}


def seed_raw_provenance(db_path: str | Path, resource_ids: tuple[str, ...] | None = None) -> dict[str, tuple[str, str]]:
    """Seed explicit Phase 10 HASH_ONLY provenance for isolated Phase 13 tests."""
    selected = resource_ids or tuple(PHASE13_RESOURCES)
    values: dict[str, tuple[str, str]] = {}
    with sqlite3.connect(db_path) as conn:
        for resource_id in selected:
            provider_id = PHASE13_RESOURCES[resource_id]
            token = resource_id.replace("-", "_")
            raw_id = f"raw-phase13-{token}"
            raw_hash = hashlib.sha256(f"phase13:{resource_id}".encode()).hexdigest()
            schema_hash = hashlib.sha256(f"schema:{resource_id}".encode()).hexdigest()
            conn.execute(
                """
                INSERT OR IGNORE INTO raw_resource_revisions VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    raw_id, f"identity-{token}", provider_id, resource_id, "fixture",
                    "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z",
                    "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z", raw_hash,
                    "1", schema_hash, "hash_only", None, "fresh", "eligible", None,
                    "phase13 fixture provenance",
                ),
            )
            values[resource_id] = (raw_id, raw_hash)
    return values


def raw_for(db_path: str | Path, resource_id: str) -> tuple[str, str]:
    return seed_raw_provenance(db_path, (resource_id,))[resource_id]
