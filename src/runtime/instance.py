"""Per-user instance ownership, descriptors, and local launch binding."""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


MUTEX_NAME = r"Local\TWStockPredictor.ProductV1"
ERROR_ALREADY_EXISTS = 183


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

    def to_dict(self) -> dict[str, Any]:
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
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot in {0, -1}:
            return None

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        first = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        if not first:
            kernel32.CloseHandle(snapshot)
            return None
        while True:
            if int(entry.th32ProcessID) == pid:
                parent = int(entry.th32ParentProcessID)
                kernel32.CloseHandle(snapshot)
                return parent
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        kernel32.CloseHandle(snapshot)
    except Exception:
        return None
    return None


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
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
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, True, self.name)
            if not handle:
                raise InstanceOwnershipError("named mutex could not be created")
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                raise InstanceOwnershipError("another instance owns the named mutex")
            self._handle = int(handle)
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
            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle(self._handle)
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
