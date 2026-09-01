"""PyInstaller launcher entrypoint for local start/stop only."""

from __future__ import annotations

import argparse
import json
import os
import sys

from src.runtime.diagnostics import DiagnosticLogger
from src.runtime.launcher import Launcher, stop_existing
from src.runtime.paths import RuntimePathError, RuntimePaths
from src.runtime.recovery_cli import main as recovery_main
from src.runtime.settings import RuntimeConfigurationError, RuntimeSettings


def _packaged_settings(user_root: str | None = None) -> RuntimeSettings:
    environment = dict(os.environ)
    # A frozen executable must always use the local-only packaged boundary.
    environment["TW_STOCK_PACKAGED"] = "true"
    paths = RuntimePaths.from_environment(environment, packaged_user_root=user_root)
    return RuntimeSettings.from_environment(environment, paths=paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start or stop the local research application")
    parser.add_argument("--stop", action="store_true", help="stop the local instance through local process control")
    parser.add_argument("--user-root", default=None, help="explicit per-user root override")
    parser.add_argument("command", nargs="?", choices=("recovery",))
    parser.add_argument("command_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        settings = _packaged_settings(args.user_root)
        if args.command == "recovery":
            return recovery_main(args.command_args, paths=settings.paths, packaged=True)
        if args.stop:
            result = stop_existing(settings)
        else:
            logger = DiagnosticLogger(
                settings.paths.logs_dir,
                "launcher.log",
                app_version=settings.app_version,
                build_sha=settings.build_sha,
            )
            launcher = Launcher(settings, logger=logger)
            result = launcher.start()
            if result.status == "started":
                print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
                launcher.wait_for_server()
                return 0
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0 if result.status in {"started", "stopped", "not_running", "existing_instance"} else 2
    except (RuntimeConfigurationError, RuntimePathError) as exc:
        print(json.dumps({"status": "failed", "code": getattr(exc, "code", "runtime_configuration_invalid")}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
