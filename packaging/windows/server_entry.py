"""PyInstaller server entrypoint; all startup work is local and fail-closed."""

from __future__ import annotations

import json
import os
import sys

from src.runtime.server import ServerStartupError, run_server
from src.runtime.settings import RuntimeConfigurationError, RuntimeSettings


def _packaged_settings() -> RuntimeSettings:
    environment = dict(os.environ)
    # A frozen executable must not depend on an inherited development-mode
    # environment variable to select its resource and user-state boundaries.
    environment["TW_STOCK_PACKAGED"] = "true"
    return RuntimeSettings.from_environment(environment)


def main() -> int:
    try:
        return run_server(_packaged_settings())
    except (RuntimeConfigurationError, ServerStartupError) as exc:
        print(
            json.dumps(
                {"status": "failed", "code": getattr(exc, "code", "server_startup_failed")},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
