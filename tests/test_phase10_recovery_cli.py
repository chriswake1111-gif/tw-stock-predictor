import json
import subprocess
import sys
from datetime import datetime, timezone

from src.domain.data_foundation import IngestionRun, TriggerType
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


def test_ingestion_cli_returns_nonzero_when_resource_is_blocked(tmp_path):
    db_path = tmp_path / "blocked.db"
    service = ProductionIngestionService(str(db_path))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    holder = IngestionRun(
        ingestion_run_id="run.cli-holder", started_at=now,
        trigger_type=TriggerType.MANUAL, runner_version="test",
        requested_resources=("twse.market-turnover",),
        actor_id="internal.test",
    )
    service.foundation.add_run(holder)
    service.foundation.acquire_resource_lock(
        "twse.market-turnover", holder.ingestion_run_id, now, lease_seconds=3600
    )
    result = subprocess.run(
        [
            sys.executable, "tools/ingest_production_data.py",
            "--database", str(db_path), "official-daily",
            "--trade-date", "2026-08-11",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "blocked"
