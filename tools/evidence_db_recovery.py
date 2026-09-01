"""Offline backup, restore, and integrity validation for Evidence V2 SQLite."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.runtime.recovery_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
