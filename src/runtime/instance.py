"""Per-user instance ownership, descriptors, and local launch binding."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .control import stop_event_name as build_stop_event_name
from .win32 import (
    ERROR_ALREADY_EXISTS,
    close_handle,
    create_mutex,
    last_error,
    parent_pid as win32_parent_pid,
    process_exists as win32_process_exists,
    release_mutex,
)


MUTEX_NAME = r"Local\TWStockPredictor.ProductV1"


class InstanceOwnershipError(RuntimeError):
    code = "instance_ownership_unavailable"


class LaunchHandshakeError(RuntimeError):
    code = "launch_handshake_mismatch"


@dataclass(frozen=True)
class LaunchContext:
    launch_id: str
    nonce: str
    launcher_pid: int
    app_version: str
    build_sha: str
    context_path: Path

    @property
    def stop_event_name(self) -> str:
        return build_stop_event_name(self.launch_id)

    @classmethod
    def create(
        cls,
        runtime_dir: str | Path,
        *,
        app_version: str,
        build_sha: str,
        launcher_pid: int | None = None,
    ) -> "LaunchContext":
        runtime = Path(runtime_dir).resolve(strict=False)
        runtime.mkdir(parents=True, exist_ok=True)
        launch_id = secrets.token_hex(16)
        context_path = runtime / f"launch-{launch_id}.json"
        context = cls(
            launch_id=launch_id,
            nonce=secrets.token_urlsafe(32),
            launcher_pid=launcher_pid if launcher_pid is not None else os.getpid(),
            app_version=app_version,
            build_sha=build_sha,
            context_path=context_path,
        )
        context.write()
        return context

    def to_dict(self, *, include_nonce: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "launch_id": self.launch_id,
            "launcher_pid": self.launcher_pid,
            "app_version": self.app_version,
            "build_sha": self.build_sha,
            "stop_event_name": self.stop_event_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if include_nonce:
            result["nonce"] = self.nonce
        return result

    def write(self) -> None:
        self.context_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.context_path.name}.", suffix=".tmp", dir=self.context_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.context_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


@dataclass(frozen=True)
class InstanceDescriptor:
    app_version: str
    build_sha: str
    launcher_pid: int
    server_pid: int
    host: str
    port: int
    origin: str
    started_at: str
    launch_id: str
    stop_event_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        event_name = self.stop_event_name or build_stop_event_name(self.launch_id)
        return {
            "app_version": self.app_version,
            "build_sha": self.build_sha,
            "launcher_pid": self.launcher_pid,
            "server_pid": self.server_pid,
            "host": self.host,
            "port": self.port,
            "origin": self.origin,
            "started_at": self.started_at,
            "launch_id": self.launch_id,
            "stop_event_name": event_name,
        }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_descriptor(path: str | Path, descriptor: InstanceDescriptor) -> Path:
    destination = Path(path).resolve(strict=False)
    _atomic_json(destination, descriptor.to_dict())
    return destination


def read_descriptor(path: str | Path) -> dict[str, Any]:
    destination = Path(path).resolve(strict=False)
    try:
        value = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstanceOwnershipError("instance descriptor is invalid") from exc
    if not isinstance(value, dict):
        raise InstanceOwnershipError("instance descriptor is invalid")
    return value


def clear_descriptor(path: str | Path) -> None:
    Path(path).resolve(strict=False).unlink(missing_ok=True)


def _windows_parent_pid(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        return win32_parent_pid(pid)
    except Exception:
        return None


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            return win32_process_exists(pid)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def validate_process_ownership(
    descriptor: dict[str, Any],
    *,
    expected_build_sha: str,
    parent_pid_resolver: Callable[[int], int | None] = _windows_parent_pid,
) -> tuple[bool, str]:
    try:
        server_pid = int(descriptor["server_pid"])
        launcher_pid = int(descriptor["launcher_pid"])
        build_sha = str(descriptor["build_sha"])
    except (KeyError, TypeError, ValueError):
        return False, "descriptor_identity_invalid"
    if build_sha != expected_build_sha:
        return False, "descriptor_build_identity_mismatch"
    if not process_exists(server_pid):
        return False, "server_process_missing"
    if launcher_pid <= 0:
        return False, "descriptor_launcher_identity_invalid"
    parent_pid = parent_pid_resolver(server_pid)
    if parent_pid is None:
        return False, "server_parent_identity_unavailable"
    if parent_pid != launcher_pid:
        return False, "server_parent_identity_mismatch"
    return True, "ok"


def validate_launch_context(
    context_path: str | Path,
    *,
    launch_id: str | None,
    nonce: str | None,
    launcher_pid: int | None,
    expected_build_sha: str,
    current_pid: int | None = None,
    parent_pid_resolver: Callable[[int], int | None] = _windows_parent_pid,
) -> dict[str, Any]:
    path = Path(context_path).resolve(strict=False)
    try:
        context = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchHandshakeError("launch_context_unreadable") from exc
    if not isinstance(context, dict):
        raise LaunchHandshakeError("launch_context_invalid")
    if not launch_id or context.get("launch_id") != launch_id:
        raise LaunchHandshakeError("launch_id_mismatch")
    if not nonce or context.get("nonce") != nonce:
        raise LaunchHandshakeError("launch_nonce_mismatch")
    try:
        expected_event_name = build_stop_event_name(str(context["launch_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise LaunchHandshakeError("launch_stop_event_identity_invalid") from exc
    if context.get("stop_event_name") != expected_event_name:
        raise LaunchHandshakeError("launch_stop_event_mismatch")
    if context.get("build_sha") != expected_build_sha:
        raise LaunchHandshakeError("launch_build_identity_mismatch")
    try:
        context_pid = int(context.get("launcher_pid", 0))
        expected_pid = int(launcher_pid) if launcher_pid is not None else context_pid
    except (TypeError, ValueError) as exc:
        raise LaunchHandshakeError("launch_launcher_identity_invalid") from exc
    if expected_pid <= 0 or context_pid != expected_pid:
        raise LaunchHandshakeError("launch_launcher_identity_mismatch")
    current = current_pid or os.getpid()
    parent_pid = parent_pid_resolver(current)
    if parent_pid is None:
        raise LaunchHandshakeError("launch_parent_identity_unavailable")
    if parent_pid != expected_pid:
        raise LaunchHandshakeError("launch_parent_identity_mismatch")
    return {
        "launch_id": str(context["launch_id"]),
        "launcher_pid": expected_pid,
        "app_version": str(context.get("app_version", "unknown")),
        "build_sha": str(context["build_sha"]),
        "stop_event_name": expected_event_name,
    }


class InstanceGuard:
    """Hold the per-user named mutex for the entire launcher lifetime."""

    def __init__(self, runtime_dir: str | Path, *, name: str = MUTEX_NAME) -> None:
        self.runtime_dir = Path(runtime_dir).resolve(strict=False)
        self.name = name
        self._handle: int | None = None
        self._lock_path: Path | None = None

    def acquire(self) -> "InstanceGuard":
        if self._handle is not None or self._lock_path is not None:
            return self
        if os.name == "nt":
            handle = create_mutex(self.name)
            if not handle:
                raise InstanceOwnershipError("named mutex could not be created")
            if last_error() == ERROR_ALREADY_EXISTS:
                close_handle(handle)
                raise InstanceOwnershipError("another instance owns the named mutex")
            self._handle = handle
            return self
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.runtime_dir / "instance.lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            raise InstanceOwnershipError("another instance owns the lock") from exc
        self._lock_path = lock_path
        self._handle = fd
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        if os.name == "nt":
            try:
                release_mutex(self._handle)
            finally:
                close_handle(self._handle)
        else:
            os.close(self._handle)
            if self._lock_path:
                self._lock_path.unlink(missing_ok=True)
        self._handle = None
        self._lock_path = None

    def __enter__(self) -> "InstanceGuard":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


__all__ = [
    "ERROR_ALREADY_EXISTS",
    "InstanceDescriptor",
    "InstanceGuard",
    "InstanceOwnershipError",
    "LaunchContext",
    "LaunchHandshakeError",
    "MUTEX_NAME",
    "clear_descriptor",
    "process_exists",
    "read_descriptor",
    "validate_launch_context",
    "validate_process_ownership",
    "write_descriptor",
]
