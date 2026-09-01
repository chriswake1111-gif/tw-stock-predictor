"""Runtime settings with explicit packaged/development boundaries."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, replace
from typing import Mapping

from src.api.workflow_security import parse_research_origin

from .paths import RuntimePaths


class RuntimeConfigurationError(ValueError):
    code = "runtime_configuration_invalid"


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def packaged_auto_migrate(environ: Mapping[str, str] | None = None) -> bool:
    """Return the request-side migration policy without touching the DB."""

    env = environ if environ is not None else os.environ
    return not _is_true(env.get("TW_STOCK_PACKAGED"))


def _packaged_identity(paths: RuntimePaths) -> tuple[str | None, str | None]:
    manifest = paths.resource_root / "package-manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    app_version = payload.get("app_version")
    build_sha = payload.get("build_sha")
    return (
        str(app_version) if app_version else None,
        str(build_sha) if build_sha else None,
    )


@dataclass(frozen=True)
class RuntimeSettings:
    paths: RuntimePaths
    packaged: bool
    app_version: str
    build_sha: str
    scheduler_enabled: bool
    host: str
    port: int | None
    application_origin: str | None
    cors_allowed_origins: tuple[str, ...]
    expected_launch_id: str | None = None
    expected_launch_nonce: str | None = None
    expected_launcher_pid: int | None = None

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        paths: RuntimePaths | None = None,
    ) -> "RuntimeSettings":
        env = environ if environ is not None else os.environ
        resolved_paths = paths or RuntimePaths.from_environment(env)
        packaged = not packaged_auto_migrate(env)
        raw_port = env.get("TW_STOCK_PORT")
        try:
            port = int(raw_port) if raw_port not in {None, ""} else None
        except ValueError as exc:
            raise RuntimeConfigurationError("port must be an integer") from exc
        if port is not None and not 1 <= port <= 65535:
            raise RuntimeConfigurationError("port must be between 1 and 65535")

        origin = env.get("RESEARCH_APPLICATION_ORIGIN", "").strip() or None
        if origin:
            try:
                origin = parse_research_origin(origin).origin
            except ValueError as exc:
                raise RuntimeConfigurationError(str(exc)) from exc

        if packaged:
            scheduler_default = False
            host_default = "127.0.0.1"
            cors_default = (origin,) if origin else ()
        else:
            scheduler_default = True
            host_default = "0.0.0.0"
            cors_default = tuple(
                item.strip()
                for item in env.get("CORS_ALLOWED_ORIGINS", "*").split(",")
                if item.strip()
            ) or ("*",)

        scheduler_value = env.get("TW_STOCK_SCHEDULER_ENABLED")
        scheduler_enabled = (
            _is_true(scheduler_value) if scheduler_value is not None else scheduler_default
        )
        cors = tuple(
            item.strip()
            for item in env.get("CORS_ALLOWED_ORIGINS", ",".join(cors_default)).split(",")
            if item.strip()
        )
        if packaged and origin and cors != (origin,):
            raise RuntimeConfigurationError(
                "packaged mode requires one exact loopback application origin"
            )
        if packaged and port is not None and not origin:
            raise RuntimeConfigurationError(
                "packaged mode requires an application origin after port selection"
            )

        manifest_version, manifest_build_sha = _packaged_identity(resolved_paths) if packaged else (None, None)

        def pid(value: str | None) -> int | None:
            if value in {None, ""}:
                return None
            try:
                return int(value)
            except ValueError as exc:
                raise RuntimeConfigurationError("launcher PID must be an integer") from exc

        return cls(
            paths=resolved_paths,
            packaged=packaged,
            app_version=env.get("TW_STOCK_APP_VERSION") or manifest_version or "1.0.0",
            build_sha=env.get("TW_STOCK_BUILD_SHA") or manifest_build_sha or "unknown",
            scheduler_enabled=scheduler_enabled,
            host=env.get("TW_STOCK_HOST", host_default),
            port=port,
            application_origin=origin,
            cors_allowed_origins=cors,
            expected_launch_id=env.get("TW_STOCK_LAUNCH_ID") or None,
            expected_launch_nonce=env.get("TW_STOCK_LAUNCH_NONCE") or None,
            expected_launcher_pid=pid(env.get("TW_STOCK_LAUNCHER_PID")),
        )

    @property
    def auto_migrate(self) -> bool:
        return not self.packaged

    def with_endpoint(self, *, port: int, host: str = "127.0.0.1") -> "RuntimeSettings":
        if not 1 <= port <= 65535:
            raise RuntimeConfigurationError("port must be between 1 and 65535")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeConfigurationError("packaged server host must be loopback")
        display_host = "[::1]" if host == "::1" else host
        origin = f"http://{display_host}:{port}"
        return replace(
            self,
            host=host,
            port=port,
            application_origin=origin,
            cors_allowed_origins=(origin,),
        )


__all__ = ["RuntimeConfigurationError", "RuntimeSettings", "packaged_auto_migrate"]
