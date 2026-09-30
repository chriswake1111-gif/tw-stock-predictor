"""Preview or explicitly collect official session evidence for one stock/range.

Requires an existing DB; never migrates or creates it. Default: no network or
writes. --execute appends independent evidence only.
Example: python tools/update_wave_session_evidence.py --db copy.db --symbol 3491.TWO
         --start 2026-07-01 --end 2026-07-31 --request-key unique-command-20260930
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.wave_session_refresh import refresh


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source", action="append", help="optional registered source ID; repeat to retry only selected sources")
    parser.add_argument("--force", action="store_true", help="explicitly recheck an otherwise retained 24-hour result")
    args = parser.parse_args(argv)
    try:
        result = refresh(args.db, args.symbol, args.start, args.end, request_key=args.request_key, execute=args.execute, force=args.force, source_ids=args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if any(i["status"] in {"failed", "not_attempted_deadline"} for i in result["items"]) else 0
    except Exception as exc:
        print(json.dumps(dict(status="error", reason=str(exc) if isinstance(exc, ValueError) else "wave_session_local_operation_failed"), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
