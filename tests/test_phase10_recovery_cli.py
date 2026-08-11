import json
import subprocess
import sys

from src.services.production_ingestion_service import ProductionIngestionService


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "tools/evidence_db_recovery.py", *map(str, args)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_recovery_cli_backup_validate_restore_round_trip(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup" / "evidence.db"
    restored = tmp_path / "restored" / "evidence.db"
    ProductionIngestionService(str(source))

    backed_up = json.loads(run_cli("backup", source, backup).stdout)
    validated = json.loads(run_cli("validate", backup).stdout)
    restored_result = json.loads(run_cli("restore", backup, restored).stdout)

    assert backed_up["integrity_check"] == "ok"
    assert validated == backed_up
    assert restored_result == backed_up
    assert backed_up["operational_provenance_counts"]["data_providers"] == 3
    assert backed_up["operational_provenance_counts"]["data_resources"] == 4
