# PyInstaller onedir launcher for the local-only Windows product.

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(os.environ.get("TW_STOCK_SOURCE_ROOT", str(Path.cwd()))).resolve()
RESOURCE_ROOT = Path(
    os.environ.get("TW_STOCK_PACKAGE_RESOURCE_ROOT", str(ROOT))
).resolve()


def data_files(source: Path, destination: str) -> list[tuple[str, str]]:
    if not source.is_dir():
        return []
    result: list[tuple[str, str]] = []
    for item in sorted(source.rglob("*")):
        if item.is_file():
            relative_parent = item.relative_to(source).parent.as_posix()
            target = destination if relative_parent == "." else f"{destination}/{relative_parent}"
            result.append((str(item), target))
    return result


def root_files(source: Path) -> list[tuple[str, str]]:
    return [
        (str(item), ".")
        for item in sorted(source.iterdir())
        if item.is_file()
    ] if source.is_dir() else []


DATAS = (
    data_files(RESOURCE_ROOT / "config", "config")
    + data_files(RESOURCE_ROOT / "migrations", "migrations")
    + data_files(RESOURCE_ROOT / "frontend" / "dist", "frontend/dist")
    + root_files(RESOURCE_ROOT)
)
HIDDEN_IMPORTS = collect_submodules("src.runtime")


a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launcher_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tw-stock-predictor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tw-stock-predictor",
)
