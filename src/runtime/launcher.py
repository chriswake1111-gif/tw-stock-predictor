"""Local packaged launcher/supervisor with bounded loopback startup."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from .control import LocalStopEvent, stop_event_name
from .diagnostics import DiagnosticLogger
from .instance import (
    InstanceDescriptor,
    InstanceGuard,
    InstanceOwnershipError,
    LaunchContext,
    clear_descriptor,
    read_descriptor,
    process_exists,
    validate_process_ownership,
)
from .settings import RuntimeSettings
from .startup_coordinator import StartupCoordinator
from .win32 import ProcessTreeOwner, terminate_process


PORT_ATTEMPTS = 5
READY_TIMEOUT_SECONDS = 30.0


class LaunchError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class LaunchResult:
    status: str
    origin: str | None = None
    port: int | None = None
    server_pid: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "origin": self.origin,
            "port": self.port,
            "server_pid": self.server_pid,
            "reason": self.reason,
        }


def _available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _ready_url(origin: str) -> str:
    return f"{origin}/api/ready"


def _fetch_ready(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


class Launcher:
    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        server_command: Sequence[str] | None = None,
        browser_opener: Callable[[str], Any] = webbrowser.open,
        port_picker: Callable[[str], int] = _available_port,
        ready_fetcher: Callable[[str, float], dict[str, Any] | None] = _fetch_ready,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        coordinator: StartupCoordinator | None = None,
        logger: DiagnosticLogger | None = None,
    ) -> None:
        self.settings = settings
        self.paths = settings.paths
        self.server_command = list(server_command or self._default_server_command())
        self.browser_opener = browser_opener
        self.port_picker = port_picker
        self.ready_fetcher = ready_fetcher
        self.process_factory = process_factory
        self.coordinator = coordinator or StartupCoordinator(settings, logger=logger)
        self.logger = logger
        self.guard: InstanceGuard | None = None
        self.process: subprocess.Popen | None = None
        self.context: LaunchContext | None = None
        self.stop_event: LocalStopEvent | None = None
        self.process_tree: ProcessTreeOwner | None = None

    def _default_server_command(self) -> list[str]:
        if self.settings.packaged and getattr(sys, "frozen", False):
            launcher_executable = Path(sys.executable).resolve(strict=False)
            candidates = (
                launcher_executable.parent.parent
                / "tw-stock-predictor-server"
                / "tw-stock-predictor-server.exe",
                launcher_executable.parent / "tw-stock-predictor-server.exe",
            )
            for candidate in candidates:
                if candidate.is_file():
                    return [str(candidate)]
            return [str(candidates[0])]
        return [sys.executable, "-m", "src.runtime.server"]

    def _log(self, code: str, message: str = "", **context: object) -> None:
        if self.logger:
            self.logger.emit(code, phase="launcher", message=message, **context)

    def _command(self) -> list[str]:
        command = list(self.server_command)
        if self.settings.packaged and getattr(sys, "frozen", False):
            command.extend(("--user-root", str(self.paths.user_root)))
        return command

    def _spawn(self, settings: RuntimeSettings, context: LaunchContext) -> subprocess.Popen:
        environment = os.environ.copy()
        environment.update(settings.paths.as_environment())
        environment.update({
            "TW_STOCK_PACKAGED": "true",
            "TW_STOCK_APP_VERSION": settings.app_version,
            "TW_STOCK_BUILD_SHA": settings.build_sha,
            "TW_STOCK_HOST": settings.host,
            "TW_STOCK_PORT": str(settings.port or ""),
            "RESEARCH_APPLICATION_ORIGIN": settings.application_origin or "",
            "CORS_ALLOWED_ORIGINS": settings.application_origin or "",
            "TW_STOCK_LAUNCH_ID": context.launch_id,
            "TW_STOCK_LAUNCH_NONCE": context.nonce,
            "TW_STOCK_LAUNCHER_PID": str(context.launcher_pid),
            "TW_STOCK_LAUNCH_CONTEXT_PATH": str(context.context_path),
            "TW_STOCK_STOP_EVENT_NAME": context.stop_event_name,
            "TW_STOCK_STARTUP_PREPARED": "true",
        })
        return self.process_factory(
            self._command(),
            cwd=str(settings.paths.resource_root),
            env=environment,
            close_fds=True,
        )

    def _clear_context(self) -> None:
        if self.context:
            self.context.context_path.unlink(missing_ok=True)
            self.context = None

    def _close_control(self) -> None:
        if self.stop_event:
            self.stop_event.close()
            self.stop_event = None
        if self.process_tree:
            self.process_tree.close()
            self.process_tree = None

    def _shutdown_child(self, *, timeout_seconds: float = 5.0) -> None:
        """Request graceful shutdown, then force only after the deadline."""

        process = self.process
        if process is not None and process.poll() is None:
            if self.stop_event:
                try:
                    self.stop_event.set()
                except OSError as exc:
                    self._log("stop_event_signal_failed", str(exc))
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                self._log("graceful_shutdown_timeout")
                if self.process_tree and self.process_tree.handle is not None:
                    try:
                        self.process_tree.terminate()
                    except OSError as exc:
                        self._log("job_object_termination_failed", str(exc))
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    self._log("forced_shutdown_timeout")
        self.process = None
        self._close_control()

    def start(self) -> LaunchResult:
        if not self.settings.packaged:
            raise LaunchError("launcher_requires_packaged_mode")
        self.paths.ensure_user_dirs()
        try:
            self.guard = InstanceGuard(self.paths.runtime_dir).acquire()
        except InstanceOwnershipError as exc:
            self._log("second_launch", str(exc))
            return LaunchResult("existing_instance", reason="instance_already_running")

        prepared = self.coordinator.prepare()
        if not prepared.ready:
            self._log(prepared.failure_code or "startup_not_ready")
            self.stop()
            return LaunchResult("failed", reason=prepared.failure_code or "startup_not_ready")

        for attempt in range(PORT_ATTEMPTS):
            try:
                port = self.port_picker("127.0.0.1")
                runtime_settings = self.settings.with_endpoint(port=port)
                self.context = LaunchContext.create(
                    self.paths.runtime_dir,
                    app_version=runtime_settings.app_version,
                    build_sha=runtime_settings.build_sha,
                    launcher_pid=os.getpid(),
                )
                self.stop_event = LocalStopEvent.create(self.context.stop_event_name)
                self.process_tree = ProcessTreeOwner.create()
                self.process = self._spawn(runtime_settings, self.context)
                if self.process_tree.handle is not None:
                    self.process_tree.assign(int(self.process.pid))
                deadline = time.monotonic() + READY_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        break
                    payload = self.ready_fetcher(
                        _ready_url(runtime_settings.application_origin or ""),
                        timeout=0.5,
                    )
                    if payload and payload.get("contract_version") == "tw_stock_ready_v1" and payload.get("ready"):
                        descriptor = read_descriptor(self.paths.runtime_dir / "instance.json")
                        owned, reason = validate_process_ownership(
                            descriptor,
                            expected_build_sha=runtime_settings.build_sha,
                        )
                        if not owned:
                            raise LaunchError(reason)
                        if descriptor.get("origin") != runtime_settings.application_origin:
                            raise LaunchError("descriptor_origin_mismatch")
                        if descriptor.get("launch_id") != self.context.launch_id:
                            raise LaunchError("descriptor_launch_identity_mismatch")
                        if int(descriptor.get("launcher_pid", 0)) != self.context.launcher_pid:
                            raise LaunchError("descriptor_launcher_identity_mismatch")
                        self.browser_opener(f"{runtime_settings.application_origin}/research/daily")
                        return LaunchResult(
                            "started",
                            origin=runtime_settings.application_origin,
                            port=port,
                            server_pid=int(descriptor.get("server_pid")),
                        )
                    time.sleep(0.1)
                self._log("port_unavailable", attempt=attempt + 1, port=port)
                self._shutdown_child()
                self._clear_context()
            except (LaunchError, OSError, ValueError, KeyError) as exc:
                self._log(
                    "launcher_start_failed",
                    str(exc),
                    attempt=attempt + 1,
                    error_code=getattr(exc, "code", type(exc).__name__),
                )
                self._shutdown_child()
                self._clear_context()
                if isinstance(exc, LaunchError) and exc.code not in {"port_unavailable", "server_process_missing"}:
                    break
        self.stop()
        return LaunchResult("failed", reason="port_unavailable")

    def stop(self) -> LaunchResult:
        self._shutdown_child()
        clear_descriptor(self.paths.runtime_dir / "instance.json")
        self._clear_context()
        if self.guard:
            self.guard.release()
            self.guard = None
        return LaunchResult("stopped")

    def wait_for_server(self) -> LaunchResult:
        """Keep launcher ownership alive while the server child is running."""

        if self.process is None:
            return LaunchResult("stopped", reason="server_process_missing")
        try:
            self.process.wait()
            return LaunchResult("stopped", reason="server_exited")
        finally:
            self.stop()


def _is_loopback_origin(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.path not in {"", "/"}:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if parsed.hostname is None or port is None:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return parsed.hostname.lower() == "localhost"


def stop_existing(
    settings: RuntimeSettings,
    *,
    timeout_seconds: float = 5.0,
    parent_pid_resolver: Callable[[int], int | None] | None = None,
) -> LaunchResult:
    """Stop only the validated local child described by the local descriptor."""

    descriptor_path = settings.paths.runtime_dir / "instance.json"
    if not descriptor_path.is_file():
        return LaunchResult("not_running", reason="instance_descriptor_missing")
    try:
        descriptor = read_descriptor(descriptor_path)
        server_pid = int(descriptor["server_pid"])
    except (KeyError, TypeError, ValueError, InstanceOwnershipError):
        return LaunchResult("failed", reason="descriptor_invalid")
    if not process_exists(server_pid):
        clear_descriptor(descriptor_path)
        return LaunchResult("not_running", reason="server_process_missing")
    if not _is_loopback_origin(descriptor.get("origin")):
        return LaunchResult("failed", reason="descriptor_origin_invalid")
    try:
        launch_id = str(descriptor["launch_id"])
        expected_event_name = stop_event_name(launch_id)
    except (KeyError, TypeError, ValueError):
        return LaunchResult("failed", reason="descriptor_stop_event_invalid")
    if descriptor.get("stop_event_name", expected_event_name) != expected_event_name:
        return LaunchResult("failed", reason="descriptor_stop_event_mismatch")
    owned, ownership_reason = validate_process_ownership(
        descriptor,
        expected_build_sha=settings.build_sha,
        **({"parent_pid_resolver": parent_pid_resolver} if parent_pid_resolver else {}),
    )
    if not owned:
        return LaunchResult("failed", reason=ownership_reason)
    try:
        stop_event = LocalStopEvent.open(expected_event_name)
        stop_event.set()
    except OSError:
        return LaunchResult("failed", reason="stop_event_unavailable")
    finally:
        if "stop_event" in locals():
            stop_event.close()
    deadline = time.monotonic() + timeout_seconds
    while process_exists(server_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    forced = False
    if process_exists(server_pid):
        forced = True
        try:
            if os.name == "nt":
                if not terminate_process(server_pid, exit_code=1):
                    return LaunchResult("failed", reason="stop_process_open_failed")
            else:
                os.kill(server_pid, 9)
        except OSError:
            return LaunchResult("failed", reason="stop_signal_failed")
        deadline = time.monotonic() + timeout_seconds
        while process_exists(server_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    if process_exists(server_pid):
        return LaunchResult("failed", reason="shutdown_timeout")
    clear_descriptor(descriptor_path)
    return LaunchResult("stopped", reason="local_stop_forced" if forced else "local_stop")


__all__ = ["LaunchError", "LaunchResult", "Launcher", "PORT_ATTEMPTS", "stop_existing"]
