"""Tests for Phase 19 centralized egress transport and security client."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.collectors.installed_egress_client import (
    APPROVED_ENDPOINT_PREFIXES,
    DeadlineExhaustedError,
    EgressHttpError,
    EgressSecurityError,
    EndpointNotAllowlistedError,
    InstalledEgressClient,
    PayloadTooLargeError,
    redact_text,
    validate_egress_url,
)


def test_approved_urls_pass_validation() -> None:
    for prefix in APPROVED_ENDPOINT_PREFIXES:
        validated = validate_egress_url(prefix)
        assert validated == prefix


def test_unapproved_domain_rejected() -> None:
    with pytest.raises(EndpointNotAllowlistedError, match="Host not in egress allowlist"):
        validate_egress_url("https://malicious.com/api/data")

    with pytest.raises(EndpointNotAllowlistedError, match="Host not in egress allowlist"):
        validate_egress_url("https://yahoo.com/finance")


def test_insecure_scheme_rejected() -> None:
    with pytest.raises(EndpointNotAllowlistedError, match="Insecure scheme"):
        validate_egress_url("http://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule")


def test_unapproved_path_on_approved_domain_rejected() -> None:
    with pytest.raises(EndpointNotAllowlistedError, match="Endpoint prefix not in allowlist"):
        validate_egress_url("https://www.twse.com.tw/unapproved/secret/endpoint")


def test_redact_proxy_credentials() -> None:
    raw = "Failed to connect via http://myuser:secretpassword@proxy.corp.internal:8080"
    sanitized = redact_text(raw)
    assert "secretpassword" not in sanitized
    assert "myuser" not in sanitized
    assert "***:***" in sanitized


def test_payload_exceeding_15mb_raises() -> None:
    client = InstalledEgressClient()
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    # Generate chunks totaling > 15MB
    large_chunk = b"X" * (1024 * 1024)  # 1MB
    mock_response.iter_content.return_value = [large_chunk] * 16
    mock_session.get.return_value = mock_response
    client._session = mock_session

    with pytest.raises(PayloadTooLargeError, match="Payload exceeded"):
        client.fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")


def test_redirect_rejected() -> None:
    client = InstalledEgressClient()
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 302
    mock_session.get.return_value = mock_response
    client._session = mock_session

    with pytest.raises(EgressSecurityError, match="Unexpected redirect"):
        client.fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")


def test_deadline_exhausted_before_fetch() -> None:
    client = InstalledEgressClient()
    # Deadline only 2 seconds in future (< 5s min budget)
    tight_deadline = time.monotonic() + 2.0

    with pytest.raises(DeadlineExhaustedError, match="Deadline budget exhausted"):
        client.fetch(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            deadline_monotonic=tight_deadline,
        )


def test_isin_endpoint_max_retries_is_one() -> None:
    client = InstalledEgressClient()
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.ConnectionError("network down")
    client._session = mock_session

    with pytest.raises(EgressHttpError, match="failed after 2 attempts"):
        client.fetch("https://isin.twse.com.tw/isin/single_main.jsp?owncode=2330")

    # 1 initial + 1 retry = 2 attempts total
    assert mock_session.get.call_count == 2


def test_general_endpoint_max_retries_is_two() -> None:
    client = InstalledEgressClient()
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.ConnectionError("network down")
    client._session = mock_session

    with pytest.raises(EgressHttpError, match="failed after 3 attempts"):
        client.fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")

    # 1 initial + 2 retries = 3 attempts total
    assert mock_session.get.call_count == 3
