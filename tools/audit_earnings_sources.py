"""Audit bounded local filings and issuer releases; never writes financial data."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.earnings_source_audit import (  # noqa: E402
    MAX_DOCUMENT_BYTES, EarningsSourceAuditError, audit_earnings_document, compare_earnings_sources,
)
from src.collectors.earnings_note_audit import (  # noqa: E402
    MAX_PDF_BYTES, NOTE_VERSION, audit_earnings_note, compare_earnings_notes,
)
from src.collectors.earnings_quarter_audit import (  # noqa: E402
    QUARTER_VERSION, audit_quarter_release, compare_quarter_releases,
)
from src.collectors.earnings_four_quarter import (  # noqa: E402
    MAX_DOCUMENT_BYTES as MAX_REVIEWED_PDF_BYTES, normalize_bundle,
)
from src.collectors.earnings_sources_v2 import sources_for  # noqa: E402
from src.collectors.earnings_coverage import plan_coverage  # noqa: E402
from src.domain.valuation import normalize_utc_timestamp  # noqa: E402


def _manifest(path):
    with path.open("rb") as handle:
        raw = handle.read(16385)
    if len(raw) > 16384:
        raise EarningsSourceAuditError("manifest_too_large")
    items = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(items, list) or not 1 <= len(items) <= 12:
        raise EarningsSourceAuditError("manifest_requires_1_to_12_documents")
    if any(not isinstance(item, dict) for item in items):
        raise EarningsSourceAuditError("invalid_manifest_item")
    return items


def _source_bytes(root, item, limit):
    path = (root / item["path"]).resolve()
    if path.parent != root or not path.is_file():
        raise EarningsSourceAuditError("document_must_be_in_manifest_directory")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise EarningsSourceAuditError("document_size_invalid")
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise EarningsSourceAuditError("source_hash_mismatch")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description="唯讀稽核本機財報來源；不啟用四季合計或寫入研究")
    parser.add_argument("--coverage-plan", action="store_true", help="只列出指定四季窗口的來源核對工作，不抓網站或啟用計算")
    parser.add_argument("--symbol", help="來源規劃的公司代碼，例如 3491.TWO")
    parser.add_argument("--window-end", help="來源規劃的季度末日，例如 2026-09-30")
    parser.add_argument("--manifest", type=Path,
                        help="最多 12 份公開財報的取得紀錄，文件須位於清單同目錄")
    parser.add_argument("--output", type=Path, help="另存稽核結果；拒絕覆寫既有檔案")
    parser.add_argument("--notes-manifest", type=Path,
                        help="可選：最多 12 份完整財報，依 filing_sha256 對應本次結構化財報")
    parser.add_argument("--quarter-releases-manifest", type=Path,
                        help="可選：最多 6 組公司季報及財務明細，核對直接公布的單季數字")
    parser.add_argument("--four-quarter-manifest", type=Path,
                        help="可選：一家公司已登錄版本的公開文件，重跑四季樣本計算；不匯入金融資料")
    args = parser.parse_args(argv)
    if args.coverage_plan:
        if (not args.symbol or not args.window_end or args.manifest or args.notes_manifest
                or args.quarter_releases_manifest or args.four_quarter_manifest):
            parser.error("--coverage-plan 需 --symbol 與 --window-end，且不能混用文件稽核參數")
        try:
            report = plan_coverage(args.symbol, args.window_end)
            output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(output)
            else:
                print(output)
            return 0
        except (ValueError, OSError) as exc:
            print(json.dumps(dict(status="invalid_input", reason=type(exc).__name__)))
            return 2
    if args.symbol or args.window_end:
        parser.error("--symbol 與 --window-end 僅供 --coverage-plan 使用")
    if not args.manifest and not args.quarter_releases_manifest and not args.four_quarter_manifest:
        parser.error("需要 --manifest、--quarter-releases-manifest 或 --four-quarter-manifest")
    if args.notes_manifest and not args.manifest:
        parser.error("--notes-manifest 需要 --manifest")
    try:
        manifest = _manifest(args.manifest) if args.manifest else []
        audits = []
        root = args.manifest.resolve().parent if args.manifest else None
        for item in manifest:
            raw = _source_bytes(root, item, MAX_DOCUMENT_BYTES)
            audits.append(audit_earnings_document(raw, symbol=item["symbol"],
                year=item["year"], quarter=item["quarter"], source_url=item["url"],
                observed_at=item["observed_at"]))
        report = dict(status="source_gate_not_passed", calculation_enabled=False,
                      document_count=len(audits), comparisons=compare_earnings_sources(audits), audits=audits)
        if args.notes_manifest:
            notes, seen = [], set()
            for item in _manifest(args.notes_manifest):
                filings = [audit for audit in audits if audit["raw_sha256"] == item["filing_sha256"]]
                if len(filings) != 1:
                    raise EarningsSourceAuditError("note_filing_reference_missing_or_ambiguous")
                identity = (item["sha256"], item["filing_sha256"])
                if identity in seen:
                    raise EarningsSourceAuditError("duplicate_note_reference")
                seen.add(identity)
                raw = _source_bytes(args.notes_manifest.resolve().parent, item, MAX_PDF_BYTES)
                notes.append(audit_earnings_note(raw, filing=filings[0], source_url=item["url"],
                                                observed_at=item["observed_at"]))
            report.update(note_contract=NOTE_VERSION, note_audits=notes,
                          note_comparisons=compare_earnings_notes(notes),
                          filings_without_notes=[audit["raw_sha256"] for audit in audits
                              if audit["raw_sha256"] not in {note["filing_sha256"] for note in notes}])
        if args.quarter_releases_manifest:
            releases, seen = [], set()
            pairs = _manifest(args.quarter_releases_manifest)
            if len(pairs) > 6:
                raise EarningsSourceAuditError("quarter_manifest_maximum_6_pairs")
            for item in pairs:
                if set(item) != {"symbol", "year", "quarter", "report", "statements"}:
                    raise EarningsSourceAuditError("quarter_manifest_fields_invalid")
                for key in ("report", "statements"):
                    receipt = item[key]
                    if (not isinstance(receipt, dict) or not {"path", "sha256", "url", "observed_at"} <= set(receipt)
                            or set(receipt) - {"path", "sha256", "url", "observed_at", "bytes"}
                            or any(not isinstance(receipt[field], str) or not receipt[field]
                                   for field in ("path", "sha256", "url", "observed_at"))
                            or ("bytes" in receipt and (type(receipt["bytes"]) is not int
                                                       or not 0 < receipt["bytes"] <= MAX_PDF_BYTES))):
                        raise EarningsSourceAuditError("quarter_receipt_fields_invalid")
                identity = (item["report"]["sha256"], item["statements"]["sha256"])
                if identity in seen:
                    raise EarningsSourceAuditError("duplicate_quarter_release_reference")
                seen.add(identity)
                root = args.quarter_releases_manifest.resolve().parent
                report_raw = _source_bytes(root, item["report"], MAX_PDF_BYTES)
                statement_raw = _source_bytes(root, item["statements"], MAX_PDF_BYTES)
                for key, raw in (("report", report_raw), ("statements", statement_raw)):
                    if "bytes" in item[key] and item[key]["bytes"] != len(raw):
                        raise EarningsSourceAuditError("quarter_receipt_size_mismatch")
                releases.append(audit_quarter_release(report_raw, statement_raw,
                    symbol=item["symbol"], year=item["year"], quarter=item["quarter"],
                    report_source=item["report"], statement_source=item["statements"]))
            report.update(quarter_release_contract=QUARTER_VERSION, quarter_release_audits=releases,
                          quarter_release_comparisons=compare_quarter_releases(releases))
        if args.four_quarter_manifest:
            items = _manifest(args.four_quarter_manifest)
            symbols = {item["symbol"] for item in items}
            if len(symbols) != 1:
                raise EarningsSourceAuditError("four_quarter_manifest_requires_one_company")
            symbol = symbols.pop()
            catalog = {s["key"]: s for s in sources_for(symbol)}
            documents, times = {}, []
            for item in items:
                if set(item) != {"symbol", "key", "path", "sha256", "url", "observed_at"}:
                    raise EarningsSourceAuditError("four_quarter_manifest_fields_invalid")
                key = item["key"]
                if key not in catalog or key in documents or item["url"] != catalog[key]["url"]:
                    raise EarningsSourceAuditError("four_quarter_manifest_source_mismatch")
                documents[key] = _source_bytes(args.four_quarter_manifest.resolve().parent, item, MAX_REVIEWED_PDF_BYTES)
                times.append(normalize_utc_timestamp(item["observed_at"], "observed_at"))
            sample = normalize_bundle(symbol, documents, max(times))
            report["four_quarter_sample"] = sample
            report["sample_provenance"] = "content_pinned_local_files_transport_not_authenticated"
            # Earlier audit categories retain their original closed gates.
            if not audits and not args.quarter_releases_manifest and sample["status"] == "available":
                report["status"] = "four_quarter_sample_calculated"
        output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if len(output.encode("utf-8")) > 2 * 1024 * 1024:
            raise EarningsSourceAuditError("audit_report_too_large")
        if args.output:
            # Exclusive creation preserves existing source files, reports and receipts.
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(output)
            print(json.dumps(dict(status=report["status"], document_count=len(audits),
                                  quarter_release_count=len(report.get("quarter_release_audits", [])),
                                  output_written=True), ensure_ascii=False))
        else:
            print(output)
        return 0 if report["status"] == "four_quarter_sample_calculated" else 1
    except (EarningsSourceAuditError, ValueError, TypeError, KeyError, OSError) as exc:
        reason = str(exc) if isinstance(exc, EarningsSourceAuditError) else type(exc).__name__
        print(json.dumps({"status": "invalid_input", "reason": reason}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
