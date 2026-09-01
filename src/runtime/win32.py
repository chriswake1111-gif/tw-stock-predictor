"""Narrow, pointer-safe Win32 interop used by the local runtime boundary."""

from __future__ import annotations

import ctypes
import os
from typing import Any


ERROR_ALREADY_EXISTS = 183
ERROR_FILE_NOT_FOUND = 2
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
TH32CS_SNAPPROCESS = 0x00000002
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL
    _kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.OpenEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
else:
    _kernel32 = None


def _require_windows() -> Any:
    if _kernel32 is None:
        raise OSError("Win32 runtime is available only on Windows")
    return _kernel32


def _handle_value(handle: Any) -> int | None:
    return int(handle) if handle else None


def last_error() -> int:
    return ctypes.get_last_error()


def close_handle(handle: int) -> None:
    if handle:
        _require_windows().CloseHandle(handle)


def create_mutex(name: str) -> int | None:
    return _handle_value(_require_windows().CreateMutexW(None, True, name))


def release_mutex(handle: int) -> None:
    if not _require_windows().ReleaseMutex(handle):
        raise ctypes.WinError()


def create_named_event(name: str) -> int | None:
    return _handle_value(_require_windows().CreateEventW(None, True, False, name))


def open_named_event(name: str) -> int | None:
    access = EVENT_MODIFY_STATE | SYNCHRONIZE
    return _handle_value(_require_windows().OpenEventW(access, False, name))


def set_named_event(handle: int) -> None:
    if not _require_windows().SetEvent(handle):
        raise ctypes.WinError()


def wait_for_handle(handle: int, timeout_seconds: float | None = None) -> int:
    if timeout_seconds is None:
        timeout_ms = INFINITE
    else:
        timeout_ms = max(0, min(INFINITE - 1, int(timeout_seconds * 1000)))
    result = int(_require_windows().WaitForSingleObject(handle, timeout_ms))
    if result == WAIT_FAILED:
        raise ctypes.WinError()
    return result


def process_exists(pid: int) -> bool:
    handle = _handle_value(
        _require_windows().OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    )
    if not handle:
        return False
    close_handle(handle)
    return True


def open_process(pid: int, access: int) -> int | None:
    return _handle_value(_require_windows().OpenProcess(access, False, pid))


def terminate_process(pid: int, exit_code: int = 1) -> bool:
    handle = open_process(pid, PROCESS_TERMINATE)
    if not handle:
        return False
    try:
        return bool(_require_windows().TerminateProcess(handle, exit_code))
    finally:
        close_handle(handle)


def parent_pid(pid: int) -> int | None:
    kernel32 = _require_windows()
    snapshot = _handle_value(kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0))
    if snapshot in {None, -1}:
        return None
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if int(entry.th32ProcessID) == pid:
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        close_handle(snapshot)


def create_kill_on_close_job() -> int | None:
    kernel32 = _require_windows()
    handle = _handle_value(kernel32.CreateJobObjectW(None, None))
    if not handle:
        return None
    limits = _JobObjectExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        close_handle(handle)
        raise ctypes.WinError()
    return handle


def assign_process_to_job(job_handle: int, pid: int) -> None:
    process_handle = open_process(pid, PROCESS_SET_QUOTA | PROCESS_TERMINATE)
    if not process_handle:
        raise ctypes.WinError()
    try:
        if not _require_windows().AssignProcessToJobObject(job_handle, process_handle):
            raise ctypes.WinError()
    finally:
        close_handle(process_handle)


def terminate_job(job_handle: int, exit_code: int = 1) -> None:
    if not _require_windows().TerminateJobObject(job_handle, exit_code):
        raise ctypes.WinError()


class ProcessTreeOwner:
    """Own a child process tree; Windows Job Object closes kill-on-owner-exit."""

    def __init__(self, handle: int | None) -> None:
        self.handle = handle

    @classmethod
    def create(cls) -> "ProcessTreeOwner":
        return cls(create_kill_on_close_job() if os.name == "nt" else None)

    def assign(self, pid: int) -> None:
        if self.handle is not None:
            assign_process_to_job(self.handle, pid)

    def terminate(self) -> None:
        if self.handle is not None:
            terminate_job(self.handle)

    def close(self) -> None:
        if self.handle is not None:
            close_handle(self.handle)
            self.handle = None


__all__ = [
    "ERROR_ALREADY_EXISTS",
    "ERROR_FILE_NOT_FOUND",
    "EVENT_MODIFY_STATE",
    "INFINITE",
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "ProcessTreeOwner",
    "SYNCHRONIZE",
    "TH32CS_SNAPPROCESS",
    "WAIT_FAILED",
    "WAIT_OBJECT_0",
    "WAIT_TIMEOUT",
    "assign_process_to_job",
    "close_handle",
    "create_kill_on_close_job",
    "create_mutex",
    "create_named_event",
    "last_error",
    "open_named_event",
    "open_process",
    "parent_pid",
    "process_exists",
    "release_mutex",
    "set_named_event",
    "terminate_job",
    "terminate_process",
    "wait_for_handle",
]
