"""Validate a built Windows package without starting the application.

This is a local, offline gate for package contents, internal resource hashes,
and final artifact hashes.  It never contacts a service and never reads the
user's runtime database unless the caller explicitly points it at a payload
that contains one (which is rejected).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _assert_no_runtime_state(root: Path) -> None:
    forbidden_names = {
        ".env",
        ".env.local",
        "cache.db",
        "cache.sqlite",
        "credentials.json",
        "secrets.json",
    }
    forbidden_dirs = {"data", "backup", "backups", "logs", "runtime"}
    for item in root.rglob("*"):
        if item.name.lower() in forbidden_names:
            _fail(f"package contains mutable or secret runtime file: {item}")
        if item.is_dir() and item.name.lower() in forbidden_dirs:
            _fail(f"package contains mutable runtime directory: {item}")


def _assert_frontend_secret_gate(resource_root: Path) -> int:
    assets = sorted(
        item
        for item in (resource_root / "frontend" / "dist").glob("assets/*")
        if item.is_file() and item.suffix.lower() in {".js", ".css"}
    )
    forbidden = ("X-Admin-API-Key", "EVIDENCE_V2_ADMIN_API_KEY")
    for asset in assets:
        source = asset.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in source:
                _fail(f"frontend package contains forbidden admin secret marker: {asset}")
    return len(assets)


def _manifest_filename(value: object, *, label: str) -> str:
    filename = str(value or "")
    if not filename or Path(filename).name != filename:
        _fail(f"{label} filename is invalid")
    return filename


def _validate_ondir_bundle(executable_root: Path, bundle_name: str, executable_name: str) -> bool:
    bundle = executable_root / bundle_name
    if not bundle.is_dir():
        _fail(f"PyInstaller onedir bundle is missing: {bundle}")
    executable = bundle / executable_name
    if not executable.is_file():
        _fail(f"PyInstaller onedir executable is missing: {executable}")
    payload_files = [item for item in bundle.rglob("*") if item.is_file()]
    if len(payload_files) < 2:
        _fail(f"PyInstaller onedir payload is incomplete: {bundle}")
    runtime_payload = any(
        item.name.lower().startswith(("python", "vcruntime", "api-ms-win"))
        and item.suffix.lower() in {".dll", ".pyd", ".zip"}
        for item in payload_files
    ) or (bundle / "_internal").is_dir()
    if not runtime_payload:
        _fail(f"PyInstaller onedir runtime payload is missing: {bundle}")
    return True


def _validate_distribution_manifest(manifest_path: Path, package_root: Path) -> dict[str, object]:
    from src.runtime.manifest import EXTERNAL_MANIFEST_VERSION, sha256_file

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("distribution manifest is unreadable") from exc
    if manifest.get("manifest_version") != EXTERNAL_MANIFEST_VERSION:
        _fail("distribution manifest version is unsupported")
    installer = manifest.get("installer")
    if not isinstance(installer, dict):
        _fail("distribution manifest installer record is missing")
    installer_path = package_root / "installer" / _manifest_filename(
        installer.get("filename"), label="installer"
    )
    if not installer_path.is_file():
        _fail(f"installer artifact is missing: {installer_path}")
    if sha256_file(installer_path) != installer.get("sha256"):
        _fail("installer artifact checksum mismatch")
    return manifest


def validate_package(package_root: str | Path, *, distribution_manifest: str | Path | None = None) -> dict[str, object]:
    from src.runtime.manifest import load_manifest, validate_internal_manifest

    root = Path(package_root).expanduser().resolve(strict=False)
    resource_root = root / "resource-payload"
    manifest_path = resource_root / "package-manifest.json"
    if not resource_root.is_dir() or not manifest_path.is_file():
        _fail("resource payload or internal package manifest is missing")
    _assert_no_runtime_state(root)
    manifest = load_manifest(manifest_path)
    internal = validate_internal_manifest(manifest, resource_root, manifest_path=manifest_path)
    frontend_assets = _assert_frontend_secret_gate(resource_root)
    executable_root = root / "executables"
    if not executable_root.is_dir():
        _fail("PyInstaller executable root is missing")
    if any(item.is_file() and item.suffix.lower() == ".exe" for item in executable_root.iterdir()):
        _fail("top-level executable is not a valid onedir layout")
    executables = {
        "tw-stock-predictor.exe": _validate_ondir_bundle(
            executable_root,
            "tw-stock-predictor",
            "tw-stock-predictor.exe",
        ),
        "tw-stock-predictor-server.exe": _validate_ondir_bundle(
            executable_root,
            "tw-stock-predictor-server",
            "tw-stock-predictor-server.exe",
        ),
    }
    if not all(executables.values()):
        _fail("one or more PyInstaller executables are missing")
    external = None
    if distribution_manifest:
        external = _validate_distribution_manifest(
            Path(distribution_manifest).expanduser().resolve(strict=False),
            root,
        )
    return {
        "status": "valid",
        "resource_root": str(resource_root),
        "internal_manifest": internal,
        "frontend_assets_checked": frontend_assets,
        "executables": executables,
        "distribution_manifest": external,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root")
    parser.add_argument("--distribution-manifest", default=None)
    args = parser.parse_args()
    try:
        result = validate_package(
            args.package_root,
            distribution_manifest=args.distribution_manifest,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
