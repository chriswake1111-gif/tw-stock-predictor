"""One symbol and explicit interval. Preview by default, no implicit migrations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.wave_candidate_refresh import refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true", help="Explicitly recheck within the 24-hour reuse window")
    args = parser.parse_args()
    try:
        result = refresh(args.db, args.symbol, args.start, args.end, request_key=args.request_key,
                         execute=args.execute, force=args.force)
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(status="error", reason=str(exc)), ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["status"] != "incomplete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
