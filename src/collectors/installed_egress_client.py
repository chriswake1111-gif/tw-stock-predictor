"""Centralized egress transport and security client for Phase 19 installed operations.

Enforces:
1. Strict 9-endpoint / approved domain allowlist.
2. No redirects (allow_redirects=False).
3. TLS 1.2+ certificate verification (verify=True).
4. Proxy/credential redaction in error messages and logs.
5. 5.0s connect + 10.0s read timeouts.
6. Max 2 retries for general endpoints; max 1 retry for ISIN classification.
7. 15MB payload cap with stream chunk validation.
8. Deterministic deadline budget defense (< 5.0s remaining aborts attempt).
"""

from __future__ import annotations

import re
import time
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

MAX_PAYLOAD_BYTES = 15 * 1024 * 1024  # 15MB
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
ISIN_MAX_RETRIES = 1
MIN_REMAINING_BUDGET_SECONDS = 5.0

APPROVED_DOMAINS = frozenset({
    "twse.com.tw",
    "www.twse.com.tw",
    "openapi.twse.com.tw",
    "isin.twse.com.tw",
    "tpex.org.tw",
    "www.tpex.org.tw",
    "cbc.gov.tw",
    "www.cbc.gov.tw",
    "cpx.cbc.gov.tw",
})

APPROVED_ENDPOINT_PREFIXES = (
    "https://www.twse.com.tw/rwd/zh/holidaySchedule/",
    "https://openapi.twse.com.tw/v1/holidaySchedule",
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "https://www.twse.com.tw/rwd/zh/companyInfo/t187ap03_L",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "https://www.tpex.org.tw/web/regular_emerging/corporateInfo/regular/mopsfin_t187ap03_O.php",
    "https://isin.twse.com.tw/isin/single_main.jsp",
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL",
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",
    "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
    "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
    "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index",
    "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_index/st41_result.php",
    "https://cpx.cbc.gov.tw/API/DataAPI/Get",
    "https://www.cbc.gov.tw/public/data/OpenData/M1B.csv",
)

_CREDENTIAL_PATTERN = re.compile(r"(?<=://)[^/]+:[^/]+(?=@)")


class EgressSecurityError(Exception):
    """Base exception for egress security violations."""


class EndpointNotAllowlistedError(EgressSecurityError):
    """Raised when an outbound URL is not in the approved endpoint allowlist."""


class PayloadTooLargeError(EgressSecurityError):
    """Raised when downloaded response exceeds 15MB cap."""


class DeadlineExhaustedError(EgressSecurityError):
    """Raised when deadline budget has insufficient remaining time."""


class EgressHttpError(EgressSecurityError):
    """Raised on network or server errors during approved egress."""


def redact_text(value: str) -> str:
    """Sanitize proxy credentials or sensitive tokens from error strings."""
    return _CREDENTIAL_PATTERN.sub("***:***", str(value))


def validate_egress_url(url: str) -> str:
    """Validate that the URL strictly matches the approved domain and endpoint allowlist."""
    cleaned = str(url).strip()
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() != "https":
        raise EndpointNotAllowlistedError(f"Insecure scheme not allowed: {parsed.scheme}")
    
    host = (parsed.hostname or "").lower()
    if host not in APPROVED_DOMAINS:
        raise EndpointNotAllowlistedError(f"Host not in egress allowlist: {host}")
    
    if not any(cleaned.startswith(prefix) for prefix in APPROVED_ENDPOINT_PREFIXES):
        raise EndpointNotAllowlistedError(f"Endpoint prefix not in allowlist: {cleaned}")
    
    return cleaned


class InstalledEgressClient:
    """Hardened HTTP client for fetching approved Layer 1 market data."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def is_isin_endpoint(self, url: str) -> bool:
        return "isin.twse.com.tw" in url.lower()

    def fetch(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        deadline_monotonic: float | None = None,
        max_retries: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Fetch URL with allowlist validation, timeout, chunked size check, and retries.

        Returns: (http_status, content_bytes, response_headers)
        """
        validated_url = validate_egress_url(url)
        
        if max_retries is None:
            max_retries = (
                ISIN_MAX_RETRIES if self.is_isin_endpoint(validated_url) else DEFAULT_MAX_RETRIES
            )

        attempts = max_retries + 1
        last_exception: Exception | None = None

        for attempt in range(1, attempts + 1):
            if deadline_monotonic is not None:
                remaining = deadline_monotonic - time.monotonic()
                if remaining < MIN_REMAINING_BUDGET_SECONDS:
                    raise DeadlineExhaustedError(
                        f"Deadline budget exhausted ({remaining:.2f}s remaining < {MIN_REMAINING_BUDGET_SECONDS}s)"
                    )

            try:
                response = self._session.get(
                    validated_url,
                    params=params,
                    headers=headers,
                    allow_redirects=False,
                    verify=True,
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                    stream=True,
                )

                if 300 <= response.status_code < 400:
                    raise EgressSecurityError(
                        f"Unexpected redirect response (status {response.status_code}) rejected"
                    )

                # Stream and enforce 15MB payload cap
                content_chunks: list[bytes] = []
                total_bytes = 0
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        total_bytes += len(chunk)
                        if total_bytes > MAX_PAYLOAD_BYTES:
                            raise PayloadTooLargeError(
                                f"Payload exceeded {MAX_PAYLOAD_BYTES} bytes limit"
                            )
                        content_chunks.append(chunk)

                body = b"".join(content_chunks)
                res_headers = {k: v for k, v in response.headers.items()}
                return response.status_code, body, res_headers

            except (PayloadTooLargeError, EndpointNotAllowlistedError, EgressSecurityError, DeadlineExhaustedError):
                raise
            except Exception as exc:
                last_exception = exc
                if attempt < attempts:
                    time.sleep(0.2 * attempt)
                    continue

        sanitized_error = redact_text(str(last_exception))
        raise EgressHttpError(f"Egress request failed after {attempts} attempts: {sanitized_error}") from last_exception
