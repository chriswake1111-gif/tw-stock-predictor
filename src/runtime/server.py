"""Packaged server entrypoint and server-local readiness preparation."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.api.main import create_app

from .database_state import DatabaseState, classify_database
from .instance import (
    InstanceDescriptor,
    LaunchHandshakeError,
    clear_descriptor,
    validate_launch_context,
    write_descriptor,
)
from .startup_coordinator import StartupCoordinator, StartupFailure, StartupResult
from .settings import RuntimeSettings


class ServerStartupError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _launch_context_path(settings: RuntimeSettings) -> Path:
    value = os.getenv("TW_STOCK_LAUNCH_CONTEXT_PATH")
    if not value:
        raise ServerStartupError("launch_context_path_missing")
    return Path(value).resolve(strict=False)


def prepare_server_app(settings: RuntimeSettings):
    if settings.packaged:
        try:
            StartupCoordinator(settings).preflight()
        except StartupFailure as exc:
            raise ServerStartupError(exc.code, str(exc)) from exc
    handshake: dict[str, Any] | None = None
    if settings.packaged:
        try:
            handshake = validate_launch_context(
                _launch_context_path(settings),
                launch_id=settings.expected_launch_id,
                nonce=settings.expected_launch_nonce,
                launcher_pid=settings.expected_launcher_pid,
                expected_build_sha=settings.build_sha,
            )
        except LaunchHandshakeError as exc:
            raise ServerStartupError(exc.code, str(exc)) from exc

    classification = classify_database(
        settings.paths.database_path,
        resource_root=str(settings.paths.resource_root),
    )
    if classification.state is not DatabaseState.KNOWN_V2_CURRENT:
        result = StartupResult(
            "failed",
            classification,
            failure_code="server_database_not_ready",
        )
    else:
        result = StartupResult("ready", classification)
    if not result.ready:
        raise ServerStartupError(result.failure_code or "server_not_ready")

    app = create_app(settings, startup_result=result)
    app.state.launch_handshake = handshake
    descriptor_path = settings.paths.runtime_dir / "instance.json"
    if settings.packaged:
        if settings.port is None or not settings.application_origin:
            raise ServerStartupError("server_endpoint_missing")
        descriptor = InstanceDescriptor(
            app_version=settings.app_version,
            build_sha=settings.build_sha,
            launcher_pid=settings.expected_launcher_pid or 0,
            server_pid=os.getpid(),
            host=settings.host,
            port=settings.port,
            origin=settings.application_origin,
            started_at=datetime.now(timezone.utc).isoformat(),
            launch_id=settings.expected_launch_id or "",
        )
        write_descriptor(descriptor_path, descriptor)
        app.state.runtime_descriptor_path = descriptor_path
    return app


def run_server(settings: RuntimeSettings | None = None) -> int:
    runtime_settings = settings or RuntimeSettings.from_environment()
    app = prepare_server_app(runtime_settings)
    descriptor_path = getattr(app.state, "runtime_descriptor_path", None)
    try:
        import uvicorn

        if runtime_settings.port is None:
            raise ServerStartupError("server_port_missing")
        uvicorn.run(
            app,
            host=runtime_settings.host,
            port=runtime_settings.port,
            reload=False,
            workers=1,
            log_config=None,
        )
        return 0
    except ServerStartupError:
        raise
    finally:
        if descriptor_path:
            clear_descriptor(descriptor_path)


__all__ = ["ServerStartupError", "prepare_server_app", "run_server"]
