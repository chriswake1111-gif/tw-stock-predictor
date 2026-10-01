"""No database access: every research action uses the existing local API."""
from __future__ import annotations

import http.cookiejar
import ipaddress
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from src.runtime.instance import read_descriptor, validate_process_ownership

CONTRACT = "tw_stock_research_assistant_v1"
TERMINAL = {"succeeded", "partial", "failed", "cancelled", "interrupted"}
SYMBOL = re.compile(r"[0-9A-Z]{4,8}\.(?:TW|TWO)\Z")
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_FOLDER_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_FILES = 512


class AssistantError(Exception):
    def __init__(self, code, *, status=None):
        super().__init__(code)
        self.code, self.status = code, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AssistantError("redirect_refused")


def local_origin(value):
    try:
        p = urllib.parse.urlsplit(value)
        if (p.scheme != "http" or not ipaddress.ip_address(p.hostname).is_loopback
                or not p.port or p.username or p.password or p.path or p.query or p.fragment):
            raise ValueError()
    except (TypeError, ValueError):
        raise AssistantError("instance_origin_invalid") from None
    return value


def symbol_path(symbol):
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise AssistantError("invalid_canonical_symbol")
    return urllib.parse.quote(symbol, safe="")


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise AssistantError("invalid_operation_id")
    return value


def request_key(value):
    try:
        if str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AssistantError("request_id_must_be_uuid") from None
    return value


