"""Build an immutable provider-compatible adjusted snapshot from TWSE gap rows."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.research.official_gap_adjustment import (
    CONTRACT_VERSION,
    build_reconciled_snapshot,
    safe_symbol,
    snapshot_fingerprint,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="建立不覆寫來源的 TWSE 官方缺日 adjusted snapshot"
    )
    parser.add_argument("--source-snapshot", required=True)
    parser.add_argument("--audit-report", required=True)
    parser.add_argument("--db-path", default="data/cache.db")
    parser.add_argument("--output-root", default="reports/backtest")
    parser.add_argument("--run-id", default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def write_snapshot(result, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    data_dir = output_dir / "data"
    data_dir.mkdir()
    for symbol, frame in result.snapshots.items():
        frame.to_csv(
            data_dir / f"{safe_symbol(symbol)}.csv", index=False, encoding="utf-8"
        )
    pd.DataFrame(result.provenance).to_csv(
        output_dir / "data_provenance.csv", index=False, encoding="utf-8"
    )
    pd.DataFrame(result.adjustments).drop(columns=["adjusted_row"], errors="ignore").to_csv(
        output_dir / "official_gap_adjustments.csv", index=False, encoding="utf-8"
    )
    pd.DataFrame(result.unresolved).to_csv(
        output_dir / "official_gap_unresolved.csv", index=False, encoding="utf-8"
    )
    pd.DataFrame(result.zero_volume_reconciliation).to_csv(
        output_dir / "zero_volume_reconciliation.csv", index=False, encoding="utf-8"
    )
    (output_dir / "snapshot_manifest.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_reconciled_snapshot(
        args.source_snapshot, args.audit_report, args.db_path
    )
    fingerprint = snapshot_fingerprint(result)
    run_id = args.run_id or datetime.now().strftime("phase2-gap-adjusted-v1-%Y%m%dT%H%M%S")
    output_dir = Path(args.output_root) / f"{run_id}-{fingerprint[:12]}"
    result.summary.update({
        "snapshot_fingerprint_sha256": fingerprint,
        "output_dir": str(output_dir.resolve()),
        "dry_run": bool(args.dry_run),
    })
    if args.execute:
        write_snapshot(result, output_dir)

    if args.json:
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"{CONTRACT_VERSION}: resolved={result.summary['resolved_gap_count']} "
            f"unresolved={result.summary['unresolved_gap_count']} "
            f"quality_approved={result.summary['quality_approved_symbol_count']}/"
            f"{result.summary['target_symbol_count']} output={output_dir.resolve()}"
        )
    return 1 if result.summary["unresolved_gap_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
