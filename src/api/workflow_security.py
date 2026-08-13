"""Local-only security boundary for Phase 12 research workflow routes."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit


RESEARCH_PREFIX = "/api/v2/research/"
CSRF_COOKIE_NAME = "research_csrf_session"
CSRF_TTL_SECONDS = 1800
MAX_CSRF_SESSIONS = 128
MAX_RESEARCH_BODY_BYTES = 16 * 1024


@dataclass(frozen=True)
class ParsedOrigin:
    origin: str
    authority: str


def parse_research_origin(value: str) -> ParsedOrigin:
    raw = value.strip()
    if not raw or "*" in raw:
        raise ValueError("research origin is required and cannot contain wildcards")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("research origin scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("research origin cannot contain userinfo")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("research origin must not contain path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("research origin port is invalid") from exc
    if parsed.hostname is None or port is None:
        raise ValueError("research origin requires host and explicit port")
    hostname = parsed.hostname.lower()
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname == "localhost"
    if not loopback:
        raise ValueError("research origin host must be loopback")
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{display_host}:{port}"
    return ParsedOrigin(f"{parsed.scheme.lower()}://{authority}", authority)


@dataclass(frozen=True)
class ResearchSecurityConfig:
    valid: bool
    allowed_origins: frozenset[str]
    allowed_authorities: frozenset[str]

    @classmethod
    def from_environment(cls) -> "ResearchSecurityConfig":
        try:
            origins = [parse_research_origin(os.getenv("RESEARCH_APPLICATION_ORIGIN", ""))]
            raw_dev = os.getenv("RESEARCH_ALLOWED_DEV_ORIGINS", "")
            if raw_dev.strip():
                entries = raw_dev.split(",")
                if any(not entry.strip() for entry in entries):
                    raise ValueError("development origin entry is empty")
                origins.extend(parse_research_origin(entry) for entry in entries)
            return cls(
                True,
                frozenset(item.origin for item in origins),
                frozenset(item.authority for item in origins),
            )
        except ValueError:
            return cls(False, frozenset(), frozenset())


class CsrfSessionStore:
    def __init__(self):
        self._sessions: dict[str, tuple[str, datetime, datetime]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, received_at: datetime) -> tuple[str, str, datetime]:
        with self._lock:
            self._sessions = {
                key: value for key, value in self._sessions.items()
                if received_at < value[1]
            }
            if len(self._sessions) >= MAX_CSRF_SESSIONS:
                raise RuntimeError("csrf_session_capacity_exceeded")
            session_id = secrets.token_urlsafe(32)
            token = secrets.token_urlsafe(32)
            expires_at = received_at + timedelta(seconds=CSRF_TTL_SECONDS)
            self._sessions[session_id] = (self._digest(token), expires_at, received_at)
            return session_id, token, expires_at

    def validate(self, session_id: str, token: str, received_at: datetime) -> str | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return "csrf_session_invalid"
            digest, expires_at, _ = session
            if received_at >= expires_at:
                self._sessions.pop(session_id, None)
                return "csrf_session_expired"
            if not hmac.compare_digest(digest, self._digest(token)):
                return "csrf_session_invalid"
            return None


def _json_response(status: int, detail: str) -> tuple[dict[str, Any], bytes]:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    return {
        "type": "http.response.start", "status": status,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii"))],
    }, body


class ResearchBoundaryMiddleware:
    def __init__(
        self,
        app,
        config: ResearchSecurityConfig | None = None,
        sessions: CsrfSessionStore | None = None,
    ):
        self.app = app
        self.config = config or ResearchSecurityConfig.from_environment()
        self.sessions = sessions or CsrfSessionStore()

    async def _reject(self, send, status: int, detail: str) -> None:
        start, body = _json_response(status, detail)
        await send(start)
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _header_values(scope, name: bytes) -> list[str]:
        return [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(RESEARCH_PREFIX):
            await self.app(scope, receive, send)
            return
        received_at = datetime.now(timezone.utc)
        state = scope.setdefault("state", {})
        state["research_request_received_at"] = received_at
        state["research_csrf_sessions"] = self.sessions
        if not self.config.valid:
            await self._reject(send, 503, "research_security_configuration_invalid")
            return
        client = scope.get("client")
        try:
            is_loopback = bool(client and ipaddress.ip_address(client[0]).is_loopback)
        except ValueError:
            is_loopback = False
        if not is_loopback:
            await self._reject(send, 403, "research_client_not_loopback")
            return
        hosts = self._header_values(scope, b"host")
        if len(hosts) != 1 or hosts[0].lower() not in self.config.allowed_authorities:
            await self._reject(send, 403, "research_host_not_allowed")
            return
        origins = self._header_values(scope, b"origin")
        if len(origins) > 1:
            await self._reject(send, 403, "research_origin_not_allowed")
            return
        origin = origins[0] if origins else None
        if origin is not None:
            try:
                canonical_origin = parse_research_origin(origin).origin
            except ValueError:
                canonical_origin = ""
            if canonical_origin not in self.config.allowed_origins:
                await self._reject(send, 403, "research_origin_not_allowed")
                return
        method = scope.get("method", "GET").upper()
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return
        if origin is None:
            await self._reject(send, 403, "research_origin_required")
            return
        if os.getenv("RESEARCH_WORKFLOW_WRITES_ENABLED", "false").strip().lower() != "true":
            await self._reject(send, 503, "research_workflow_writes_disabled")
            return
        content_types = self._header_values(scope, b"content-type")
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            await self._reject(send, 415, "research_json_content_type_required")
            return
        content_lengths = self._header_values(scope, b"content-length")
        try:
            if content_lengths and int(content_lengths[0]) > MAX_RESEARCH_BODY_BYTES:
                raise OverflowError
        except (ValueError, OverflowError):
            await self._reject(send, 413, "research_request_body_too_large")
            return
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > MAX_RESEARCH_BODY_BYTES:
                await self._reject(send, 413, "research_request_body_too_large")
                return
            more = message.get("more_body", False)
        cookie = SimpleCookie()
        cookie_headers = self._header_values(scope, b"cookie")
        if cookie_headers:
            cookie.load("; ".join(cookie_headers))
        session_id = cookie.get(CSRF_COOKIE_NAME)
        tokens = self._header_values(scope, b"x-csrf-token")
        reason = self.sessions.validate(
            session_id.value if session_id else "", tokens[0] if len(tokens) == 1 else "",
            received_at,
        )
        if reason:
            await self._reject(send, 403, reason)
            return
        replayed = False
        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        await self.app(scope, replay_receive, send)
