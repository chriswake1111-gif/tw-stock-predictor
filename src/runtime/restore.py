"""Local-only canonical database recovery and activation."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from src.services.evidence_backup_service import EvidenceBackupService

from .instance import (
    InstanceGuard,
    InstanceOwnershipError,
    process_exists,
    read_descriptor,
)
from .manifest import sha256_file


class RestoreError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _metadata_path(path: Path) -> Path:
    return Path(f"{path}.meta.json")


def _copy_verified(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(source) != sha256_file(destination):
        raise RestoreError("restore_copy_hash_mismatch")


def _ensure_no_active_writer(runtime_dir: Path) -> None:
    descriptor_path = runtime_dir / "instance.json"
    if not descriptor_path.is_file():
        return
    try:
        descriptor = read_descriptor(descriptor_path)
        server_pid = int(descriptor.get("server_pid", 0))
    except (InstanceOwnershipError, TypeError, ValueError) as exc:
        raise RestoreError("restore_descriptor_invalid", str(exc)) from exc
    if server_pid > 0 and process_exists(server_pid):
        raise RestoreError("restore_writer_active")


def _preserve_previous(canonical: Path, operation_dir: Path) -> tuple[Path | None, Path | None]:
    if not canonical.is_file():
        return None, None
    previous = operation_dir / "prior-canonical.db"
    _copy_verified(canonical, previous)
    previous_metadata: Path | None = None
    metadata = _metadata_path(canonical)
    if metadata.is_file():
        previous_metadata = operation_dir / "prior-canonical.db.meta.json"
        shutil.copy2(metadata, previous_metadata)
    return previous, previous_metadata


def _activate_owned(
    candidate: Path,
    canonical: Path,
    *,
    backup_root: Path,
    runtime_dir: Path,
) -> dict[str, Any]:
    if not candidate.is_file():
        raise RestoreError("restore_candidate_missing")
    validation = EvidenceBackupService.validate(str(candidate))
    if validation.get("status") != "valid":
        raise RestoreError("restore_candidate_validation_failed")
    if "backup_metadata" not in validation:
        raise RestoreError("restore_candidate_metadata_missing")

    operation_id = uuid.uuid4().hex
    operation_dir = backup_root / "restore-staging" / operation_id
    operation_dir.mkdir(parents=True, exist_ok=False)
    staged = operation_dir / "candidate.db"
    _copy_verified(candidate, staged)
    candidate_metadata = _metadata_path(candidate)
    staged_metadata = _metadata_path(staged)
    if candidate_metadata.is_file():
        shutil.copy2(candidate_metadata, staged_metadata)
    staged_validation = EvidenceBackupService.validate(str(staged))
    previous, previous_metadata = _preserve_previous(canonical, operation_dir)
    displaced = operation_dir / "displaced-canonical.db"
    displaced_metadata = _metadata_path(displaced)
    canonical_metadata = _metadata_path(canonical)
    try:
        if canonical.is_file():
            os.replace(canonical, displaced)
        if canonical_metadata.is_file():
            os.replace(canonical_metadata, displaced_metadata)
        os.replace(staged, canonical)
        if staged_metadata.is_file():
            os.replace(staged_metadata, canonical_metadata)
    except Exception as exc:
        if canonical.is_file():
            os.replace(canonical, operation_dir / "failed-activation.db")
        if canonical_metadata.is_file():
            os.replace(canonical_metadata, operation_dir / "failed-activation.db.meta.json")
        if displaced.is_file() and not canonical.is_file():
            os.replace(displaced, canonical)
        if displaced_metadata.is_file() and not canonical_metadata.is_file():
            os.replace(displaced_metadata, canonical_metadata)
        raise RestoreError("restore_activation_failed", str(exc)) from exc

    try:
        post = EvidenceBackupService.validate(str(canonical))
        if post.get("status") != "valid":
            raise RestoreError("restore_post_activation_validation_failed")
    except Exception as exc:
        if canonical.is_file():
            os.replace(canonical, operation_dir / "failed-postcheck.db")
        if canonical_metadata.is_file():
            os.replace(canonical_metadata, operation_dir / "failed-postcheck.db.meta.json")
        if displaced.is_file() and not canonical.is_file():
            os.replace(displaced, canonical)
        if displaced_metadata.is_file() and not canonical_metadata.is_file():
            os.replace(displaced_metadata, canonical_metadata)
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError("restore_post_activation_validation_failed", str(exc)) from exc

    return {
        "status": "activated",
        "operation_id": operation_id,
        "canonical_path": str(canonical),
        "candidate_path": str(candidate),
        "staging_path": str(operation_dir),
        "prior_canonical_path": str(previous) if previous else None,
        "prior_canonical_metadata_path": str(previous_metadata) if previous_metadata else None,
        "validation": post,
        "reopened_and_revalidated": True,
    }


def activate_candidate(
    candidate_db: str | Path,
    canonical_db: str | Path,
    *,
    backup_root: str | Path,
    runtime_dir: str | Path,
) -> dict[str, Any]:
    """Explicitly activate a validated candidate through local control only."""

    candidate = Path(candidate_db).resolve(strict=False)
    canonical = Path(canonical_db).resolve(strict=False)
    root = Path(backup_root).resolve(strict=False)
    runtime = Path(runtime_dir).resolve(strict=False)
    if candidate == canonical:
        raise RestoreError("restore_candidate_must_differ_from_canonical")
    _ensure_no_active_writer(runtime)
    try:
        with InstanceGuard(runtime):
            _ensure_no_active_writer(runtime)
            return _activate_owned(
                candidate,
                canonical,
                backup_root=root,
                runtime_dir=runtime,
            )
    except InstanceOwnershipError as exc:
        raise RestoreError("restore_instance_ownership_unavailable", str(exc)) from exc


def restore_backup_and_activate(
    backup_db: str | Path,
    canonical_db: str | Path,
    *,
    backup_root: str | Path,
    runtime_dir: str | Path,
) -> dict[str, Any]:
    """Copy a backup to a unique candidate, then activate only explicitly."""

    backup = Path(backup_db).resolve(strict=False)
    canonical = Path(canonical_db).resolve(strict=False)
    root = Path(backup_root).resolve(strict=False)
    runtime = Path(runtime_dir).resolve(strict=False)
    if not backup.is_file():
        raise RestoreError("restore_backup_missing")
    _ensure_no_active_writer(runtime)
    try:
        with InstanceGuard(runtime):
            _ensure_no_active_writer(runtime)
            operation_id = uuid.uuid4().hex
            operation_dir = root / "restore-staging" / operation_id
            operation_dir.mkdir(parents=True, exist_ok=False)
            candidate = operation_dir / "candidate.db"
            EvidenceBackupService.restore(str(backup), str(candidate), reason="recovery")
            result = _activate_owned(
                candidate,
                canonical,
                backup_root=root,
                runtime_dir=runtime,
            )
            result["source_backup_path"] = str(backup)
            result["source_backup_preserved"] = True
            return result
    except InstanceOwnershipError as exc:
        raise RestoreError("restore_instance_ownership_unavailable", str(exc)) from exc


__all__ = [
    "RestoreError",
    "activate_candidate",
    "restore_backup_and_activate",
]
