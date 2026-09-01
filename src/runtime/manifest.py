"""Non-circular package and distribution manifest helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


INTERNAL_MANIFEST_VERSION = "tw_stock_internal_manifest_v1"
EXTERNAL_MANIFEST_VERSION = "tw_stock_external_distribution_manifest_v1"


class ManifestError(RuntimeError):
    code = "package_manifest_invalid"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_normalized_text(path: Path) -> str:
    """Hash text using the same newline-normalized contract as migrations."""

    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ManifestError(f"resource outside package root: {path}") from exc
    return relative.as_posix()


def _resource_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def _iter_files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return ()
    return (item for item in sorted(directory.rglob("*")) if item.is_file())


def build_internal_manifest(
    resource_root: str | Path,
    *,
    app_version: str = "unknown",
    build_sha: str = "unknown",
    migration_records: Iterable[dict[str, Any]] = (),
    frontend_dist: str | Path | None = None,
    model_rules_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(resource_root).resolve(strict=False)
    resources: list[dict[str, Any]] = []
    candidates: list[Path] = []
    config_path = root / "config" / "config.yaml"
    if config_path.is_file():
        candidates.append(config_path)
    if model_rules_path:
        candidates.append(Path(model_rules_path))
    else:
        default_rules = root / "config" / "model_rules.yaml"
        if default_rules.is_file():
            candidates.append(default_rules)
    candidates.extend(_iter_files(root / "migrations"))
    frontend = Path(frontend_dist) if frontend_dist else root / "frontend" / "dist"
    candidates.extend(_iter_files(frontend))
    for path in sorted({item.resolve(strict=False) for item in candidates}):
        if path.is_file():
            resources.append(_resource_record(root, path))
    migrations: list[dict[str, Any]] = []
    for item in migration_records:
        record = dict(item)
        migration_path = Path(str(record.get("path", "")))
        if not migration_path.is_absolute():
            migration_path = root / migration_path
        normalized: dict[str, Any] = {
            "version_id": str(record.get("version_id", "")),
            "path": _relative(root, migration_path),
            "checksum_sha256": str(record.get("checksum_sha256", "")),
        }
        migrations.append(normalized)
    return {
        "manifest_version": INTERNAL_MANIFEST_VERSION,
        "scope": "internal_package_resources",
        "app_version": app_version,
        "build_sha": build_sha,
        "resources": resources,
        "migrations": migrations,
        "external_artifact_hashes": {},
        "installer_sha256": None,
        "self_sha256": None,
    }


def validate_internal_manifest(
    manifest: dict[str, Any],
    resource_root: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ManifestError("internal manifest is not an object")
    if manifest.get("manifest_version") != INTERNAL_MANIFEST_VERSION:
        raise ManifestError("internal manifest version is unsupported")
    if manifest.get("scope") != "internal_package_resources":
        raise ManifestError("internal manifest scope is invalid")
    if manifest.get("installer_sha256") not in {None, ""}:
        raise ManifestError("internal manifest must not contain installer hash")
    if manifest.get("self_sha256") not in {None, ""}:
        raise ManifestError("internal manifest must not self-hash")
    root = Path(resource_root).resolve(strict=False)
    expected_manifest = Path(manifest_path).resolve(strict=False) if manifest_path else None
    resources = manifest.get("resources", [])
    migrations = manifest.get("migrations", [])
    if not isinstance(resources, list) or not isinstance(migrations, list):
        raise ManifestError("internal manifest records are invalid")
    checked = 0
    seen_paths: set[str] = set()
    for record in resources:
        if not isinstance(record, dict):
            raise ManifestError("internal manifest resource record is invalid")
        relative = str(record.get("path", ""))
        if not relative or Path(relative).is_absolute() or relative.startswith("../"):
            raise ManifestError(f"internal manifest path is invalid: {relative}")
        path = (root / relative).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ManifestError(f"internal manifest path escapes root: {relative}") from exc
        if relative in seen_paths:
            raise ManifestError(f"internal manifest resource is duplicated: {relative}")
        seen_paths.add(relative)
        if expected_manifest and path == expected_manifest:
            raise ManifestError("internal manifest cannot include itself")
        if not path.is_file():
            raise ManifestError(f"package resource missing: {relative}")
        if sha256_file(path) != record.get("sha256"):
            raise ManifestError(f"package resource checksum mismatch: {relative}")
        if not isinstance(record.get("size"), int) or record["size"] < 0:
            raise ManifestError(f"package resource size is invalid: {relative}")
        if path.stat().st_size != record["size"]:
            raise ManifestError(f"package resource size mismatch: {relative}")
        checked += 1
    migrations_checked = 0
    for record in migrations:
        if not isinstance(record, dict):
            raise ManifestError("internal manifest migration record is invalid")
        relative = str(record.get("path", ""))
        if not relative or Path(relative).is_absolute() or relative.startswith("../"):
            raise ManifestError(f"internal manifest migration path is invalid: {relative}")
        path = (root / relative).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ManifestError(f"internal manifest migration escapes root: {relative}") from exc
        if not path.is_file():
            raise ManifestError(f"package migration missing: {relative}")
        if _sha256_normalized_text(path) != record.get("checksum_sha256"):
            raise ManifestError(f"package migration checksum mismatch: {relative}")
        migrations_checked += 1
    return {
        "status": "valid",
        "manifest_version": INTERNAL_MANIFEST_VERSION,
        "resources_checked": checked,
        "migrations_checked": migrations_checked,
    }


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    destination = Path(path).resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def build_external_distribution_manifest(
    installer_path: str | Path,
    *,
    app_version: str,
    build_sha: str,
    output_path: str | Path | None = None,
    onedir_archive_path: str | Path | None = None,
    internal_manifest_path: str | Path | None = None,
    artifact_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    installer = Path(installer_path).resolve(strict=False)
    if not installer.is_file():
        raise ManifestError("final installer bytes do not exist")
    result: dict[str, Any] = {
        "manifest_version": EXTERNAL_MANIFEST_VERSION,
        "scope": "final_distribution_artifacts",
        "app_version": app_version,
        "build_sha": build_sha,
        "installer": {
            "filename": installer.name,
            "sha256": sha256_file(installer),
            "size": installer.stat().st_size,
        },
    }
    if onedir_archive_path:
        archive = Path(onedir_archive_path).resolve(strict=False)
        if archive.is_file():
            result["onedir_archive"] = {
                "filename": archive.name,
                "sha256": sha256_file(archive),
                "size": archive.stat().st_size,
            }
    if internal_manifest_path:
        internal = Path(internal_manifest_path).resolve(strict=False)
        if not internal.is_file():
            raise ManifestError("internal package manifest does not exist")
        result["internal_manifest"] = {
            "filename": internal.name,
            "sha256": sha256_file(internal),
            "size": internal.stat().st_size,
        }
    artifacts: list[dict[str, Any]] = []
    for artifact_path in artifact_paths:
        artifact = Path(artifact_path).resolve(strict=False)
        if not artifact.is_file():
            raise ManifestError(f"distribution artifact does not exist: {artifact}")
        artifacts.append({
            "filename": artifact.name,
            "sha256": sha256_file(artifact),
            "size": artifact.stat().st_size,
        })
    if artifacts:
        result["artifacts"] = artifacts
    if output_path:
        write_manifest(output_path, result)
    return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("package manifest cannot be read") from exc


__all__ = [
    "EXTERNAL_MANIFEST_VERSION",
    "INTERNAL_MANIFEST_VERSION",
    "ManifestError",
    "build_external_distribution_manifest",
    "build_internal_manifest",
    "load_manifest",
    "sha256_file",
    "validate_internal_manifest",
    "write_manifest",
]
