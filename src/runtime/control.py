"""Local-only graceful shutdown event abstraction."""

from __future__ import annotations

import os
import re
import threading
from typing import ClassVar

from .win32 import (
    WAIT_OBJECT_0,
    close_handle,
    create_named_event,
    open_named_event,
    set_named_event,
    wait_for_handle,
)


STOP_EVENT_PREFIX = r"Local\TWStockPredictor.Stop."
_SAFE_EVENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def stop_event_name(launch_id: str) -> str:
    if not _SAFE_EVENT_ID.fullmatch(str(launch_id)):
        raise ValueError("launch_id cannot be used as a local event identity")
    return STOP_EVENT_PREFIX + str(launch_id)


class LocalStopEvent:
    """Named Win32 event in production; process-local event for non-Windows tests."""

    _fallback_events: ClassVar[dict[str, threading.Event]] = {}
    _fallback_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, name: str, *, handle: int | None = None, event: threading.Event | None = None) -> None:
        self.name = name
        self._handle = handle
        self._event = event

    @classmethod
    def create(cls, name: str) -> "LocalStopEvent":
        if os.name == "nt":
            handle = create_named_event(name)
            if not handle:
                raise OSError(f"unable to create local stop event: {name}")
            return cls(name, handle=handle)
        with cls._fallback_lock:
            event = cls._fallback_events.setdefault(name, threading.Event())
        return cls(name, event=event)

    @classmethod
    def open(cls, name: str) -> "LocalStopEvent":
        if os.name == "nt":
            handle = open_named_event(name)
            if not handle:
                raise OSError(f"local stop event is unavailable: {name}")
            return cls(name, handle=handle)
        with cls._fallback_lock:
            event = cls._fallback_events.get(name)
        if event is None:
            raise OSError(f"local stop event is unavailable: {name}")
        return cls(name, event=event)

    def set(self) -> None:
        if self._handle is not None:
            set_named_event(self._handle)
        elif self._event is not None:
            self._event.set()
        else:
            raise OSError("local stop event is closed")

    def wait(self, timeout_seconds: float | None = None) -> bool:
        if self._handle is not None:
            return wait_for_handle(self._handle, timeout_seconds) == WAIT_OBJECT_0
        if self._event is None:
            raise OSError("local stop event is closed")
        return self._event.wait(timeout_seconds)

    def close(self) -> None:
        if self._handle is not None:
            close_handle(self._handle)
            self._handle = None
        self._event = None

    def __enter__(self) -> "LocalStopEvent":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = ["LocalStopEvent", "STOP_EVENT_PREFIX", "stop_event_name"]
