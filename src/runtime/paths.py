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
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def _local_app_data(environ: Mapping[str, str]) -> Path:
    configured = environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (Path.home() / "AppData" / "Local").resolve(strict=False)


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
    ) -> "RuntimePaths":
        env = environ if environ is not None else os.environ
        default_project = Path(project_root or Path(__file__).resolve().parents[2])
        default_project = default_project.resolve(strict=False)
        packaged = _is_true(env.get("TW_STOCK_PACKAGED"))

        if packaged:
            executable_root = Path(getattr(sys, "executable", default_project)).resolve(strict=False).parent
            resource_default = Path(getattr(sys, "_MEIPASS", executable_root))
            install_root = _absolute(
                env.get("TW_STOCK_INSTALL_ROOT", str(executable_root)),
                base=default_project,
            )
            resource_root = _absolute(
                env.get("TW_STOCK_RESOURCE_ROOT", str(resource_default)),
                base=install_root,
            )
            user_default = _local_app_data(env) / "tw-stock-predictor"
            user_root = _absolute(
                env.get("TW_STOCK_USER_ROOT", str(user_default)),
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

        data_dir = _absolute(env.get("TW_STOCK_DATA_ROOT", str(user_root / "data")), base=user_root)
        logs_dir = _absolute(env.get("TW_STOCK_LOG_ROOT", str(user_root / "logs")), base=user_root)
        backup_dir = _absolute(env.get("TW_STOCK_BACKUP_ROOT", str(user_root / "backup")), base=user_root)
        config_dir = _absolute(env.get("TW_STOCK_CONFIG_ROOT", str(user_root / "config")), base=user_root)
        runtime_dir = _absolute(env.get("TW_STOCK_RUNTIME_ROOT", str(user_root / "runtime")), base=user_root)

        def db_path(name: str, default: Path) -> Path:
            return _absolute(env.get(name, str(default)), base=user_root)

        config_default = config_dir / "config.yaml" if packaged else resource_root / "config" / "config.yaml"
        model_rules_default = resource_root / "config" / "model_rules.yaml"
        frontend_default = resource_root / "frontend" / "dist"
        migrations_default = resource_root / "migrations"

        return cls(
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
            config_path=_absolute(env.get("CONFIG_PATH", str(config_default)), base=user_root),
            model_rules_path=_absolute(
                env.get("MODEL_RULES_PATH", str(model_rules_default)), base=resource_root
            ),
            frontend_dist=_absolute(
                env.get("TW_STOCK_FRONTEND_DIST", str(frontend_default)), base=resource_root
            ),
            migrations_dir=_absolute(
                env.get("TW_STOCK_MIGRATIONS_ROOT", str(migrations_default)), base=resource_root
            ),
        )

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


__all__ = ["RuntimePaths"]
