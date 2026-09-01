"""Build the internal Windows onedir package and optional Inno Setup installer.

The script builds from a copied, immutable resource payload.  It never copies
the repository database, logs, backups, environment files, or research output
into the product.  Mutable runtime state is created by the application under
the user's local application directory on first launch.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "packaging" / "windows"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=str(cwd), env=env, check=True)


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _copy_required_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"required package resource is missing: {source}")
    shutil.copytree(source, destination)


def _iscc_path(configured: str | None) -> str | None:
    if configured:
        return configured
    return shutil.which("ISCC.exe") or shutil.which("iscc")


def _build_payload(output_root: Path, *, app_version: str, build_sha: str) -> Path:
    from src.repositories.migration_runner import migration_manifest
    from src.runtime.manifest import (
        build_internal_manifest,
        validate_internal_manifest,
        write_manifest,
    )

    resource_root = output_root / "resource-payload"
    resource_root.mkdir(parents=True, exist_ok=False)
    _copy_required_tree(ROOT / "config", resource_root / "config")
    _copy_required_tree(ROOT / "migrations", resource_root / "migrations")
    _copy_required_tree(ROOT / "frontend" / "dist", resource_root / "frontend" / "dist")
    records = migration_manifest(resource_root)
    manifest = build_internal_manifest(
        resource_root,
        app_version=app_version,
        build_sha=build_sha,
        migration_records=records,
        frontend_dist=resource_root / "frontend" / "dist",
        model_rules_path=resource_root / "config" / "model_rules.yaml",
    )
    manifest_path = write_manifest(resource_root / "package-manifest.json", manifest)
    validate_internal_manifest(manifest, resource_root, manifest_path=manifest_path)
    shutil.copy2(manifest_path, output_root / "internal-package-manifest.json")
    return resource_root


def _pyinstaller(output_root: Path, resource_root: Path) -> Path:
    executable_root = output_root / "executables"
    executable_root.mkdir(parents=True, exist_ok=True)
    base_env = os.environ.copy()
    base_env["TW_STOCK_SOURCE_ROOT"] = str(ROOT)
    base_env["TW_STOCK_PACKAGE_RESOURCE_ROOT"] = str(resource_root)
    for name in ("server", "launcher"):
        spec = WINDOWS_DIR / f"tw_stock_predictor_{name}.spec"
        work = output_root / "pyinstaller-build" / name
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(executable_root),
            "--workpath",
            str(work),
            str(spec),
        ]
        _run(command, cwd=ROOT, env=base_env)
    expected = (
        executable_root / "tw-stock-predictor" / "tw-stock-predictor.exe",
        executable_root / "tw-stock-predictor-server" / "tw-stock-predictor-server.exe",
    )
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise RuntimeError("PyInstaller output is incomplete: " + ", ".join(missing))
    return executable_root


def _installer(
    output_root: Path,
    executable_root: Path,
    *,
    iscc: str,
    app_version: str,
) -> Path:
    installer_root = output_root / "installer"
    installer_root.mkdir(parents=True, exist_ok=True)
    command = [
        iscc,
        f"/DTW_STOCK_BUILD_ROOT={executable_root}",
        f"/DTW_STOCK_OUTPUT_DIR={installer_root}",
        f"/DTW_STOCK_APP_VERSION={app_version}",
        str(WINDOWS_DIR / "tw-stock-predictor.iss"),
    ]
    _run(command, cwd=ROOT)
    installer = installer_root / "tw-stock-predictor-setup.exe"
    if not installer.is_file():
        raise RuntimeError(f"Inno Setup output is missing: {installer}")
    return installer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "dist" / "windows-productization"),
        help="rebuildable output directory (must be absent unless --clean is supplied)",
    )
    parser.add_argument("--app-version", default="1.0.0")
    parser.add_argument("--build-sha", default=None)
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--iscc", default=None, help="path to Inno Setup Compiler")
    parser.add_argument("--clean", action="store_true", help="remove only the selected output directory first")
    args = parser.parse_args()

    output_root = Path(args.output_dir).expanduser().resolve(strict=False)
    if output_root == ROOT or output_root in {ROOT.parent, Path(output_root.anchor)}:
        raise RuntimeError("refusing to use a broad repository or drive path as package output")
    if output_root.exists():
        if not args.clean:
            raise RuntimeError(f"output directory already exists; use --clean explicitly: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=False)

    if not args.skip_frontend:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        _run([npm, "ci"], cwd=ROOT / "frontend")
        _run([npm, "run", "build"], cwd=ROOT / "frontend")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("frontend build output is missing; run without --skip-frontend")

    build_sha = args.build_sha or _git_sha()
    resource_root = _build_payload(output_root, app_version=args.app_version, build_sha=build_sha)
    executable_root = _pyinstaller(output_root, resource_root)

    from src.runtime.manifest import build_external_distribution_manifest

    installer: Path | None = None
    if not args.skip_installer:
        compiler = _iscc_path(args.iscc)
        if not compiler:
            raise RuntimeError("Inno Setup Compiler was not found; use --skip-installer or --iscc")
        installer = _installer(
            output_root,
            executable_root,
            iscc=compiler,
            app_version=args.app_version,
        )
        distribution = build_external_distribution_manifest(
            installer,
            app_version=args.app_version,
            build_sha=build_sha,
            output_path=output_root / "distribution-manifest.json",
            internal_manifest_path=output_root / "internal-package-manifest.json",
            artifact_paths=(
                executable_root / "tw-stock-predictor" / "tw-stock-predictor.exe",
                executable_root / "tw-stock-predictor-server" / "tw-stock-predictor-server.exe",
            ),
        )
    else:
        distribution = None

    summary = {
        "status": "built",
        "app_version": args.app_version,
        "build_sha": build_sha,
        "output_root": str(output_root),
        "resource_root": str(resource_root),
        "executable_root": str(executable_root),
        "installer": str(installer) if installer else None,
        "distribution_manifest": distribution,
    }
    (output_root / "build-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
