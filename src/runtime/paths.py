"""Absolute resource and per-user runtime path resolution.

The packaged application must not depend on its current working directory.
Development callers retain the repository-root defaults used by the existing
application, while packaged callers resolve all mutable state below the
per-user application directory.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _absolute(value: str | Path, *, base: Path) -> Path:
    path = Path(value)
    # ``Path.expanduser()`` consults HOME/USERPROFILE even when callers have
    # already supplied an absolute path.  The packaged launcher intentionally
    # runs with a minimal environment, so only consult the home directory when
    # the input actually contains a user-home marker.
    if str(path).startswith("~"):
        path = path.expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


class RuntimePathError(ValueError):
    code = "runtime_path_authority_invalid"


def _paths_overlap(first: Path, second: Path) -> bool:
    first_text = os.path.normcase(str(first.resolve(strict=False)))
    second_text = os.path.normcase(str(second.resolve(strict=False)))
    try:
        common = os.path.commonpath((first_text, second_text))
    except ValueError:
        return False
    return common in {first_text, second_text}


def _path_is_within(path: Path, root: Path) -> bool:
    path_text = os.path.normcase(str(path.resolve(strict=False)))
    root_text = os.path.normcase(str(root.resolve(strict=False)))
    try:
        return os.path.commonpath((path_text, root_text)) == root_text
    except ValueError:
        return False


def _validate_packaged_isolation(paths: "RuntimePaths") -> None:
    immutable = {
        "install_root": paths.install_root,
        "resource_root": paths.resource_root,
    }
    mutable = {
        "user_root": paths.user_root,
        "data_dir": paths.data_dir,
        "logs_dir": paths.logs_dir,
        "backup_dir": paths.backup_dir,
        "config_dir": paths.config_dir,
        "runtime_dir": paths.runtime_dir,
        "database_path": paths.database_path,
        "eod_db_path": paths.eod_db_path,
        "universe_db_path": paths.universe_db_path,
        "config_path": paths.config_path,
    }
    for mutable_name, mutable_path in mutable.items():
        if not _path_is_within(mutable_path, paths.user_root):
            raise RuntimePathError(
                f"packaged mutable path {mutable_name} escapes user_root"
            )
        for immutable_name, immutable_path in immutable.items():
            if _paths_overlap(mutable_path, immutable_path):
                raise RuntimePathError(
                    f"packaged mutable path {mutable_name} overlaps {immutable_name}"
                )
    packaged_resources = {
        "model_rules_path": paths.model_rules_path,
        "frontend_dist": paths.frontend_dist,
        "migrations_dir": paths.migrations_dir,
    }
    for resource_name, resource_path in packaged_resources.items():
        if not _path_is_within(resource_path, paths.resource_root):
            raise RuntimePathError(
                f"packaged immutable path {resource_name} escapes resource_root"
            )


def _local_app_data(environ: Mapping[str, str]) -> Path:
    configured = environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (Path.home() / "AppData" / "Local").resolve(strict=False)


def _packaged_resource_default(executable_root: Path) -> Path:
    """Locate resources in both supported PyInstaller onedir layouts."""

    bundle_root = Path(getattr(sys, "_MEIPASS", executable_root)).resolve(strict=False)
    candidates = (bundle_root, bundle_root / "_internal")
    for candidate in candidates:
        if (
            (candidate / "package-manifest.json").is_file()
            and (candidate / "config").is_dir()
            and (candidate / "migrations").is_dir()
        ):
            return candidate
    return bundle_root


@dataclass(frozen=True)
class RuntimePaths:
    install_root: Path
    resource_root: Path
    user_root: Path
    data_dir: Path
    logs_dir: Path
    backup_dir: Path
    config_dir: Path
    runtime_dir: Path
    database_path: Path
    eod_db_path: Path
    universe_db_path: Path
    config_path: Path
    model_rules_path: Path
    frontend_dist: Path
    migrations_dir: Path

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
        packaged_install_root: str | Path | None = None,
        packaged_resource_root: str | Path | None = None,
        packaged_user_root: str | Path | None = None,
    ) -> "RuntimePaths":
        env = environ if environ is not None else os.environ
        default_project = Path(project_root or Path(__file__).resolve().parents[2])
        default_project = default_project.resolve(strict=False)
        packaged = _is_true(env.get("TW_STOCK_PACKAGED"))

        if packaged:
            executable_root = Path(getattr(sys, "executable", default_project)).resolve(strict=False).parent
            resource_default = _packaged_resource_default(executable_root)
            install_root = _absolute(
                packaged_install_root or executable_root,
                base=default_project,
            )
            resource_root = _absolute(
                packaged_resource_root or resource_default,
                base=install_root,
            )
            user_default = (
                Path(packaged_user_root)
                if packaged_user_root is not None
                else _local_app_data(env) / "tw-stock-predictor"
            )
            user_root = _absolute(
                user_default,
                base=default_project,
            )
        else:
            install_root = _absolute(
                env.get("TW_STOCK_INSTALL_ROOT", str(default_project)),
                base=default_project,
            )
            resource_root = _absolute(
                env.get("TW_STOCK_RESOURCE_ROOT", str(install_root)),
                base=install_root,
            )
            user_root = _absolute(
                env.get("TW_STOCK_USER_ROOT", str(install_root)),
                base=install_root,
            )

        def configured(name: str, default: Path) -> str | Path:
            return default if packaged else env.get(name, str(default))

        data_dir = _absolute(configured("TW_STOCK_DATA_ROOT", user_root / "data"), base=user_root)
        logs_dir = _absolute(configured("TW_STOCK_LOG_ROOT", user_root / "logs"), base=user_root)
        backup_dir = _absolute(configured("TW_STOCK_BACKUP_ROOT", user_root / "backup"), base=user_root)
        config_dir = _absolute(configured("TW_STOCK_CONFIG_ROOT", user_root / "config"), base=user_root)
        runtime_dir = _absolute(configured("TW_STOCK_RUNTIME_ROOT", user_root / "runtime"), base=user_root)

        def db_path(name: str, default: Path) -> Path:
            return _absolute(configured(name, default), base=user_root)

        config_default = config_dir / "config.yaml" if packaged else resource_root / "config" / "config.yaml"
        model_rules_default = resource_root / "config" / "model_rules.yaml"
        frontend_default = resource_root / "frontend" / "dist"
        migrations_default = resource_root / "migrations"

        result = cls(
            install_root=install_root,
            resource_root=resource_root,
            user_root=user_root,
            data_dir=data_dir,
            logs_dir=logs_dir,
            backup_dir=backup_dir,
            config_dir=config_dir,
            runtime_dir=runtime_dir,
            database_path=db_path("DATABASE_PATH", data_dir / "cache.db"),
            eod_db_path=db_path("EOD_DB_PATH", data_dir / "cache.db"),
            universe_db_path=db_path("UNIVERSE_DB_PATH", data_dir / "cache.db"),
            config_path=_absolute(configured("CONFIG_PATH", config_default), base=user_root),
            model_rules_path=_absolute(
                configured("MODEL_RULES_PATH", model_rules_default), base=resource_root
            ),
            frontend_dist=_absolute(
                configured("TW_STOCK_FRONTEND_DIST", frontend_default), base=resource_root
            ),
            migrations_dir=_absolute(
                configured("TW_STOCK_MIGRATIONS_ROOT", migrations_default), base=resource_root
            ),
        )
        if packaged:
            _validate_packaged_isolation(result)
        return result

    def ensure_user_dirs(self) -> None:
        """Create only mutable per-user directories, never the install root."""

        for directory in (
            self.user_root,
            self.data_dir,
            self.logs_dir,
            self.backup_dir,
            self.config_dir,
            self.runtime_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def as_environment(self) -> dict[str, str]:
        return {
            "DATABASE_PATH": str(self.database_path),
            "EOD_DB_PATH": str(self.eod_db_path),
            "UNIVERSE_DB_PATH": str(self.universe_db_path),
            "CONFIG_PATH": str(self.config_path),
            "MODEL_RULES_PATH": str(self.model_rules_path),
            "TW_STOCK_FRONTEND_DIST": str(self.frontend_dist),
            "TW_STOCK_MIGRATIONS_ROOT": str(self.migrations_dir),
            "TW_STOCK_INSTALL_ROOT": str(self.install_root),
            "TW_STOCK_RESOURCE_ROOT": str(self.resource_root),
            "TW_STOCK_USER_ROOT": str(self.user_root),
            "TW_STOCK_RUNTIME_ROOT": str(self.runtime_dir),
        }


__all__ = ["RuntimePathError", "RuntimePaths"]
