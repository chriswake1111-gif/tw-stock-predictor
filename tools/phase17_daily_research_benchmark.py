"""Reproducible Phase 17 Daily Research queue benchmark.

The benchmark creates deterministic, migrated SQLite fixtures under an
isolated work directory.  It never opens the configured application database,
calls a provider, or writes benchmark state into the repository database.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from math import ceil
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.research_workflow import WORKFLOW_CONTRACT_VERSION
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.daily_research_read_repository import DailyResearchReadRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.services.daily_research_review_context_service import (
    DailyResearchReviewContextService,
)


CONTRACT_VERSION = "phase17_daily_research_benchmark_v1"
MANIFEST_SCHEMA_VERSION = "phase17_daily_research_benchmark_manifest_v1"
BENCHMARK_MIGRATION_VERSION = "20260828_20_phase14_third_code_review_remediation"
D = "2026-08-31"
K = "2026-08-31T00:00:00Z"
PAGE_LIMIT = 25
QUEUE_SIZES = (25, 50, 100, 200)
SAMPLE_COUNT = 30
MAX_PAGE_READ_STATEMENTS = 32


def manifest_sha256(manifest: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "fixture_sha256"}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_manifest(manifest)
    expected = str(manifest.get("fixture_sha256") or "").strip().lower()
    if expected and expected != manifest_sha256(manifest):
        raise ValueError("fixture_sha256_mismatch")
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("benchmark_contract_version_mismatch")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("benchmark_manifest_schema_mismatch")
    if manifest.get("migration_version") != BENCHMARK_MIGRATION_VERSION:
        raise ValueError("benchmark_migration_version_mismatch")
    if manifest.get("market_date") != D or manifest.get("knowledge_cutoff_at") != K:
        raise ValueError("benchmark_d_k_mismatch")
    if tuple(manifest.get("queue_sizes") or ()) != QUEUE_SIZES:
        raise ValueError("benchmark_queue_sizes_mismatch")
    if manifest.get("page_limit") != PAGE_LIMIT:
        raise ValueError("benchmark_page_limit_mismatch")
    samples = manifest.get("samples") or {}
    if samples.get("cold") != SAMPLE_COUNT or samples.get("warm") != SAMPLE_COUNT:
        raise ValueError("benchmark_sample_count_mismatch")
    if samples.get("percentile") != "nearest_rank":
        raise ValueError("benchmark_percentile_mismatch")
    budget = manifest.get("query_budget") or {}
    if budget.get("max_app_read_statements_per_page") != MAX_PAGE_READ_STATEMENTS:
        raise ValueError("benchmark_query_budget_mismatch")
    if budget.get("query_count_independent_of_queue_size") is not True:
        raise ValueError("benchmark_query_scaling_policy_mismatch")
    fixture = manifest.get("fixture") or {}
    if fixture.get("network") is not False or fixture.get("production_database") is not False:
        raise ValueError("benchmark_safety_manifest_mismatch")


def nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or not 0 < percentile <= 1:
        raise ValueError("percentile requires non-empty values and 0<p<=1")
    return ordered[ceil(percentile * len(ordered)) - 1]


def percentile_summary(values: list[float]) -> dict[str, float | int]:
    return {
        "p95_seconds": nearest_rank(values, 0.95),
        "p99_seconds": nearest_rank(values, 0.99),
        "min_seconds": min(values),
        "max_seconds": max(values),
        "sample_count": len(values),
    }


def _physical_memory_bytes() -> int | None:
    if os.name == "nt":
        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except (AttributeError, OSError):
            return None
        return None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
    return page_size * page_count if page_size > 0 and page_count > 0 else None


def environment_metadata() -> dict[str, Any]:
    memory_total = _physical_memory_bytes()
    cpu_parts = [
        " ".join(str(value or "").split())
        for value in (platform.processor(), platform.uname().processor)
        if str(value or "").strip()
    ]
    cpu_model = " ".join(cpu_parts)
    if not cpu_model:
        cpu_model = platform.machine() or "unknown"
    return {
        "os": platform.platform(),
        "cpu_class": platform.machine() or "unknown",
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": memory_total,
        "memory_class": (
            "unknown"
            if not memory_total
            else f"{memory_total / float(1024 ** 3):.1f}GiB"
        ),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "worker_count": 1,
    }


def _symbol(index: int) -> str:
    suffix = ".TW" if index % 2 == 0 else ".TWO"
    return f"{2301 + index:04d}{suffix}"


def _created_at(index: int) -> str:
    return f"2026-08-30T{index // 60:02d}:{index % 60:02d}:00Z"


def _snapshot_status(index: int) -> str:
    return ("available", "partial", "insufficient_data")[index % 3]


def build_database(database: Path, queue_size: int) -> dict[str, Any]:
    database.parent.mkdir(parents=True, exist_ok=True)
    apply_valuation_migration(str(database))
    snapshots = AnalysisSnapshotRepository(str(database), auto_migrate=False)
    snapshot_ids: dict[str, str] = {}
    fixed_migration_time = "2026-08-01T00:00:00Z"
    memberships: list[dict[str, Any]] = []
    with sqlite3.connect(database) as conn:
        for index in range(queue_size):
            symbol = _symbol(index)
            item_id = "research_watchlist_" + hashlib.sha256(
                symbol.encode("utf-8")
            ).hexdigest()[:24]
            membership = {
                "watchlist_item_id": item_id,
                "symbol": symbol,
                "membership_state": "active",
                "created_at": fixed_migration_time,
                "updated_at": fixed_migration_time,
                "archived_at": None,
                "workflow_contract_version": WORKFLOW_CONTRACT_VERSION,
            }
            conn.execute(
                "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
                tuple(membership.values()),
            )
            memberships.append(membership)
        archived_symbol = "8899.TW"
        archived_id = "research_watchlist_" + hashlib.sha256(
            archived_symbol.encode("utf-8")
        ).hexdigest()[:24]
        archived = {
            "watchlist_item_id": archived_id,
            "symbol": archived_symbol,
            "membership_state": "archived",
            "created_at": fixed_migration_time,
            "updated_at": fixed_migration_time,
            "archived_at": fixed_migration_time,
            "workflow_contract_version": WORKFLOW_CONTRACT_VERSION,
        }
        conn.execute(
            "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
            tuple(archived.values()),
        )
    for index, membership in enumerate(memberships):
        # Keep several explicit workflow states in the fixture: no snapshot,
        # different persisted statuses, and acknowledged baselines.
        if index % 6 == 0:
            continue
        snapshot = snapshots.add(
            AnalysisSnapshot(
                symbol=membership["symbol"],
                knowledge_cutoff_at="2026-08-30T00:00:00Z",
                capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
                model_version="2.0.0",
                used_rule_versions={},
                source_resource_versions=[],
                manual_approval_ids=[],
                output={
                    "status": _snapshot_status(index),
                    "symbol": membership["symbol"],
                    "fixture_index": index,
                },
                created_at=_created_at(index),
            ),
            f"phase17-benchmark-snapshot-{queue_size}-{index}",
        )
        snapshot_ids[membership["watchlist_item_id"]] = snapshot["snapshot_id"]
        if index % 5 == 1:
            review_key = f"phase17-benchmark-review-{queue_size}-{index}"
            review_event_id = "research_review_" + hashlib.sha256(
                review_key.encode("utf-8")
            ).hexdigest()[:24]
            with sqlite3.connect(database) as conn:
                conn.execute(
                    "INSERT INTO research_review_events VALUES (?,?,?,?,?,?,?,?)",
                    (
                        review_event_id,
                        membership["watchlist_item_id"],
                        snapshot["snapshot_id"],
                        "2026-08-30T00:00:00Z",
                        "2026-08-30T12:00:00Z",
                        "2026-08-30T12:00:01Z",
                        review_key,
                        WORKFLOW_CONTRACT_VERSION,
                    ),
                )
    with sqlite3.connect(database) as conn:
        # Repository write APIs intentionally timestamp live records.  The
        # benchmark fixture is a separate deterministic artifact, so freeze
        # only its non-semantic audit timestamps after fixture construction.
        conn.execute(
            "UPDATE schema_migrations SET applied_at = ?",
            (fixed_migration_time,),
        )
        conn.execute(
            "UPDATE additive_schema_migrations SET applied_at = ?",
            (fixed_migration_time,),
        )
        conn.execute(
            "UPDATE analysis_snapshot_idempotency_keys SET bound_at = ?",
            (fixed_migration_time,),
        )
        active_count = conn.execute(
            "SELECT COUNT(*) FROM research_watchlist_items WHERE membership_state='active'"
        ).fetchone()[0]
        archived_count = conn.execute(
            "SELECT COUNT(*) FROM research_watchlist_items WHERE membership_state='archived'"
        ).fetchone()[0]
    if active_count != queue_size or archived_count != 1:
        raise RuntimeError("benchmark_fixture_queue_count_mismatch")
    return {
        "queue_size": queue_size,
        "active_count": active_count,
        "archived_count": archived_count,
        "snapshot_count": len(snapshot_ids),
    }


class TracingDailyResearchReadRepository(DailyResearchReadRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.statements: list[str] = []

    def _connect(self):
        conn = super()._connect()
        conn.set_trace_callback(self.statements.append)
        return conn

    def reset_trace(self) -> None:
        self.statements.clear()

    def read_statement_count(self, statements: Iterable[str]) -> int:
        return sum(
            str(statement).lstrip().upper().startswith(("SELECT", "WITH"))
            for statement in statements
        )


def run_request(
    service: DailyResearchReviewContextService,
    reader: TracingDailyResearchReadRepository,
    *,
    mode: str,
) -> dict[str, Any]:
    if mode not in {"first", "all"}:
        raise ValueError("benchmark_mode_invalid")
    reader.reset_trace()
    started = time.perf_counter()
    cursor = None
    pages: list[dict[str, Any]] = []
    page_query_counts: list[int] = []
    while True:
        before = len(reader.statements)
        response = service.list(
            market_date=D,
            knowledge_cutoff_at=K,
            request_received_at="2026-08-31T09:00:00Z",
            limit=PAGE_LIMIT,
            cursor=cursor,
        )
        page_query_counts.append(
            reader.read_statement_count(reader.statements[before:])
        )
        pages.append(response)
        if mode == "first" or not response.get("next_cursor"):
            break
        cursor = response["next_cursor"]
        if len(pages) > 100:
            raise RuntimeError("benchmark_pagination_did_not_terminate")
    elapsed = time.perf_counter() - started
    page_rows = [len(page.get("items", [])) for page in pages]
    if any(row_count > PAGE_LIMIT for row_count in page_rows):
        raise RuntimeError("benchmark_page_bound_violation")
    return {
        "elapsed_seconds": elapsed,
        "page_mode": mode,
        "page_rows": page_rows,
        "page_query_counts": page_query_counts,
        "max_page_query_count": max(page_query_counts),
        "total_read_statements": sum(page_query_counts),
        "process_pid": os.getpid(),
        "process_isolated": False,
    }


def run_cold_sample(database: Path, *, mode: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--database",
        str(database.resolve()),
        "--mode",
        mode,
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"benchmark_cold_worker_failed:{detail}")
    try:
        sample = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("benchmark_cold_worker_invalid_json") from exc
    if sample.get("process_pid") == os.getpid() or not sample.get("process_isolated"):
        raise RuntimeError("benchmark_cold_process_not_isolated")
    sample["elapsed_seconds"] = elapsed
    sample["mode"] = "cold"
    return sample


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _database_sha256(database: Path) -> str:
    digest = hashlib.sha256()
    with database.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_benchmark(
    manifest: dict[str, Any],
    *,
    sizes: tuple[int, ...],
    runs: int,
    work_dir: Path,
) -> dict[str, Any]:
    if not sizes or any(size not in QUEUE_SIZES for size in sizes):
        raise ValueError("benchmark_size_invalid")
    if runs < 1:
        raise ValueError("benchmark_runs_invalid")
    summaries: dict[str, Any] = {}
    for size in sizes:
        database = work_dir / f"queue-{size}" / "benchmark.sqlite"
        fixture = build_database(database, size)
        warm_reader = TracingDailyResearchReadRepository(str(database))
        warm_service = DailyResearchReviewContextService(
            str(database), read_repository=warm_reader
        )
        samples: list[dict[str, Any]] = []
        for mode_name, mode_count in (("cold", runs), ("warm", runs)):
            for sample_index in range(mode_count):
                page_mode = "first" if sample_index % 2 == 0 else "all"
                if mode_name == "cold":
                    sample = run_cold_sample(database, mode=page_mode)
                else:
                    sample = run_request(
                        warm_service, warm_reader, mode=page_mode
                    )
                    sample["mode"] = "warm"
                sample.update({"sample_index": sample_index})
                samples.append(sample)
        cold = [sample["elapsed_seconds"] for sample in samples if sample["mode"] == "cold"]
        warm = [sample["elapsed_seconds"] for sample in samples if sample["mode"] == "warm"]
        first_page_counts = [
            sample["page_query_counts"][0]
            for sample in samples
            if sample["page_mode"] == "first"
        ]
        continuation_counts = [
            count
            for sample in samples
            if sample["page_mode"] == "all"
            for count in sample["page_query_counts"]
        ]
        summaries[str(size)] = {
            "fixture": fixture,
            "fixture_database_sha256": _database_sha256(database),
            "cold": percentile_summary(cold),
            "warm": percentile_summary(warm),
            "first_page_query_counts": sorted(set(first_page_counts)),
            "continuation_page_query_counts": sorted(set(continuation_counts)),
            "max_page_query_count": max(sample["max_page_query_count"] for sample in samples),
            "max_page_rows": max(max(sample["page_rows"]) for sample in samples),
            "sample_process_ids": {
                "cold": sorted({sample["process_pid"] for sample in samples if sample["mode"] == "cold"}),
                "warm": sorted({sample["process_pid"] for sample in samples if sample["mode"] == "warm"}),
            },
            "sample_count": len(samples),
        }
        del warm_service
        del warm_reader
        gc.collect()
    return {
        "contract_version": CONTRACT_VERSION,
        "manifest_fixture_sha256": manifest_sha256(manifest),
        "baseline_git_sha": _git_sha(),
        "market_date": D,
        "knowledge_cutoff_at": K,
        "migration_version": manifest["migration_version"],
        "environment": environment_metadata(),
        "network_disabled": True,
        "production_database_disabled": True,
        "sample_method": {
            "cold": "new Python subprocess per sample and query-only SQLite transaction per page",
            "warm": "same Python process and service instance with query-only SQLite transaction per page",
            "sample_count_per_mode_per_size": runs,
            "percentile": "nearest_rank",
        },
        "thresholds_seconds": manifest["thresholds_seconds"],
        "query_budget": manifest["query_budget"],
        "queue_sizes": list(sizes),
        "runs_per_mode": runs,
        "summaries": summaries,
    }


def evaluate_gate(result: dict[str, Any]) -> dict[str, Any]:
    thresholds = result["thresholds_seconds"]
    checks: dict[str, Any] = {}
    passed = True
    first_counts: set[int] = set()
    for size, summary in result["summaries"].items():
        size_checks = {
            "warm_p95": summary["warm"]["p95_seconds"] <= float(thresholds["warm_p95"]),
            "warm_p99": summary["warm"]["p99_seconds"] <= float(thresholds["warm_p99"]),
            "cold_p95": summary["cold"]["p95_seconds"] <= float(thresholds["cold_p95"]),
            "cold_p99": summary["cold"]["p99_seconds"] <= float(thresholds["cold_p99"]),
            "bounded_page_rows": summary["max_page_rows"] <= PAGE_LIMIT,
            "bounded_page_queries": summary["max_page_query_count"] <= MAX_PAGE_READ_STATEMENTS,
            "first_page_query_count_stable": len(summary["first_page_query_counts"]) == 1,
            "continuation_page_query_count_bounded": all(
                count <= MAX_PAGE_READ_STATEMENTS
                for count in summary["continuation_page_query_counts"]
            ),
            "sample_count": summary["sample_count"] == result["runs_per_mode"] * 2,
        }
        first_counts.update(summary["first_page_query_counts"])
        checks[size] = size_checks
        passed = passed and all(size_checks.values())
    checks["cross_size_first_page_query_count_stable"] = len(first_counts) == 1
    passed = passed and checks["cross_size_first_page_query_count_stable"]
    return {"status": "pass" if passed else "hold", "checks": checks}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", default="tests/fixtures/phase17_daily_research_benchmark_v1.json"
    )
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--sizes", default=None, help="comma-separated queue sizes")
    parser.add_argument("--runs", type=int, default=SAMPLE_COUNT)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--database", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("first", "all"), default="first", help=argparse.SUPPRESS)
    parser.add_argument("--enforce-gate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.worker:
        if not args.database:
            raise SystemExit("worker requires --database")
        reader = TracingDailyResearchReadRepository(args.database)
        service = DailyResearchReviewContextService(
            args.database, read_repository=reader
        )
        sample = run_request(service, reader, mode=args.mode)
        sample.update({"process_pid": os.getpid(), "process_isolated": True})
        print(json.dumps(sample, ensure_ascii=False, sort_keys=True))
        return 0
    manifest = load_manifest(args.manifest)
    sizes = tuple(
        int(value.strip()) for value in args.sizes.split(",")
    ) if args.sizes else QUEUE_SIZES
    default_root = Path.cwd() / ".tmp_phase17_daily_research_benchmark"
    work_dir = Path(args.work_dir) if args.work_dir else default_root
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_benchmark(
            manifest,
            sizes=sizes,
            runs=args.runs,
            work_dir=work_dir,
        )
        result["acceptance_gate"] = evaluate_gate(result)
        result["manifest_path"] = str(Path(args.manifest).resolve())
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        if args.output:
            Path(args.output).write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0 if result["acceptance_gate"]["status"] == "pass" or not args.enforce_gate else 1
    finally:
        if work_dir == default_root and work_dir.exists():
            try:
                shutil.rmtree(work_dir)
            except PermissionError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
