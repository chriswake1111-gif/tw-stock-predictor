"""Repository for durable Phase 19 installed data operations and item lineage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Sequence

from src.domain.installed_data_operations import (
    InstalledItemRow,
    InstalledOperationRow,
    InstalledOperationStatus,
    OperationActiveConflict,
    OperationAuthorizationRevoked,
    OperationCancelled,
    OperationNotFound,
    OperationStateInvalid,
)
from src.domain.valuation import utc_now_timestamp


_CAPABILITY_TO_STORAGE_RESOURCE = {
    "twse.t187ap03_L": "twse-universe-master",
    "tpex.mopsfin_t187ap03_O": "tpex-universe-master",
}
_STORAGE_TO_CAPABILITY_RESOURCE = {
    "twse-universe-master": "twse.t187ap03_L",
    "tpex-universe-master": "tpex.mopsfin_t187ap03_O",
}


class InstalledDataOperationsRepository:
    def __init__(self, db_path: str = "data/cache.db") -> None:
        self.db_path = db_path

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_operation(
        self,
        operation_id: str,
        operation_type: str,
        lease_owner_id: str,
        lease_duration_seconds: int = 60,
        target_symbols: Sequence[str] | None = None,
    ) -> InstalledOperationRow:
        now = utc_now_timestamp()
        lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds)
        ).isoformat().replace("+00:00", "Z")
        symbols_json = json.dumps(list(target_symbols or []), ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT * FROM installed_data_operations
                WHERE status IN ('pending', 'running', 'cancelling')
                LIMIT 1
                """
            ).fetchone()
            if active:
                raise OperationActiveConflict(
                    f"Another operation {active['operation_id']} is currently {active['status']}"
                )
            try:
                conn.execute(
                    """
                    INSERT INTO installed_data_operations (
                        operation_id, operation_type, status, current_stage,
                        lease_owner_id, lease_expires_at, target_symbols_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        operation_type,
                        InstalledOperationStatus.RUNNING.value,
                        "prerequisites_calendar",
                        lease_owner_id,
                        lease_expires_at,
                        symbols_json,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationActiveConflict("Another operation is currently active") from exc

        return InstalledOperationRow(
            operation_id=operation_id,
            operation_type=operation_type,
            status=InstalledOperationStatus.RUNNING.value,
            current_stage="prerequisites_calendar",
            lease_owner_id=lease_owner_id,
            lease_expires_at=lease_expires_at,
            target_symbols_json=symbols_json,
            created_at=now,
            updated_at=now,
        )

    def get_operation_by_id(self, operation_id: str) -> InstalledOperationRow | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM installed_data_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                return None
            return InstalledOperationRow(
                operation_id=row["operation_id"],
                operation_type=row["operation_type"],
                status=row["status"],
                current_stage=row["current_stage"],
                lease_owner_id=row["lease_owner_id"],
                lease_expires_at=row["lease_expires_at"],
                target_symbols_json=row["target_symbols_json"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                error_detail=row["error_detail"],
            )

    def get_active_operation(self) -> InstalledOperationRow | None:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM installed_data_operations
                WHERE status IN ('pending', 'running', 'cancelling')
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            return InstalledOperationRow(
                operation_id=row["operation_id"],
                operation_type=row["operation_type"],
                status=row["status"],
                current_stage=row["current_stage"],
                lease_owner_id=row["lease_owner_id"],
                lease_expires_at=row["lease_expires_at"],
                target_symbols_json=row["target_symbols_json"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                error_detail=row["error_detail"],
            )

    def extend_lease(
        self,
        operation_id: str,
        lease_duration_seconds: int = 60,
        expected_owner_id: str | None = None,
    ) -> str:
        now = utc_now_timestamp()
        lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds)
        ).isoformat().replace("+00:00", "Z")

        with self._get_connection() as conn:
            if expected_owner_id:
                res = conn.execute(
                    """
                    UPDATE installed_data_operations
                    SET lease_expires_at = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'running' AND lease_owner_id = ?
                    """,
                    (lease_expires_at, now, operation_id, expected_owner_id),
                )
                if res.rowcount == 0:
                    row = conn.execute(
                        "SELECT * FROM installed_data_operations WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone()
                    if not row:
                        raise OperationNotFound(f"Operation {operation_id} not found")
                    if row["status"] in ("cancelling", "cancelled"):
                        raise OperationCancelled(f"Operation {operation_id} cancelled")
                    if row["lease_owner_id"] != expected_owner_id:
                        raise OperationAuthorizationRevoked("lease_owner_mismatch")
                    raise OperationStateInvalid(f"Operation status is {row['status']}")
            else:
                conn.execute(
                    """
                    UPDATE installed_data_operations
                    SET lease_expires_at = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'running'
                    """,
                    (lease_expires_at, now, operation_id),
                )
        return lease_expires_at

    def transition_stage(
        self,
        operation_id: str,
        stage: str,
        lease_duration_seconds: int = 60,
        expected_owner_id: str | None = None,
    ) -> str:
        now = utc_now_timestamp()
        lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds)
        ).isoformat().replace("+00:00", "Z")

        with self._get_connection() as conn:
            if expected_owner_id:
                res = conn.execute(
                    """
                    UPDATE installed_data_operations
                    SET current_stage = ?, lease_expires_at = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'running' AND lease_owner_id = ?
                    """,
                    (stage, lease_expires_at, now, operation_id, expected_owner_id),
                )
                if res.rowcount == 0:
                    row = conn.execute(
                        "SELECT * FROM installed_data_operations WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone()
                    if not row:
                        raise OperationNotFound(f"Operation {operation_id} not found")
                    if row["status"] in ("cancelling", "cancelled"):
                        raise OperationCancelled(f"Operation {operation_id} cancelled")
                    if row["lease_owner_id"] != expected_owner_id:
                        raise OperationAuthorizationRevoked("lease_owner_mismatch")
                    raise OperationStateInvalid(f"Operation status is {row['status']}")
            else:
                conn.execute(
                    """
                    UPDATE installed_data_operations
                    SET current_stage = ?, lease_expires_at = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'running'
                    """,
                    (stage, lease_expires_at, now, operation_id),
                )
        return lease_expires_at

    def finalize_operation(
        self, operation_id: str, status: str, error_detail: str | None = None
    ) -> None:
        now = utc_now_timestamp()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE installed_data_operations
                SET status = ?, current_stage = 'completed', completed_at = ?, error_detail = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (status, now, error_detail, now, operation_id),
            )

    def request_cancel(self, operation_id: str) -> bool:
        now = utc_now_timestamp()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE installed_data_operations
                SET status = 'cancelling', updated_at = ?
                WHERE operation_id = ? AND status = 'running'
                """,
                (now, operation_id),
            )
            return cursor.rowcount > 0

    def create_item(
        self, item_id: str, operation_id: str, stage: str, resource_id: str
    ) -> InstalledItemRow:
        now = utc_now_timestamp()
        storage_resource = _CAPABILITY_TO_STORAGE_RESOURCE.get(resource_id, resource_id)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO installed_data_operation_items (
                    item_id, operation_id, stage, resource_id,
                    status, attempt_count, created_at
                ) VALUES (?, ?, ?, ?, 'running', 1, ?)
                """,
                (item_id, operation_id, stage, storage_resource, now),
            )
        return InstalledItemRow(
            item_id=item_id,
            operation_id=operation_id,
            stage=stage,
            resource_id=resource_id,
            status="running",
            attempt_count=1,
            created_at=now,
        )

    def update_item(
        self,
        item_id: str,
        status: str,
        raw_resource_revision_id: str | None = None,
        ingestion_run_id: str | None = None,
        ingestion_run_item_id: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        now = utc_now_timestamp()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE installed_data_operation_items
                SET status = ?, raw_resource_revision_id = ?, ingestion_run_id = ?,
                    ingestion_run_item_id = ?, completed_at = ?, error_code = ?, error_detail = ?
                WHERE item_id = ?
                """,
                (
                    status,
                    raw_resource_revision_id,
                    ingestion_run_id,
                    ingestion_run_item_id,
                    now,
                    error_code,
                    error_detail,
                    item_id,
                ),
            )

    def list_items_by_operation(self, operation_id: str) -> list[InstalledItemRow]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM installed_data_operation_items
                WHERE operation_id = ?
                ORDER BY created_at ASC
                """,
                (operation_id,),
            ).fetchall()
            return [
                InstalledItemRow(
                    item_id=row["item_id"],
                    operation_id=row["operation_id"],
                    stage=row["stage"],
                    resource_id=_STORAGE_TO_CAPABILITY_RESOURCE.get(
                        row["resource_id"], row["resource_id"]
                    ),
                    status=row["status"],
                    raw_resource_revision_id=row["raw_resource_revision_id"],
                    ingestion_run_id=row["ingestion_run_id"],
                    ingestion_run_item_id=row["ingestion_run_item_id"],
                    attempt_count=row["attempt_count"],
                    created_at=row["created_at"],
                    completed_at=row["completed_at"],
                    error_code=row["error_code"],
                    error_detail=row["error_detail"],
                )
                for row in rows
            ]

    def recover_interrupted_operations(self) -> int:
        now = utc_now_timestamp()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE installed_data_operations
                SET status = 'interrupted', completed_at = ?, error_detail = 'interrupted_by_server_restart', updated_at = ?
                WHERE status IN ('pending', 'running', 'cancelling')
                """,
                (now, now),
            )
            return cursor.rowcount
