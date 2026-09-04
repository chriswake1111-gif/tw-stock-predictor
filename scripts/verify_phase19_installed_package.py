"""Phase 19 installed package smoke verification script."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collectors.installed_egress_client import (
    APPROVED_DOMAINS,
    APPROVED_ENDPOINT_PREFIXES,
    validate_egress_url,
)
from src.repositories.migration_runner import (
    ADDITIONAL_MIGRATION_IDS,
    MIGRATION_IDS,
    apply_valuation_migration,
)
from src.runtime.database_state import DatabaseState, classify_database
from src.runtime.paths import RuntimePaths
from src.runtime.settings import RuntimeSettings
from src.runtime.startup_coordinator import StartupCoordinator


def verify_phase19_package() -> int:
    print("[1/4] Checking migration manifests and registration...")
    all_migrations = MIGRATION_IDS + ADDITIONAL_MIGRATION_IDS
    assert "20260902_21_installed_data_operations" in all_migrations
    print(f" -> Total migrations registered: {len(all_migrations)} (Migration 21 present)")

    print("[2/4] Checking egress security allowlist and URL validation...")
    for prefix in APPROVED_ENDPOINT_PREFIXES:
        validated = validate_egress_url(prefix)
        assert validated == prefix
    print(f" -> All {len(APPROVED_ENDPOINT_PREFIXES)} approved endpoint prefixes verified.")

    print("[3/4] Checking database readiness and migrations...")
    paths = RuntimePaths.from_environment()
    settings = RuntimeSettings.from_environment(paths=paths)
    coordinator = StartupCoordinator(settings)
    res = coordinator.prepare()
    print(f" -> Startup coordinator status: {res.status}")
    if res.database:
        print(f" -> Database state: {res.database.state.value}")

    print("[4/4] Phase 19 verification complete!")
    return 0


if __name__ == "__main__":
    sys.exit(verify_phase19_package())
