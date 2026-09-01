"""Bounded, redacted local diagnostic logging."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_SENSITIVE_KEY = re.compile(
    r"(?:api.?key|token|secret|password|passwd|credential|cookie|authorization|private.?key|nonce)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:Bearer\s+|sk-[A-Za-z0-9]|gh[pousr]_[A-Za-z0-9]|xox[baprs]-[A-Za-z0-9])",
    re.IGNORECASE,
)


def _redact(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, str):
        if _SENSITIVE_VALUE.search(value):
            return "[REDACTED]"
        return value[:2000]
    return value


class DiagnosticLogger:
    """A single logical log with bounded rotation and no broad cleanup."""

    def __init__(
        self,
        log_dir: str | Path,
        logical_name: str,
        *,
        app_version: str = "unknown",
        build_sha: str = "unknown",
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        max_logical_bytes: int = 60 * 1024 * 1024,
    ) -> None:
        if not logical_name or Path(logical_name).name != logical_name or Path(logical_name).suffix != ".log":
            raise ValueError("logical_name must be a simple .log filename")
        if max_bytes <= 0 or backup_count < 0 or max_logical_bytes < max_bytes:
            raise ValueError("invalid log bounds")
        self.log_dir = Path(log_dir).resolve(strict=False)
        self.logical_name = logical_name
        self.path = self.log_dir / logical_name
        self.app_version = app_version
        self.build_sha = build_sha
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.max_logical_bytes = max_logical_bytes
        self._lock = threading.Lock()

    def _rotated(self, index: int) -> Path:
        return self.log_dir / f"{self.logical_name}.{index}"

    def _logical_size(self) -> int:
        paths = [self.path, *(self._rotated(i) for i in range(1, self.backup_count + 1))]
        return sum(path.stat().st_size for path in paths if path.is_file())

    def _rotate(self) -> None:
        if not self.path.exists():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.backup_count == 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self._rotated(self.backup_count)
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self._rotated(index)
            if source.exists():
                os.replace(source, self._rotated(index + 1))
        os.replace(self.path, self._rotated(1))

    def _remove_oldest_rotated(self) -> bool:
        for index in range(self.backup_count, 0, -1):
            candidate = self._rotated(index)
            if candidate.is_file():
                candidate.unlink()
                return True
        return False

    def _make_room(self, record_bytes: int) -> None:
        """Bound the complete logical log before appending one record."""

        while self._logical_size() + record_bytes > self.max_logical_bytes:
            if self._remove_oldest_rotated():
                continue
            if self.path.is_file():
                self._rotate()
                if self._logical_size() + record_bytes <= self.max_logical_bytes:
                    break
            # The current file is itself the only remaining segment.  It is
            # always <= max_bytes, and max_logical_bytes is >= max_bytes, so
            # an oversized record is handled by the truncation branch above.
            break

    def emit(self, code: str, *, phase: str, message: str = "", **context: Any) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app_version": self.app_version,
            "build_sha": self.build_sha,
            "pid": os.getpid(),
            "code": code,
            "phase": phase,
            "message": message[:1000],
            "context": _redact(context),
        }
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) >= self.max_bytes:
            record["message"] = "diagnostic record truncated"
            record["context"] = {"original_record_bytes": len(encoded)}
            encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            current_size = self.path.stat().st_size if self.path.exists() else 0
            if current_size and current_size + len(encoded) > self.max_bytes:
                self._rotate()
            self._make_room(len(encoded))
            with self.path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def info(self, code: str, *, phase: str, message: str = "", **context: Any) -> dict[str, Any]:
        return self.emit(code, phase=phase, message=message, **context)

    def error(self, code: str, *, phase: str, message: str = "", **context: Any) -> dict[str, Any]:
        return self.emit(code, phase=phase, message=message, **context)


__all__ = ["DiagnosticLogger"]
