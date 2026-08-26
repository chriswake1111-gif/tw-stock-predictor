from __future__ import annotations

from src.repositories.migration_runner import apply_valuation_migration
from src.services.evidence_backup_service import EvidenceBackupService


def test_backup_validation_includes_universe_tables(tmp_path):
    db = tmp_path / "db.sqlite"
    backup = tmp_path / "backup.sqlite"
    restored = tmp_path / "restored.sqlite"
    apply_valuation_migration(str(db))
    result = EvidenceBackupService.backup(str(db), str(backup))
    restored_result = EvidenceBackupService.restore(str(backup), str(restored))
    assert result["status"] == restored_result["status"] == "valid"
    assert "universe_instruments" in result["irreplaceable_counts"]
    assert "universe_ingestion_idempotency" in result["irreplaceable_counts"]
