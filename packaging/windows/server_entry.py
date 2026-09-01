"""PyInstaller server entrypoint; all startup work is local and fail-closed."""

from __future__ import annotations

import argparse
import json
import os
import sys

from src.runtime.server import ServerStartupError, run_server
from src.runtime.paths import RuntimePathError, RuntimePaths
from src.runtime.settings import RuntimeConfigurationError, RuntimeSettings


def _packaged_settings(user_root: str | None = None) -> RuntimeSettings:
    environment = dict(os.environ)
    # A frozen executable must not depend on an inherited development-mode
    # environment variable to select its resource and user-state boundaries.
    environment["TW_STOCK_PACKAGED"] = "true"
    paths = RuntimePaths.from_environment(environment, packaged_user_root=user_root)
    return RuntimeSettings.from_environment(environment, paths=paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal packaged server")
    parser.add_argument("--user-root", default=None)
    args = parser.parse_args()
    try:
        return run_server(_packaged_settings(args.user_root))
    except (RuntimeConfigurationError, RuntimePathError, ServerStartupError) as exc:
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
