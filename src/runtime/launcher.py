"""Local packaged launcher/supervisor with bounded loopback startup."""

from __future__ import annotations

import json
import ctypes
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

from .database_state import DatabaseState
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

    def _default_server_command(self) -> list[str]:
        if self.settings.packaged and getattr(sys, "frozen", False):
            server_path = Path(sys.executable).resolve(strict=False).with_name(
                "tw-stock-predictor-server.exe"
            )
            return [str(server_path)]
        return [sys.executable, "-m", "src.runtime.server"]

    def _log(self, code: str, message: str = "", **context: object) -> None:
        if self.logger:
            self.logger.emit(code, phase="launcher", message=message, **context)

    def _command(self) -> list[str]:
        return list(self.server_command)

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
            "TW_STOCK_STARTUP_PREPARED": "true",
        })
        return self.process_factory(
            self._command(),
            cwd=str(settings.paths.resource_root),
            env=environment,
            close_fds=True,
        )

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
                self.process = self._spawn(runtime_settings, self.context)
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
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                self.process = None
                if self.context:
                    self.context.context_path.unlink(missing_ok=True)
                    self.context = None
            except (LaunchError, OSError, ValueError, KeyError) as exc:
                self._log("launcher_start_failed", str(exc), attempt=attempt + 1)
                if self.process is not None and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                self.process = None
                if self.context:
                    self.context.context_path.unlink(missing_ok=True)
                    self.context = None
                if isinstance(exc, LaunchError) and exc.code not in {"port_unavailable", "server_process_missing"}:
                    break
        self.stop()
        return LaunchResult("failed", reason="port_unavailable")

    def stop(self) -> LaunchResult:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.process = None
        clear_descriptor(self.paths.runtime_dir / "instance.json")
        if self.context:
            self.context.context_path.unlink(missing_ok=True)
            self.context = None
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
    owned, ownership_reason = validate_process_ownership(
        descriptor,
        expected_build_sha=settings.build_sha,
        **({"parent_pid_resolver": parent_pid_resolver} if parent_pid_resolver else {}),
    )
    if not owned:
        return LaunchResult("failed", reason=ownership_reason)
    try:
        if os.name == "nt":
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, server_pid)
            if not handle:
                return LaunchResult("failed", reason="stop_process_open_failed")
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(server_pid, 15)
    except OSError:
        return LaunchResult("failed", reason="stop_signal_failed")
    deadline = time.monotonic() + timeout_seconds
    while process_exists(server_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if process_exists(server_pid):
        return LaunchResult("failed", reason="shutdown_timeout")
    clear_descriptor(descriptor_path)
    return LaunchResult("stopped", reason="local_stop")


__all__ = ["LaunchError", "LaunchResult", "Launcher", "PORT_ATTEMPTS", "stop_existing"]
