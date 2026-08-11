"""Validated, unit-explicit contracts for Evidence Model v2 liquidity data."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from src.domain.valuation import normalize_utc_timestamp


M1B_UNIT_MULTIPLIERS = {
    "TWD": 1.0,
    "TWD_million": 1_000_000.0,
    "TWD_100_million": 100_000_000.0,
}


def normalize_twd(value: float, raw_unit: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("monetary value must be finite and greater than zero")
    try:
        multiplier = M1B_UNIT_MULTIPLIERS[raw_unit]
    except KeyError as exc:
        raise ValueError(f"unsupported monetary unit: {raw_unit}") from exc
    return number * multiplier


@dataclass(frozen=True)
class M1BMonthlyObservation:
    period: str
    value_raw: float
    raw_unit: str
    data_date: str
    available_at: str
    fetched_at: str
    source: str
    source_dataset: str
    revision: int = 1
    status: str = "available"
    source_url: str | None = None
    payload_hash: str | None = None
    publication_evidence_id: str | None = None
    quality_note: str | None = None

    def canonical_payload(self) -> dict:
        if len(self.period) != 7 or self.period[4] != "-":
            raise ValueError("period must use YYYY-MM")
        date.fromisoformat(self.data_date)
        if self.revision < 1:
            raise ValueError("revision must be at least 1")
        if self.status not in {"available", "revoked"}:
            raise ValueError("unsupported M1B status")
        if not self.source.strip() or not self.source_dataset.strip():
            raise ValueError("source and source_dataset are required")
        return {
            "period": self.period,
            "value_raw": float(self.value_raw),
            "raw_unit": self.raw_unit,
            "value_twd": normalize_twd(self.value_raw, self.raw_unit),
            "data_date": self.data_date,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "fetched_at": normalize_utc_timestamp(self.fetched_at, "fetched_at"),
            "source": self.source,
            "source_dataset": self.source_dataset,
            "source_url": self.source_url,
            "payload_hash": self.payload_hash,
            "publication_evidence_id": self.publication_evidence_id,
            "revision": self.revision,
            "status": self.status,
            "quality_note": self.quality_note,
        }


@dataclass(frozen=True)
class MarketTurnoverObservation:
    trade_date: str
    twse_turnover_twd: float | None
    tpex_turnover_twd: float | None
    twse_source: str | None
    tpex_source: str | None
    twse_dataset: str | None
    tpex_dataset: str | None
    available_at: str
    fetched_at: str
    revision: int = 1
    status: str | None = None
    twse_payload_hash: str | None = None
    tpex_payload_hash: str | None = None
    quality_note: str | None = None

    def canonical_payload(self) -> dict:
        date.fromisoformat(self.trade_date)
        if self.revision < 1:
            raise ValueError("revision must be at least 1")
        if self.status not in {None, "available", "partial", "revoked"}:
            raise ValueError("unsupported market turnover status")
        twse = self._optional_money(self.twse_turnover_twd, "twse_turnover_twd")
        tpex = self._optional_money(self.tpex_turnover_twd, "tpex_turnover_twd")
        if self.status != "revoked" and twse is None and tpex is None:
            raise ValueError("at least one official market turnover is required")
        if twse is not None and (not self.twse_source or not self.twse_dataset):
            raise ValueError("TWSE source and dataset are required")
        if tpex is not None and (not self.tpex_source or not self.tpex_dataset):
            raise ValueError("TPEx source and dataset are required")
        complete = twse is not None and tpex is not None
        derived_status = "available" if complete else "partial"
        status = self.status or derived_status
        if status == "available" and not complete:
            raise ValueError("available turnover requires both TWSE and TPEx")
        if status == "partial" and (complete or (twse is None and tpex is None)):
            raise ValueError("partial turnover requires exactly one market")
        if status == "revoked":
            complete = False
        return {
            "trade_date": self.trade_date,
            "twse_turnover_twd": twse,
            "tpex_turnover_twd": tpex,
            "total_turnover_twd": twse + tpex if complete else None,
            "twse_source": self.twse_source,
            "tpex_source": self.tpex_source,
            "twse_dataset": self.twse_dataset,
            "tpex_dataset": self.tpex_dataset,
            "twse_payload_hash": self.twse_payload_hash,
            "tpex_payload_hash": self.tpex_payload_hash,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "fetched_at": normalize_utc_timestamp(self.fetched_at, "fetched_at"),
            "revision": self.revision,
            "status": status,
            "quality_note": self.quality_note,
        }

    @staticmethod
    def _optional_money(value: float | None, field: str) -> float | None:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{field} must be finite and greater than zero")
        return number
