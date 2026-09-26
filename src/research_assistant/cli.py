"""UTF-8 JSON command interface; no generic HTTP or approval command."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
import webbrowser
from pathlib import Path

from .client import CONTRACT, AssistantError, LocalClient, atomic_json


def read_json(path):
    raw = sys.stdin.read(16385) if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    if len(raw.encode("utf-8")) > 16384:
        raise AssistantError("input_too_large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise AssistantError("input_object_required")
    return result


def compact(value):
    if isinstance(value, dict):
        result = {k: compact(v) for k, v in value.items() if k != "rows"}
        if isinstance(value.get("rows"), list):
            rows = value["rows"]
            result.update(row_count=len(rows), latest_rows=rows[-1:], rows_omitted=max(0, len(rows)-1))
        return result
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def parser():
    p = argparse.ArgumentParser(description="本機台股研究工具；JSON 輸出，不提供核准或撤銷")
    p.add_argument("--install-root", type=Path)
    p.add_argument("--user-root", type=Path)
    p.add_argument("--full", action="store_true", help="include all historical rows")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("connect")
    search = sub.add_parser("search")
    search.add_argument("query")
    research = sub.add_parser("research")
    research.add_argument("query")
    research.add_argument("--wait-seconds", type=float, default=120)
    for cmd in ("review", "update", "open"):
        q = sub.add_parser(cmd)
        q.add_argument("symbol")
        if cmd == "open":
            q.add_argument("--assumptions", action="store_true")
        if cmd == "update":
            q.add_argument("--refresh", action="store_true")
    for cmd in ("operation", "wait", "cancel"):
        q = sub.add_parser(cmd)
        q.add_argument("operation_id")
        if cmd == "wait":
            q.add_argument("--seconds", type=float, default=120)
    for cmd in ("assumption-preview", "assumption-draft"):
        q = sub.add_parser(cmd)
        q.add_argument("symbol")
        q.add_argument("kind", choices=("eps", "pe", "anchor"))
        q.add_argument("--input", required=True, help="UTF-8 JSON file, or - for stdin")
        if cmd == "assumption-draft":
            q.add_argument("--confirmed", action="store_true", required=True)
            q.add_argument("--request-id", required=True)
    save = sub.add_parser("save")
    save.add_argument("--review", type=Path, required=True)
    save.add_argument("--note-file", type=Path, required=True)
    save.add_argument("--request-id", required=True)
    save.add_argument("--confirmed", action="store_true", required=True)
    return p


def roots(args):
    local = os.environ.get("LOCALAPPDATA")
    if not local and (args.install_root is None or args.user_root is None):
        raise AssistantError("local_app_data_missing")
    # The executable lives in {app}/research; development uses the installed app.
    install = args.install_root or (Path(sys.executable).resolve().parent.parent
                                    if getattr(sys, "frozen", False)
                                    else Path(local) / "Programs" / "tw-stock-predictor")
    user = args.user_root or Path(local) / "tw-stock-predictor"
    return install, user


def execute(args, client):
    connection = client.connect(start=args.command != "doctor")
    cmd = args.command
    if cmd in {"connect", "doctor"}:
        return {"status":"ready", "origin":client.origin, "build_sha":client.descriptor["build_sha"],
                "active_operation":client.active_operation(),
                "valuation_pairing_policy":connection.get("valuation_pairing_policy")}
    if cmd == "search":
        return client.search(args.query)
    if cmd == "research":
        if not 0 <= args.wait_seconds <= 120:
            raise AssistantError("wait_out_of_range")
        result = client.research(args.query, seconds=args.wait_seconds)
    elif cmd in {"review", "update", "open", "assumption-preview", "assumption-draft"}:
        symbol, selection = client.resolve(args.symbol)
        if symbol is None:
            return selection
        if cmd == "review":
            result = {"status":"review_ready", "review":client.review(symbol)}
        elif cmd == "update":
            return client.update(symbol, refresh=args.refresh)
        elif cmd == "open":
            url = client.page_url(symbol, assumptions=args.assumptions)
            opened = webbrowser.open(url)
            return {"status":"opened" if opened else "open_manually", "url":url}
        else:
            payload = read_json(args.input)
            result = client.assumption(symbol, args.kind, payload, draft=cmd == "assumption-draft",
                                       key=getattr(args, "request_id", None))
            result["confirmation_url"] = client.page_url(symbol, assumptions=True)
            result["approval_required"] = True
            return result
    elif cmd == "operation":
        return client.operation(args.operation_id)
    elif cmd == "wait":
        return client.wait(args.operation_id, seconds=args.seconds)
    elif cmd == "cancel":
        return client.cancel(args.operation_id)
    elif cmd == "save":
        # The review is local evidence, not instructions. Only selected fields
        # are sent; never accept a URL or arbitrary API path from the file.
        reviewed = json.loads(args.review.read_text(encoding="utf-8"))
        if not isinstance(reviewed, dict):
            raise AssistantError("invalid_review_receipt")
        note = args.note_file.read_text(encoding="utf-8-sig")
        return client.save(reviewed, note, args.request_id)
    else:
        raise AssistantError("unsupported_command")
    if result.get("review"):
        path = client.assistant_dir / "reviews" / f"{uuid.uuid4().hex}.json"
        try:
            atomic_json(path, result["review"])
            result["review_file"] = str(path)
        except (AssistantError, OSError):
            result["save_unavailable_reason"] = "review_receipt_not_saved_check_storage"
        result["research_url"] = client.page_url(result["review"]["symbol"])
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        install, user = roots(args)
        result = execute(args, LocalClient(install, user))
        output = {"contract_version":CONTRACT, **(result if args.full else compact(result))}
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 0
    except AssistantError as exc:
        print(json.dumps({"contract_version":CONTRACT, "status":"error", "reason":exc.code,
                          "http_status":exc.status}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"contract_version":CONTRACT, "status":"error", "reason":"invalid_input_or_local_file"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
