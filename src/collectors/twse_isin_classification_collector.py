"""Exact Traditional ISIN product-classification evidence collector."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote

import requests

from src.domain.data_foundation import canonical_json, sha256_text
from src.domain.eod_close import classify_product, parse_iso_date, parse_twse_roc_date


TWSE_ISIN_CLASSIFICATION_RESOURCE_ID = "twse.isin.security_classification"
TWSE_ISIN_CLASSIFICATION_URL = (
    "https://isin.twse.com.tw/isin/single_main.jsp?owncode={official_code}&stockname="
)

_LABELS = {
    "official_code": frozenset({"Security Code", "證券代號", "有價證券代號"}),
    "market": frozenset({"Market", "市場別"}),
    "security_type": frozenset({"Type of security", "有價證券別"}),
    "listing_date": frozenset({"Date Stock Listed", "上市日期", "公開發行/上市(櫃)/發行日"}),
    "isin": frozenset({"ISIN Code", "國際證券辨識號碼", "國際證券編碼"}),
    "cfi": frozenset({"CFI", "CFI Code", "CFICode"}),
    "remarks": frozenset({"Remarks", "備註", "備 註"}),
    "currency": frozenset({"Currency", "幣別", "計價幣別"}),
}
_ALL_LABELS = frozenset(value for values in _LABELS.values() for value in values)


def _clean(value: str) -> str:
    return " ".join(str(value).replace("\xa0", " ").split())


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif lowered == "tr" and self._row is not None:
            if self._cell is not None:
                self._row.append(_clean("".join(self._cell)))
            if any(self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _label_for(value: str) -> str | None:
    candidate = _clean(value)
    for field, labels in _LABELS.items():
        if candidate in labels:
            return field
    return None


def _candidate_maps(rows: list[list[str]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    aggregate: dict[str, str] = {}
    for index, row in enumerate(rows):
        mapping: dict[str, str] = {}
        for cell_index, cell in enumerate(row[:-1]):
            label = _label_for(cell)
            if label and row[cell_index + 1]:
                mapping[label] = row[cell_index + 1]
        if mapping:
            candidates.append(mapping)
            aggregate.update(mapping)

        header_indices = {
            cell_index: _label_for(cell)
            for cell_index, cell in enumerate(row)
            if _label_for(cell)
        }
        if len(header_indices) >= 2:
            for following in rows[index + 1 : index + 3]:
                if len(following) < len(row):
                    continue
                header_mapping = {
                    label: following[cell_index]
                    for cell_index, label in header_indices.items()
                    if following[cell_index]
                }
                if header_mapping:
                    candidates.append(header_mapping)
                    aggregate.update(header_mapping)
    if aggregate:
        candidates.append(aggregate)
    return candidates


def _parse_listing_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    try:
        if "/" in text:
            parts = text.split("/")
            if len(parts) == 3 and len(parts[0]) == 4 and parts[0].isdigit():
                return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        if len(text) >= 3 and text[:3].isdigit() and (
            "/" in text or len(text) == 7
        ):
            return parse_twse_roc_date(text)
        return parse_iso_date(text, "listing_date")
    except ValueError:
        return None


@dataclass(frozen=True)
class ParsedClassificationEvidence:
    official_code: str
    state: str
    decision: str
    reason: str | None
    official_code_raw: str | None
    market_raw: str | None
    security_type_raw: str | None
    listing_date: str | None
    isin_raw: str | None
    cfi_raw: str | None
    currency_raw: str | None
    remarks_raw: str | None
    raw_cells: tuple[tuple[str, ...], ...]
    schema_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "official_code": self.official_code,
            "state": self.state,
            "decision": self.decision,
            "reason": self.reason,
            "official_code_raw": self.official_code_raw,
            "market_raw": self.market_raw,
            "security_type_raw": self.security_type_raw,
            "listing_date": self.listing_date,
            "isin_raw": self.isin_raw,
            "cfi_raw": self.cfi_raw,
            "currency_raw": self.currency_raw,
            "remarks_raw": self.remarks_raw,
            "raw_cells": [list(row) for row in self.raw_cells],
            "schema_fingerprint": self.schema_fingerprint,
        }


def parse_twse_isin_classification(
    html: str, *, official_code: str, expected_market: str | None = None,
    trade_date: str | None = None,
) -> ParsedClassificationEvidence:
    """Parse only the exact Traditional labels on the approved HTML surface."""

    if not isinstance(html, str):
        raise ValueError("classification HTML must be text")
    requested_code = str(official_code).strip()
    if not requested_code:
        raise ValueError("official_code is required")
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    candidates = _candidate_maps(parser.rows)
    complete_candidates = [
        item for item in candidates
        if item.get("official_code") == requested_code
        and item.get("market")
        and item.get("security_type")
    ]
    candidate = complete_candidates[0] if complete_candidates else None
    schema_hash = sha256_text(canonical_json(sorted(_ALL_LABELS)))
    raw_cells = tuple(tuple(row) for row in parser.rows)
    if candidate is None:
        return ParsedClassificationEvidence(
            official_code=requested_code,
            state="schema_changed",
            decision="needs_human_input",
            reason="classification_evidence_missing",
            official_code_raw=None,
            market_raw=None,
            security_type_raw=None,
            listing_date=None,
            isin_raw=None,
            cfi_raw=None,
            currency_raw=None,
            remarks_raw=None,
            raw_cells=raw_cells,
            schema_fingerprint=schema_hash,
        )

    exact_candidates = complete_candidates
    signatures = {
        (
            item.get("market"), item.get("security_type"),
            item.get("listing_date"), item.get("currency"),
            item.get("cfi"), item.get("remarks"),
        )
        for item in exact_candidates
    }
    if len(signatures) > 1:
        return ParsedClassificationEvidence(
            official_code=requested_code,
            state="ambiguous",
            decision="needs_human_input",
            reason="classification_evidence_missing",
            official_code_raw=requested_code,
            market_raw=None,
            security_type_raw=None,
            listing_date=None,
            isin_raw=None,
            cfi_raw=None,
            currency_raw=None,
            remarks_raw=None,
            raw_cells=raw_cells,
            schema_fingerprint=schema_hash,
        )

    listing_date = _parse_listing_date(candidate.get("listing_date"))
    if candidate.get("listing_date") and listing_date is None:
        return ParsedClassificationEvidence(
            official_code=requested_code,
            state="schema_changed",
            decision="needs_human_input",
            reason="classification_evidence_missing",
            official_code_raw=candidate.get("official_code"),
            market_raw=candidate.get("market"),
            security_type_raw=candidate.get("security_type"),
            listing_date=None,
            isin_raw=candidate.get("isin"),
            cfi_raw=candidate.get("cfi"),
            currency_raw=candidate.get("currency"),
            remarks_raw=candidate.get("remarks"),
            raw_cells=raw_cells,
            schema_fingerprint=schema_hash,
        )

    market = candidate.get("market")
    security_type = candidate.get("security_type")
    currency = candidate.get("currency")
    if currency is None and expected_market in {"上市", "上櫃"} and security_type == "股票":
        currency = "新台幣"
    if expected_market:
        decision = classify_product(
            official_code=candidate.get("official_code"),
            requested_code=requested_code,
            market=market,
            expected_market=expected_market,
            security_type=security_type,
            listing_date=listing_date,
            trade_date=trade_date,
            currency=currency,
            cfi=candidate.get("cfi"),
            remarks=candidate.get("remarks"),
        )
    else:
        decision = {
            "decision": "needs_human_input",
            "product_scope": "needs_human_input",
            "reason_codes": [],
        }
    return ParsedClassificationEvidence(
        official_code=requested_code,
        state="accepted",
        decision=decision["product_scope"],
        reason=(decision.get("reason_codes") or [None])[0],
        official_code_raw=candidate.get("official_code"),
        market_raw=market,
        security_type_raw=security_type,
        listing_date=listing_date,
        isin_raw=candidate.get("isin"),
        cfi_raw=candidate.get("cfi"),
        currency_raw=currency,
        remarks_raw=candidate.get("remarks"),
        raw_cells=raw_cells,
        schema_fingerprint=schema_hash,
    )


def classification_url(official_code: str) -> str:
    code = str(official_code).strip()
    if not code:
        raise ValueError("official_code is required")
    return TWSE_ISIN_CLASSIFICATION_URL.format(official_code=quote(code, safe=""))


class TwseIsinClassificationCollector:
    """Explicitly invoked, allowlisted Traditional ISIN transport."""

    def __init__(
        self,
        fetcher: Callable[..., Any] | None = None,
        *,
        timeout: int = 20,
    ) -> None:
        self.fetcher = fetcher or requests.get
        self.timeout = timeout

    def fetch(self, official_code: str) -> dict[str, Any]:
        url = classification_url(official_code)
        response = self.fetcher(url, timeout=self.timeout)
        response.raise_for_status()
        content = getattr(response, "content", None)
        html = getattr(response, "text", None)
        if not isinstance(html, str):
            if content is None:
                raise ValueError("classification response has no body")
            html = bytes(content).decode("utf-8")
        raw_bytes = bytes(content) if content is not None else html.encode("utf-8")
        return {
            "official_code": str(official_code).strip(),
            "source_url": url,
            "html": html,
            "raw_payload_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }


__all__ = [
    "ParsedClassificationEvidence",
    "TWSE_ISIN_CLASSIFICATION_RESOURCE_ID",
    "TWSE_ISIN_CLASSIFICATION_URL",
    "TwseIsinClassificationCollector",
    "classification_url",
    "parse_twse_isin_classification",
]
