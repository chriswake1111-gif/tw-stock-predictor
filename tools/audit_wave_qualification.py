"""Read local qualification or replay pinned synthetic fixtures. Never fetch data.

Examples:
  python tools/audit_wave_qualification.py --db EXISTING.db --symbol 9911.TWO
  python tools/audit_wave_qualification.py --fixture-manifest tests/fixtures/wave_pivots/manifest.json
Append --output-dir NEW_DIRECTORY for bounded JSON and Markdown reports.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.wave_pivot_lab import run_manifest
from src.services.wave_qualification_service import WaveQualificationService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--db", help="existing database; opened read-only")
    inputs.add_argument("--fixture-manifest", help="pinned synthetic fixture manifest")
    parser.add_argument("--symbol", help="one explicit canonical stock symbol")
    parser.add_argument("--output-dir", help="new directory; existing paths are never overwritten")
    args = parser.parse_args(argv)
    if bool(args.db) != bool(args.symbol):
        parser.error("--db requires --symbol; fixture mode does not accept --symbol")
    try:
        if args.db:
            result = WaveQualificationService(args.db).get(args.symbol)
            result["lab_replay"] = "not_run_real_data_qualification_only"
        else:
            result = run_manifest(args.fixture_manifest)
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if len(encoded.encode("utf-8")) > 1024 * 1024:
            raise ValueError("report_size_limit")
        if args.output_dir:
            output = Path(args.output_dir).resolve()
            output.mkdir(parents=True, exist_ok=False)
            (output / "report.json").write_text(encoded, encoding="utf-8")
            lines = ["# 自動波段資格／隔離驗證", "", "此報告不授權正式候選、核准、保存或交易。", ""]
            if args.db:
                lines += [result.get("headline", "功能關閉"), ""]
                for item in result.get("checks", []):
                    label = {"passed":"已確認", "failed":"未通過", "unknown":"尚無證據"}[item["status"]]
                    lines += [f"- {item['title']}：{label}。{item['reason']} {item['impact']}"]
            else:
                lines += [f"固定測試資料：{len(result['results'])} 份；逐段重播通過。", "歷史當時可用性與正式候選資格均未宣稱通過。"]
            (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(encoded, end="")
        return 1 if result.get("status") == "unavailable" else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status":"error", "reason":str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
