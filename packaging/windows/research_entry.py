"""PyInstaller entrypoint for the local research assistant."""

from __future__ import annotations

import sys

from src.research_assistant.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
