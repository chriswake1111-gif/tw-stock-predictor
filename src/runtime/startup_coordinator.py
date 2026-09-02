"""Single packaged startup owner for resource and database preparation."""

from __future__ import annotations

import os
import inspect
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.services.evidence_backup_service import EvidenceBackupService

from .database_state import DatabaseClassification, DatabaseState, classify_database
from .diagnostics import DiagnosticLogger
from .manifest import ManifestError, load_manifest, sha256_file, validate_internal_manifest
from .settings import RuntimeSettings


class StartupFailure(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class StartupResult:
    status: str
    database: DatabaseClassification | None
    failure_code: str | None = None
    legacy_archive: Path | None = None
    pre_upgrade_backup: Path | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.database is not None and self.database.is_ready_for_reads

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "failure_code": self.failure_code,
            "database": self.database.to_dict() if self.database else None,
            "legacy_archive": str(self.legacy_archive) if self.legacy_archive else None,
            "pre_upgrade_backup": str(self.pre_upgrade_backup) if self.pre_upgrade_backup else None,
        }


class StartupCoordinator:
    """The only packaged owner of preflight, migration, and DB readiness."""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        logger: DiagnosticLogger | None = None,
        classifier: Callable[..., DatabaseClassification] = classify_database,
        migrator: Callable[..., dict[str, object]] = apply_valuation_migration,
    ) -> None:
        self.settings = settings
        self.paths = settings.paths
        self.logger = logger
        self.classifier = classifier
        self.migrator = migrator

    def _log(self, code: str, message: str = "", **context: object) -> None:
        if self.logger:
            self.logger.emit(code, phase="startup", message=message, **context)

    def _call_classifier(self, database: Path) -> DatabaseClassification:
        parameters = inspect.signature(self.classifier).parameters
        accepts_resource_root = (
            "resource_root" in parameters
            or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        )
        if accepts_resource_root:
            return self.classifier(
                database,
                resource_root=str(self.paths.resource_root),
            )
        return self.classifier(database)

    def _call_migrator(self, database: Path) -> dict[str, object]:
        parameters = inspect.signature(self.migrator).parameters
        accepts_resource_root = (
            "resource_root" in parameters
            or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        )
        if accepts_resource_root:
            return self.migrator(
                str(database),
                resource_root=str(self.paths.resource_root),
            )
        return self.migrator(str(database))

    def _preflight(self) -> None:
        paths = self.paths
        if not self.settings.packaged:
            return
        if not paths.frontend_dist.is_dir() or not (paths.frontend_dist / "index.html").is_file():
            raise StartupFailure("frontend_asset_missing")
        migration_dir = paths.migrations_dir
        if not migration_dir.is_dir():
            raise StartupFailure("package_resource_missing")
        for required in (
            paths.model_rules_path,
            paths.resource_root / "config" / "config.yaml",
        ):
            if not required.is_file():
                raise StartupFailure("package_resource_missing")
        configured_manifest = os.getenv("TW_STOCK_INTERNAL_MANIFEST_PATH")
        manifest_path = Path(configured_manifest) if configured_manifest else paths.resource_root / "package-manifest.json"
        if not manifest_path.is_absolute():
            manifest_path = paths.resource_root / manifest_path
        manifest_path = manifest_path.resolve(strict=False)
        try:
            manifest = load_manifest(manifest_path)
            validate_internal_manifest(manifest, paths.resource_root, manifest_path=manifest_path)
        except ManifestError as exc:
            raise StartupFailure("package_manifest_invalid", str(exc)) from exc

    def preflight(self) -> None:
        """Validate immutable packaged resources without touching the database."""

        self._preflight()

    @staticmethod
    def _unique_path(directory: Path, prefix: str, suffix: str = ".db") -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{prefix}-{uuid.uuid4().hex}{suffix}"

    def _seed_user_config(self) -> None:
        if not self.settings.packaged:
            return
        source = self.paths.resource_root / "config" / "config.yaml"
        destination = self.paths.config_path
        if source.resolve(strict=False) == destination.resolve(strict=False):
            return
        if destination.exists():
            if not destination.is_file():
                raise StartupFailure("user_config_invalid")
            return
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            if sha256_file(source) != sha256_file(temporary):
                raise StartupFailure("user_config_seed_hash_mismatch")
            os.replace(temporary, destination)
        except StartupFailure:
            raise
        except Exception as exc:
            raise StartupFailure("user_config_seed_failed", str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _preserve_legacy(
        self,
        source: Path,
        source_hash: str | None,
    ) -> tuple[Path, Path, Path | None, Path | None]:
        legacy_dir = self.paths.backup_dir / "legacy"
        archive = self._unique_path(legacy_dir, "legacy-source")
        shutil.copy2(source, archive)
        archived_hash = sha256_file(archive)
        if source_hash and archived_hash != source_hash:
            raise StartupFailure("legacy_archive_hash_mismatch")
        source_metadata = Path(f"{source}.meta.json")
        archive_metadata: Path | None = None
        if source_metadata.is_file():
            archive_metadata = Path(f"{archive}.meta.json")
            shutil.copy2(source_metadata, archive_metadata)
        quarantine = self._unique_path(legacy_dir, "legacy-canonical")
        quarantine_metadata = Path(f"{quarantine}.meta.json") if archive_metadata else None
        return archive, quarantine, archive_metadata, quarantine_metadata

    def _create_v2_from_legacy(
        self,
        source: Path,
        classification: DatabaseClassification,
    ) -> tuple[DatabaseClassification, Path]:
        archive, quarantine, _, quarantine_metadata = self._preserve_legacy(
            source,
            classification.file_sha256,
        )
        staging = self._unique_path(self.paths.data_dir, ".phase18-v2-staging")
        source_metadata = Path(f"{source}.meta.json")
        try:
            self._call_migrator(staging)
            staged = self._call_classifier(staging)
            if staged.state is not DatabaseState.KNOWN_V2_CURRENT:
                raise StartupFailure("legacy_v2_staging_not_current")
            try:
                os.replace(source, quarantine)
                if source_metadata.is_file() and quarantine_metadata:
                    os.replace(source_metadata, quarantine_metadata)
                os.replace(staging, source)
            except Exception as exc:
                if not source.exists() and quarantine.exists():
                    os.replace(quarantine, source)
                if quarantine_metadata and not source_metadata.exists() and quarantine_metadata.exists():
                    os.replace(quarantine_metadata, source_metadata)
                raise StartupFailure("legacy_activation_failed", str(exc)) from exc
            return self._call_classifier(source), archive
        except StartupFailure:
            raise
        except Exception as exc:
            raise StartupFailure("legacy_database_preservation_failed", str(exc)) from exc

    def _finalize_ready(
        self,
        canonical: Path,
        classification: DatabaseClassification,
        *,
        pre_upgrade_backup: Path | None = None,
        legacy_archive: Path | None = None,
    ) -> StartupResult:
        try:
            InstalledDataOperationsRepository(str(canonical)).recover_interrupted_operations()
        except Exception as exc:
            raise StartupFailure("operation_recovery_failed", str(exc)) from exc
        return StartupResult(
            "ready",
            classification,
            pre_upgrade_backup=pre_upgrade_backup,
            legacy_archive=legacy_archive,
        )

    def prepare(self) -> StartupResult:
        try:
            self.paths.ensure_user_dirs()
            self._preflight()
            self._seed_user_config()
            canonical = self.paths.database_path
            classification = self._call_classifier(canonical)
            if classification.state is DatabaseState.CORRUPT_UNKNOWN:
                raise StartupFailure("database_corrupt_unknown")
            if classification.state is DatabaseState.KNOWN_V2_CURRENT:
                return self._finalize_ready(canonical, classification)
            if classification.state is DatabaseState.FRESH:
                self._log("database_fresh", database=str(canonical))
                self._call_migrator(canonical)
                classification = self._call_classifier(canonical)
                if classification.state is not DatabaseState.KNOWN_V2_CURRENT:
                    raise StartupFailure("fresh_database_not_current")
                return self._finalize_ready(canonical, classification)
            if classification.state is DatabaseState.KNOWN_V2_UPGRADEABLE:
                backup = self._unique_path(self.paths.backup_dir, "pre-upgrade")
                EvidenceBackupService.backup(
                    str(canonical),
                    str(backup),
                    reason="pre_upgrade",
                )
                self._log("database_pre_upgrade_backup", backup=str(backup))
                self._call_migrator(canonical)
                classification = self._call_classifier(canonical)
                if classification.state is not DatabaseState.KNOWN_V2_CURRENT:
                    raise StartupFailure("upgraded_database_not_current")
                return self._finalize_ready(canonical, classification, pre_upgrade_backup=backup)
            if classification.state is DatabaseState.LEGACY:
                self._log("legacy_database_preserve", database=str(canonical))
                current, archive = self._create_v2_from_legacy(canonical, classification)
                if current.state is not DatabaseState.KNOWN_V2_CURRENT:
                    raise StartupFailure("legacy_database_not_current")
                return self._finalize_ready(canonical, current, legacy_archive=archive)
            raise StartupFailure("database_state_unhandled")
        except StartupFailure as exc:
            self._log(exc.code, str(exc))
            current = None
            try:
                current = self._call_classifier(self.paths.database_path)
            except Exception:
                pass
            return StartupResult("failed", current, failure_code=exc.code)
        except Exception as exc:
            self._log("startup_failed", str(exc))
            current = None
            try:
                current = self._call_classifier(self.paths.database_path)
            except Exception:
                pass
            return StartupResult("failed", current, failure_code="startup_failed")


__all__ = ["StartupCoordinator", "StartupFailure", "StartupResult"]