def atomic_json(path, payload):
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise AssistantError("receipt_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = [p for p in path.parent.glob("*.json") if p != path]
    if (len(existing) >= MAX_RECEIPT_FILES
            or sum(p.stat().st_size for p in existing) + len(encoded) > MAX_RECEIPT_FOLDER_BYTES):
        raise AssistantError("receipt_storage_full_cleanup_required")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalClient:
    def __init__(self, install_root: Path, user_root: Path, *, clock=time.monotonic,
                 sleep=time.sleep, ownership=validate_process_ownership):
        self.install_root, self.user_root = install_root.resolve(), user_root.resolve()
        self.clock, self.sleep, self.ownership = clock, sleep, ownership
        self.origin = None
        self.descriptor = None
        self.token = None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @property
    def assistant_dir(self):
        return self.user_root / "runtime" / "research-assistant"

    def _http(self, path, *, body=None, key=None, timeout=10, retry_read=True):
        if not self.origin or not path.startswith("/api/") or ".." in path or "\\" in path:
            raise AssistantError("invalid_local_request")
        headers = {"Accept": "application/json", "Origin": self.origin}
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": self.token or ""})
        if key:
            headers["Idempotency-Key"] = key
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None
        attempts = 2 if body is None and retry_read else 1
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(self.origin + path, data=data, headers=headers)
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise AssistantError("response_too_large")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise AssistantError("response_contract_invalid")
                    return result
            except urllib.error.HTTPError as exc:
                if exc.code in {502, 503, 504} and attempt + 1 < attempts:
                    continue
                # Do not leak raw server errors, cookies, or third-party bodies.
                detail = "local_api_request_failed"
                try:
                    reason = json.loads(exc.read(16384)).get("detail")
                    if reason in {"research_content_changed_review_again", "research_idempotency_conflict"}:
                        detail = reason
                except (ValueError, AttributeError):
                    pass
                raise AssistantError(detail, status=exc.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if attempt + 1 == attempts:
                    raise AssistantError("local_connection_failed" if body is None else "write_response_unknown_retry_same_request") from None
            except (ValueError, UnicodeError):
                raise AssistantError("response_contract_invalid") from None

    def connect(self, *, start=False):
        manifest_path = self.install_root / "tw-stock-predictor" / "_internal" / "package-manifest.json"
        launcher = self.install_root / "tw-stock-predictor" / "tw-stock-predictor.exe"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_build = manifest["build_sha"]
            if not launcher.is_file() or not isinstance(expected_build, str) or not expected_build:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise AssistantError("installation_missing_or_invalid") from None
        path = self.user_root / "runtime" / "instance.json"
        if not path.exists():
            if not start:
                raise AssistantError("application_not_running")
            if os.name != "nt":
                raise AssistantError("windows_required")
            subprocess.Popen([str(launcher), "--user-root", str(self.user_root)],
                             cwd=str(launcher.parent), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = self.clock() + 60
            while not path.exists() and self.clock() < deadline:
                self.sleep(.25)
            if not path.exists():
                raise AssistantError("application_start_timeout")
        try:
            descriptor = read_descriptor(path)
        except Exception:
            raise AssistantError("instance_descriptor_invalid") from None
        valid, reason = self.ownership(descriptor, expected_build_sha=expected_build)
        if not valid:
            raise AssistantError(reason)
        self.origin = local_origin(descriptor.get("origin"))
        parsed = urllib.parse.urlsplit(self.origin)
        if descriptor.get("host") != parsed.hostname or descriptor.get("port") != parsed.port:
            raise AssistantError("instance_endpoint_mismatch")
        self.descriptor = descriptor
        ready = self._http("/api/ready")
        if (not ready.get("ready") or ready.get("origin") != self.origin
                or ready.get("build_sha") != expected_build):
            raise AssistantError("instance_not_ready_or_mismatched")
        if ready.get("research_assistant_contract") != CONTRACT:
            raise AssistantError("assistant_upgrade_required")
        return {"contract_version": CONTRACT, "status": "ready", "origin": self.origin,
                "build_sha": expected_build,
                "valuation_pairing_policy": ready.get("valuation_pairing_policy"),
                "research_guidance_contract": ready.get("research_guidance_contract"),
                "earnings_research_contract": ready.get("earnings_research_contract")}

    def evidence(self, symbol, payload=None, key=None, *, history=False, before=None):
        if self._http("/api/ready").get("research_guidance_contract") != "research_guidance_v1":
            raise AssistantError("research_guidance_upgrade_required")
        path = f"/api/v2/research/evidence/{symbol_path(symbol)}"
        if payload is not None:
            request_key(key)
            return self.mutate(path, payload, key)
        query = urllib.parse.urlencode({"history": str(history).lower(), **({"before": before} if before else {})})
        return self._http(path + "?" + query)

    def evidence_reuse(self, symbol, scope, year=None, *, force=False, new_information=False):
        if self._http("/api/ready").get("research_guidance_contract") != "research_guidance_v1":
            raise AssistantError("research_guidance_upgrade_required")
        query = {"scope":scope, "force":str(force).lower(), "new_information":str(new_information).lower()}
        if year is not None:
            query["fiscal_year"] = year
        return self._http(f"/api/v2/research/evidence/{symbol_path(symbol)}/reuse?" + urllib.parse.urlencode(query))

    def evidence_candidate(self, symbol, record_id):
        if self._http("/api/ready").get("research_guidance_contract") != "research_guidance_v1":
            raise AssistantError("research_guidance_upgrade_required")
        return self._http(f"/api/v2/research/evidence/{symbol_path(symbol)}/{identifier(record_id)}/candidate")

    def wave_candidates(self, symbol, candidate_id=None):
        if self._http("/api/ready").get("wave_candidates_contract") != "wave_candidates_v1":
            raise AssistantError("wave_candidates_upgrade_required")
        path = f"/api/v2/research/wave-candidates/{symbol_path(symbol)}"
        if candidate_id is not None:
            path += "/"+identifier(candidate_id)
        return self._http(path)

    def mutate(self, path, body, key=None):
        self.token = self._http("/api/v2/data-operations/csrf-token")["csrf_token"]
        return self._http(path, body=body, key=key)

    def search(self, query):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
            raise AssistantError("invalid_search_query")
        return self._http("/api/v2/universe/search?" + urllib.parse.urlencode({"q":query.strip(), "limit":50}))

    def resolve(self, query):
        found = self.search(query)
        rows = found.get("results", [])
        exact = [r for r in rows if query.strip().upper() in
                 {str(r.get("canonical_symbol", "")).upper(), str(r.get("official_code", ""))}]
        candidates = exact or rows
        if len(candidates) != 1 or (not exact and found.get("total_matches", len(rows)) != 1):
            return None, {"status": "needs_selection" if candidates else "not_found", "search": found}
        row = candidates[0]
        symbol = row.get("canonical_symbol", "")
        symbol_path(symbol)
        if row.get("security_type") not in {"股票", "普通股"} or not re.fullmatch(r"[1-9][0-9]{3}", row.get("official_code", "")):
            return None, {"status":"unsupported_instrument", "instrument":row}
        return symbol, row

    def review(self, symbol, year=None):
        if year is not None and (type(year) is not int or not 1900 <= year <= 2200):
            raise AssistantError("invalid_research_year")
        query = "?research_year=" + str(year) if year is not None else ""
        result = self._http(f"/api/v2/research/journal/{symbol_path(symbol)}/preview" + query)
        if result.get("contract_version") != CONTRACT or result.get("symbol") != symbol:
            raise AssistantError("response_contract_invalid")
        return result

    def operation(self, op):
        return self._http("/api/v2/data-operations/operations/" + identifier(op))

    def active_operation(self):
        return self._http("/api/v2/data-operations/status").get("active_operation")

    def _remember_operation(self, result, symbol):
        if result.get("operation_created") is True and result.get("operation_id"):
            op = identifier(result["operation_id"])
            try:
                atomic_json(self.assistant_dir / "operations" / f"{op}.json",
                            {"operation_id": op, "symbol": symbol, "launch_id": self.descriptor["launch_id"]})
            except (AssistantError, OSError):
                result["cancel_unavailable_reason"] = "operation_receipt_not_saved"

    def update(self, symbol, *, refresh=False):
        symbol_path(symbol)
        result = self.mutate("/api/v2/research/bootstrap", {"canonical_symbol":symbol, "refresh":refresh})
        self._remember_operation(result, symbol)
        return result

    def wait(self, op, *, seconds=120):
        identifier(op)
        if not 0 <= seconds <= 120:
            raise AssistantError("wait_out_of_range")
        deadline = self.clock() + seconds
        result = {"operation_id": op, "status":"pending"}
        while self.clock() < deadline:
            # No read retry here: the total poll budget includes network time.
            result = self._http("/api/v2/data-operations/operations/" + op,
                                timeout=max(.01, min(10, deadline-self.clock())), retry_read=False)
            if result.get("status") in TERMINAL:
                return result
            self.sleep(min(2, max(0, deadline-self.clock())))
        return {**result, "wait_status":"timeout", "operation_continues":True}

    def research(self, query, *, seconds=120):
        symbol, selection = self.resolve(query)
        if symbol is None:
            return selection
        cached = self.review(symbol)
        operation = None
        try:
            started = self.clock()
            operation = self.update(symbol)
            if operation.get("operation_id"):
                waiting_for_other = operation.get("status") == "waiting_for_data_operation"
                operation = self.wait(operation["operation_id"], seconds=seconds)
                if waiting_for_other and operation.get("status") in TERMINAL and self.clock()-started < seconds:
                    operation = self.update(symbol)
                    if operation.get("operation_id"):
                        operation = self.wait(operation["operation_id"], seconds=max(0, seconds-(self.clock()-started)))
            current = self.review(symbol)
        except AssistantError as exc:
            current = cached
            operation = {"status":"unavailable", "reason":exc.code, "last_operation":operation}
        return {"status":"review_ready", "symbol":symbol, "instrument":selection,
                "update":operation, "review":current}

    def cancel(self, op):
        identifier(op)
        try:
            owned = json.loads((self.assistant_dir / "operations" / f"{op}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AssistantError("operation_not_created_by_assistant") from None
        if owned.get("operation_id") != op or owned.get("launch_id") != self.descriptor["launch_id"]:
            raise AssistantError("operation_instance_changed")
        current = self.operation(op)
        if current.get("status") in TERMINAL:
            return current
        if current.get("target_symbols") != [owned.get("symbol")]:
            raise AssistantError("operation_ownership_mismatch")
        return self.mutate("/api/v2/data-operations/cancel", {"expected_operation_id":op})

    def assumption(self, symbol, kind, payload, *, draft=False, key=None):
        if kind not in {"eps", "pe", "anchor"}:
            raise AssistantError("unsupported_assumption_kind")
        if kind == "pe":
            if self._http("/api/ready").get("valuation_pairing_policy") != "same_fiscal_year_v1":
                raise AssistantError("pe_fiscal_year_upgrade_required")
        if draft:
            request_key(key)
        return self.mutate(f"/api/v2/research/assumptions/{symbol_path(symbol)}/{kind}/" + ("draft" if draft else "preview"), payload, key)

    def save(self, reviewed, note, key):
        if reviewed.get("contract_version") != CONTRACT or not re.fullmatch(r"[a-f0-9]{64}", reviewed.get("content_fingerprint", "")):
            raise AssistantError("invalid_review_receipt")
        request_key(key)
        if len(note) > 4000:
            raise AssistantError("invalid_save_request")
        context = ({"include_research_context": True, "research_year": reviewed["guidance"].get("selected_year")}
                   if reviewed.get("guidance", {}).get("contract_version") == "research_guidance_v1" else {})
        return self.mutate(f"/api/v2/research/journal/{symbol_path(reviewed['symbol'])}",
                           {"knowledge_cutoff_at":reviewed["knowledge_cutoff_at"], "note":note,
                            "expected_content_fingerprint":reviewed["content_fingerprint"], **context}, key)

    def page_url(self, symbol, *, assumptions=False):
        return f"{self.origin}/stocks/{symbol_path(symbol)}" + ("#local-assumptions" if assumptions else "")
