"""Official-source ingestion with immutable operational evidence.

The service intentionally separates transport success, schema validation,
source availability, and downstream model eligibility.  It does not schedule
itself and does not approve research inputs.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

import requests

from src.collectors.cbc_collector import CBCCollector
from src.collectors.market_turnover_collector import MarketTurnoverCollector
from src.domain.data_foundation import (
    AuthorityTier,
    DataHealthStatus,
    DataProvider,
    DataResource,
    EligibilityStatus,
    ExpectedFrequency,
    IngestionItemStatus,
    IngestionRun,
    IngestionRunItem,
    IngestionRunStatus,
    ProviderType,
    PublicationEvidenceStatus,
    PublicationVerificationMode,
    RawResourceRevision,
    ResourcePublicationEvidence,
    ResourceType,
    StoragePolicy,
    TradingSessionStatus,
    TriggerType,
    canonical_json,
    schema_fingerprint,
    sha256_text,
)
from src.domain.liquidity import M1BMonthlyObservation, MarketTurnoverObservation
from src.domain.valuation import normalize_utc_timestamp, parse_aware_timestamp
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.liquidity_repository import LiquidityRepository


RUNNER_VERSION = "phase10.2"
REGISTRY_CREATED_AT = "2026-08-11T00:00:00Z"
TWSE_CALENDAR_URL = (
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
)


@dataclass(frozen=True)
class _OfficialSource:
    provider_id: str
    resource_id: str
    url: str
    required_fields: tuple[str, ...]
    value_field: str
    source_name: str
    dataset: str
    parser: Callable[[list[dict], str], float | None]


TURNOVER_SOURCES = (
    _OfficialSource(
        provider_id="twse",
        resource_id="twse.market-turnover",
        url=MarketTurnoverCollector.TWSE_OPENAPI_URL,
        required_fields=("Date", "TradeValue"),
        value_field="TradeValue",
        source_name="TWSE",
        dataset="exchangeReport/FMTQIK",
        parser=MarketTurnoverCollector.parse_twse_openapi,
    ),
    _OfficialSource(
        provider_id="tpex",
        resource_id="tpex.market-turnover",
        url=MarketTurnoverCollector.TPEX_OPENAPI_URL,
        required_fields=("Date", "TradeAmount"),
        value_field="TradeAmount",
        source_name="TPEx",
        dataset="tpex_daily_trading_index",
        parser=MarketTurnoverCollector.parse_tpex_openapi,
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _payload_hash(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


class ProductionIngestionService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        fetcher: Callable[..., Any] | None = None,
    ):
        self.foundation = DataFoundationRepository(db_path)
        self.liquidity = LiquidityRepository(db_path)
        self.fetcher = fetcher or requests.get
        self.register_default_resources()

    def register_default_resources(self) -> None:
        providers = (
            DataProvider(
                "twse", "Taiwan Stock Exchange", AuthorityTier.AUTHORITATIVE,
                ProviderType.OFFICIAL, "openapi.twse.com.tw",
                REGISTRY_CREATED_AT,
            ),
            DataProvider(
                "tpex", "Taipei Exchange", AuthorityTier.AUTHORITATIVE,
                ProviderType.OFFICIAL, "www.tpex.org.tw",
                REGISTRY_CREATED_AT,
            ),
            DataProvider(
                "cbc", "Central Bank of the Republic of China (Taiwan)",
                AuthorityTier.AUTHORITATIVE, ProviderType.OFFICIAL,
                "cpx.cbc.gov.tw", REGISTRY_CREATED_AT,
            ),
        )
        for provider in providers:
            self.foundation.register_provider(provider)

        resources = (
            DataResource(
                "twse.market-turnover", "twse", "market.turnover.daily",
                ResourceType.MARKET_TURNOVER, "TWSE", ExpectedFrequency.DAILY,
                "official_session_observed_by_ingestion",
                "twse.fmtqik", "1", "1", StoragePolicy.ARCHIVE_NORMALIZED,
                REGISTRY_CREATED_AT,
            ),
            DataResource(
                "tpex.market-turnover", "tpex", "market.turnover.daily",
                ResourceType.MARKET_TURNOVER, "TPEX", ExpectedFrequency.DAILY,
                "official_session_observed_by_ingestion",
                "tpex.daily-trading-index", "1", "1",
                StoragePolicy.ARCHIVE_NORMALIZED, REGISTRY_CREATED_AT,
            ),
            DataResource(
                "twse.trading-calendar", "twse", "market.calendar.official",
                ResourceType.TRADING_CALENDAR, "TW", ExpectedFrequency.PERIODIC,
                "official_schedule_revision_observed_by_ingestion",
                "twse.holiday-schedule", "1", "1",
                StoragePolicy.ARCHIVE_NORMALIZED, REGISTRY_CREATED_AT,
            ),
            DataResource(
                "cbc.m1b", "cbc", "monetary.m1b.monthly",
                ResourceType.MONETARY_STATISTIC, "TW", ExpectedFrequency.MONTHLY_PUBLICATION,
                "official_release_timestamp_required",
                "cbc.ef15m01", "1", "1", StoragePolicy.ARCHIVE_NORMALIZED,
                REGISTRY_CREATED_AT,
            ),
        )
        for resource in resources:
            self.foundation.register_resource(resource)

    def _start_run(
        self,
        resources: tuple[str, ...],
        started_at: str,
        trigger_type: TriggerType,
        actor_id: str,
        retry_of_run_id: str | None = None,
    ) -> IngestionRun:
        run = IngestionRun(
            ingestion_run_id=_id("run"), started_at=started_at,
            trigger_type=trigger_type, runner_version=RUNNER_VERSION,
            requested_resources=resources, actor_id=actor_id,
            retry_of_run_id=retry_of_run_id,
        )
        self.foundation.add_run(run)
        return run

    def _finish_run(
        self, run: IngestionRun, status: IngestionRunStatus, completed_at: str
    ) -> dict[str, Any]:
        completed = IngestionRun(
            ingestion_run_id=run.ingestion_run_id,
            started_at=run.started_at,
            trigger_type=run.trigger_type,
            runner_version=run.runner_version,
            requested_resources=run.requested_resources,
            actor_id=run.actor_id,
            status=status,
            completed_at=completed_at,
            retry_of_run_id=run.retry_of_run_id,
        )
        return self.foundation.complete_run(completed)

    def _add_item(
        self,
        run: IngestionRun,
        source: _OfficialSource,
        started_at: str,
        completed_at: str,
        *,
        status: IngestionItemStatus,
        health: DataHealthStatus,
        http_status: int | None = None,
        raw_hash: str | None = None,
        schema_hash: str | None = None,
        record_count: int = 0,
        accepted_count: int = 0,
        rejected_count: int = 0,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return self.foundation.add_run_item(IngestionRunItem(
            ingestion_run_item_id=_id("item"),
            ingestion_run_id=run.ingestion_run_id,
            provider_id=source.provider_id,
            resource_id=source.resource_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            quality_status=health,
            http_status=http_status,
            raw_payload_sha256=raw_hash,
            parser_version="1",
            schema_fingerprint=schema_hash,
            record_count=record_count,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            reason=reason,
        ))

    def _add_raw(
        self,
        *,
        provider_id: str,
        resource_id: str,
        logical_key: str,
        payload_hash: str,
        schema_hash: str,
        observed_at: str,
        health: DataHealthStatus,
        eligibility: EligibilityStatus,
        source_published_at: str | None = None,
        available_at: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        latest = self.foundation.latest_raw_revision(
            provider_id, resource_id, logical_key
        )
        normalized_published = (
            normalize_utc_timestamp(source_published_at, "source_published_at")
            if source_published_at else None
        )
        normalized_available = (
            normalize_utc_timestamp(available_at, "available_at")
            if available_at else None
        )
        supersedes = bool(
            latest and (
                latest["raw_payload_sha256"] != payload_hash
                or latest["source_published_at"] != normalized_published
                or latest["available_at"] != normalized_available
                or latest["quality_status"] != health.value
                or latest["eligibility_status"] != eligibility.value
            )
        )
        return self.foundation.add_raw_revision(RawResourceRevision(
            raw_resource_revision_id=_id("raw"),
            provider_id=provider_id,
            resource_id=resource_id,
            logical_revision_key=logical_key,
            received_at=observed_at,
            ingested_at=observed_at,
            raw_payload_sha256=payload_hash,
            parser_version="1",
            schema_fingerprint=schema_hash,
            storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
            quality_status=health,
            eligibility_status=eligibility,
            source_published_at=normalized_published,
            available_at=normalized_available,
            supersedes_revision_id=(
                latest["raw_resource_revision_id"]
                if supersedes
                else None
            ),
            reason=reason,
        ))

    def _record_provider_failure(
        self,
        source: _OfficialSource,
        observed_at: str,
        actor_id: str,
        error: Exception,
        trigger_type: TriggerType = TriggerType.MANUAL,
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        run = self._start_run(
            (source.resource_id,), observed_at, trigger_type, actor_id,
            retry_of_run_id,
        )
        try:
            self.foundation.acquire_resource_lock(
                source.resource_id, run.ingestion_run_id, observed_at
            )
        except RuntimeError:
            self._finish_run(run, IngestionRunStatus.BLOCKED, observed_at)
            return {
                "run_id": run.ingestion_run_id,
                "status": "blocked",
                "items": [],
                "reason": "resource_ingestion_already_locked",
            }
        try:
            item = self._add_item(
                run, source, observed_at, observed_at,
                status=IngestionItemStatus.PROVIDER_ERROR,
                health=DataHealthStatus.PROVIDER_ERROR,
                http_status=getattr(
                    getattr(error, "response", None), "status_code", None
                ),
                reason=f"provider_request_failed:{type(error).__name__}",
            )
            self._finish_run(run, IngestionRunStatus.FAILED, observed_at)
            return {
                "run_id": run.ingestion_run_id,
                "status": "failed",
                "items": [item],
                "reason": "provider_request_failed",
            }
        finally:
            self.foundation.release_resource_lock(
                source.resource_id, run.ingestion_run_id
            )

    @staticmethod
    def _matching_turnover_row(payload: list[dict], trade_date: str) -> dict:
        for row in payload:
            if MarketTurnoverCollector._iso_date(row.get("Date", "")) == trade_date:
                return row
        raise ValueError(f"{trade_date} is absent from official response")

    def ingest_official_turnover(
        self,
        trade_date: str,
        *,
        observed_at: str | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        actor_id: str = "internal.cli",
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        datetime.strptime(trade_date, "%Y-%m-%d")
        observed = normalize_utc_timestamp(
            observed_at or _utc_now(), "observed_at"
        )
        run = self._start_run(
            tuple(source.resource_id for source in TURNOVER_SOURCES),
            observed, trigger_type, actor_id, retry_of_run_id,
        )
        successes: dict[str, dict[str, Any]] = {}
        items: list[dict[str, Any]] = []
        locks: list[str] = []
        try:
            for source in TURNOVER_SOURCES:
                self.foundation.acquire_resource_lock(
                    source.resource_id, run.ingestion_run_id, observed
                )
                locks.append(source.resource_id)
        except RuntimeError:
            for resource_id in reversed(locks):
                self.foundation.release_resource_lock(
                    resource_id, run.ingestion_run_id
                )
            self._finish_run(run, IngestionRunStatus.BLOCKED, observed)
            return {
                "run_id": run.ingestion_run_id,
                "status": "blocked",
                "trade_date": trade_date,
                "turnover": None,
                "items": [],
                "reason": "resource_ingestion_already_locked",
            }
        try:
            for source in TURNOVER_SOURCES:
                try:
                    response = self.fetcher(source.url, timeout=15)
                    response.raise_for_status()
                    http_status = getattr(response, "status_code", 200)
                    try:
                        payload = response.json()
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise ValueError("official response is not valid JSON") from exc
                    if not isinstance(payload, list):
                        raise ValueError("official response must be a list")
                    row = self._matching_turnover_row(payload, trade_date)
                    if any(field not in row for field in source.required_fields):
                        raise ValueError("official response schema is missing required fields")
                    value = source.parser(payload, trade_date)
                    if value is None or value <= 0:
                        raise ValueError("official turnover must be greater than zero")
                    normalized_hash = _payload_hash(row)
                    schema_hash = schema_fingerprint(source.required_fields)
                    raw = self._add_raw(
                        provider_id=source.provider_id,
                        resource_id=source.resource_id,
                        logical_key=trade_date,
                        payload_hash=normalized_hash,
                        schema_hash=schema_hash,
                        observed_at=observed,
                        health=DataHealthStatus.FRESH,
                        eligibility=EligibilityStatus.ELIGIBLE,
                        available_at=observed,
                        reason="observed_available_at_current_official_retrieval",
                    )
                    successes[source.provider_id] = {
                        "value": value, "raw": raw, "source": source,
                    }
                    items.append(self._add_item(
                        run, source, observed, observed,
                        status=IngestionItemStatus.ACCEPTED,
                        health=DataHealthStatus.FRESH,
                        http_status=http_status,
                        raw_hash=normalized_hash,
                        schema_hash=schema_hash,
                        record_count=1,
                        accepted_count=1,
                    ))
                except requests.RequestException as exc:
                    items.append(self._add_item(
                        run, source, observed, observed,
                        status=IngestionItemStatus.PROVIDER_ERROR,
                        health=DataHealthStatus.PROVIDER_ERROR,
                        http_status=getattr(getattr(exc, "response", None), "status_code", None),
                        reason=f"provider_request_failed:{type(exc).__name__}",
                    ))
                except ValueError as exc:
                    items.append(self._add_item(
                        run, source, observed, observed,
                        status=IngestionItemStatus.SCHEMA_CHANGED,
                        health=DataHealthStatus.SCHEMA_CHANGED,
                        reason=str(exc),
                    ))

            domain_record = None
            if successes:
                previous = self.liquidity.latest_turnover_revision(trade_date)
                any_new_raw = any(value["raw"]["created"] for value in successes.values())
                desired_status = "available" if len(successes) == 2 else "partial"
                if previous is None or any_new_raw or previous["status"] != desired_status:
                    twse = successes.get("twse")
                    tpex = successes.get("tpex")
                    domain_record = self.liquidity.add_turnover(
                        MarketTurnoverObservation(
                            trade_date=trade_date,
                            twse_turnover_twd=twse["value"] if twse else None,
                            tpex_turnover_twd=tpex["value"] if tpex else None,
                            twse_source="TWSE" if twse else None,
                            tpex_source="TPEx" if tpex else None,
                            twse_dataset="exchangeReport/FMTQIK" if twse else None,
                            tpex_dataset="tpex_daily_trading_index" if tpex else None,
                            twse_payload_hash=(twse["raw"]["raw_payload_sha256"] if twse else None),
                            tpex_payload_hash=(tpex["raw"]["raw_payload_sha256"] if tpex else None),
                            available_at=observed,
                            fetched_at=observed,
                            revision=(int(previous["revision"]) + 1 if previous else 1),
                            status=desired_status,
                            quality_note=(
                                None if desired_status == "available"
                                else "one_official_market_source_unavailable"
                            ),
                        ),
                        ingested_at=observed,
                    )
                else:
                    domain_record = previous
            terminal = (
                IngestionRunStatus.SUCCEEDED if len(successes) == 2
                else IngestionRunStatus.PARTIAL if successes
                else IngestionRunStatus.FAILED
            )
            self._finish_run(run, terminal, observed)
            return {
                "run_id": run.ingestion_run_id,
                "status": terminal.value,
                "trade_date": trade_date,
                "turnover": domain_record,
                "items": items,
            }
        finally:
            for resource_id in reversed(locks):
                self.foundation.release_resource_lock(
                    resource_id, run.ingestion_run_id
                )

    @staticmethod
    def _cbc_rows(payload: dict) -> list[dict[str, Any]]:
        official = payload.get("data", {})
        if isinstance(official, dict) and "dataSets" in official:
            structure = official.get("structure", {})
            rows = official.get("dataSets", [])
            categories = [item.get("data", "") for item in structure.get("Table1", [])]
        else:
            result = payload.get("result", {})
            tables = result.get("structure", {}).get("tables", [])
            rows = result.get("data", [])
            categories = next(
                (table.get("items", []) for table in tables if table.get("name") == "Table1"),
                [],
            )
        index = next(
            (
                i for i, value in enumerate(categories)
                if "M1B" in unicodedata.normalize("NFKC", str(value)).upper().replace(" ", "")
            ),
            None,
        )
        if index is None:
            raise ValueError("CBC EF15M01 M1B category was not found")
        value_index = 1 + index * 2
        parsed = []
        for row in rows:
            if len(row) <= value_index:
                raise ValueError("CBC EF15M01 row does not match the declared schema")
            period, data_date = CBCCollector._cbc_period(row[0])
            parsed.append({
                "period": period,
                "data_date": data_date,
                "value_raw": float(str(row[value_index]).replace(",", "")),
                "raw_unit": "TWD_million",
            })
        if not parsed:
            raise ValueError("CBC EF15M01 response contains no M1B rows")
        return parsed

    def ingest_cbc_m1b(
        self,
        payload: dict,
        publication_evidence_by_period: dict[str, Any],
        *,
        observed_at: str | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        actor_id: str = "internal.cli",
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        observed = normalize_utc_timestamp(observed_at or _utc_now(), "observed_at")
        resource_id = "cbc.m1b"
        run = self._start_run(
            (resource_id,), observed, trigger_type, actor_id, retry_of_run_id
        )
        try:
            self.foundation.acquire_resource_lock(
                resource_id, run.ingestion_run_id, observed
            )
        except RuntimeError:
            self._finish_run(run, IngestionRunStatus.BLOCKED, observed)
            return {
                "run_id": run.ingestion_run_id,
                "status": "blocked",
                "records": [],
                "candidate_periods": [],
                "raw_revisions": [],
                "items": [],
                "reason": "resource_ingestion_already_locked",
            }
        try:
            try:
                rows = self._cbc_rows(payload)
            except (TypeError, ValueError, KeyError) as exc:
                source = _OfficialSource(
                    "cbc", resource_id, CBCCollector.OFFICIAL_M1B_URL,
                    ("period", "value_raw", "raw_unit"), "value_raw",
                    "CBC", CBCCollector.OFFICIAL_M1B_DATASET,
                    lambda _payload, _period: None,
                )
                item = self._add_item(
                    run, source, observed, observed,
                    status=IngestionItemStatus.SCHEMA_CHANGED,
                    health=DataHealthStatus.SCHEMA_CHANGED,
                    reason=str(exc),
                )
                self._finish_run(run, IngestionRunStatus.FAILED, observed)
                return {"run_id": run.ingestion_run_id, "status": "failed", "items": [item]}

            schema_hash = schema_fingerprint(("period", "data_date", "value_raw", "raw_unit"))
            accepted_evidence: dict[str, dict[str, Any]] = {}
            validated_evidence: dict[str, ResourcePublicationEvidence] = {}
            try:
                known_periods = {row["period"] for row in rows}
                unknown_periods = set(publication_evidence_by_period) - known_periods
                if unknown_periods:
                    raise ValueError("publication evidence contains an unknown CBC period")
                for period, value in publication_evidence_by_period.items():
                    if isinstance(value, str):
                        normalized = normalize_utc_timestamp(
                            value, f"official_release_at[{period}]"
                        )
                        if parse_aware_timestamp(
                            normalized, f"official_release_at[{period}]"
                        ) > parse_aware_timestamp(observed, "observed_at"):
                            raise ValueError(
                                "CBC publication timestamp cannot be later than observed_at"
                            )
                        continue
                    if not isinstance(value, dict):
                        raise ValueError(
                            "CBC publication evidence must be an object or bare timestamp"
                        )
                    evidence = ResourcePublicationEvidence(
                        provider_id="cbc",
                        resource_id=resource_id,
                        logical_revision_key=period,
                        official_release_at=value.get("official_release_at", ""),
                        source_reference=value.get("source_reference", ""),
                        source_identity=value.get("source_identity", ""),
                        evidence_file_sha256=value.get("evidence_file_sha256", ""),
                        captured_at=value.get("captured_at", ""),
                        verification_mode=PublicationVerificationMode(
                            value.get("verification_mode", "")
                        ),
                        verified_by=value.get("verified_by", ""),
                        status=PublicationEvidenceStatus(
                            value.get("status", "accepted")
                        ),
                    )
                    evidence_payload = evidence.canonical_payload()
                    if parse_aware_timestamp(
                        evidence_payload["official_release_at"],
                        f"official_release_at[{period}]",
                    ) > parse_aware_timestamp(observed, "observed_at"):
                        raise ValueError(
                            "CBC publication timestamp cannot be later than observed_at"
                        )
                    if parse_aware_timestamp(
                        evidence_payload["captured_at"], f"captured_at[{period}]"
                    ) > parse_aware_timestamp(observed, "observed_at"):
                        raise ValueError(
                            "CBC publication evidence captured_at cannot be later than observed_at"
                        )
                    validated_evidence[period] = evidence
            except (KeyError, TypeError, ValueError) as exc:
                item = self.foundation.add_run_item(IngestionRunItem(
                    ingestion_run_item_id=_id("item"),
                    ingestion_run_id=run.ingestion_run_id,
                    provider_id="cbc", resource_id=resource_id,
                    started_at=observed, completed_at=observed,
                    status=IngestionItemStatus.REJECTED,
                    quality_status=DataHealthStatus.REJECTED,
                    parser_version="1", schema_fingerprint=schema_hash,
                    record_count=len(rows), accepted_count=0,
                    rejected_count=len(rows), reason=str(exc),
                ))
                self._finish_run(run, IngestionRunStatus.FAILED, observed)
                return {
                    "run_id": run.ingestion_run_id, "status": "failed",
                    "records": [], "candidate_periods": [],
                    "raw_revisions": [], "items": [item],
                }
            for period, evidence in validated_evidence.items():
                stored = self.foundation.add_publication_evidence(
                    evidence, ingested_at=observed
                )
                if stored["status"] == PublicationEvidenceStatus.ACCEPTED.value:
                    accepted_evidence[period] = stored
            records = []
            candidates = []
            raw_records = []
            for row in rows:
                period = row["period"]
                evidence = accepted_evidence.get(period)
                normalized_release = (
                    evidence["official_release_at"] if evidence else None
                )
                row_hash = _payload_hash(row)
                raw = self._add_raw(
                    provider_id="cbc", resource_id=resource_id,
                    logical_key=period, payload_hash=row_hash,
                    schema_hash=schema_hash, observed_at=observed,
                    health=(DataHealthStatus.FRESH if normalized_release else DataHealthStatus.AWAITING_REVIEW),
                    eligibility=(EligibilityStatus.ELIGIBLE if normalized_release else EligibilityStatus.AWAITING_REVIEW),
                    source_published_at=normalized_release,
                    available_at=normalized_release,
                    reason=(None if normalized_release else "verified_publication_evidence_required"),
                )
                raw_records.append(raw)
                if not normalized_release:
                    candidates.append(period)
                    continue
                previous = self.liquidity.latest_m1b_revision(period)
                if (
                    raw["created"]
                    or previous is None
                    or previous.get("publication_evidence_id")
                    != evidence["publication_evidence_id"]
                ):
                    records.append(self.liquidity.add_m1b(
                        M1BMonthlyObservation(
                            period=period,
                            value_raw=row["value_raw"],
                            raw_unit=row["raw_unit"],
                            data_date=row["data_date"],
                            available_at=normalized_release,
                            fetched_at=observed,
                            source="CBC",
                            source_dataset=CBCCollector.OFFICIAL_M1B_DATASET,
                            source_url=CBCCollector.OFFICIAL_M1B_URL,
                            payload_hash=row_hash,
                            publication_evidence_id=evidence[
                                "publication_evidence_id"
                            ],
                            revision=(int(previous["revision"]) + 1 if previous else 1),
                        ),
                        ingested_at=observed,
                    ))
            item_status = (
                IngestionItemStatus.ACCEPTED if not candidates
                else IngestionItemStatus.AWAITING_REVIEW
            )
            health = (
                DataHealthStatus.FRESH if not candidates
                else DataHealthStatus.AWAITING_REVIEW
            )
            item = self.foundation.add_run_item(IngestionRunItem(
                ingestion_run_item_id=_id("item"),
                ingestion_run_id=run.ingestion_run_id,
                provider_id="cbc", resource_id=resource_id,
                started_at=observed, completed_at=observed,
                status=item_status, quality_status=health,
                raw_payload_sha256=_payload_hash(payload),
                parser_version="1", schema_fingerprint=schema_hash,
                record_count=len(rows),
                accepted_count=sum(
                    1 for row in rows if row["period"] in accepted_evidence
                ),
                rejected_count=0,
                reason=(
                    "verified_publication_evidence_required"
                    if candidates else None
                ),
            ))
            terminal = (
                IngestionRunStatus.SUCCEEDED if not candidates
                else IngestionRunStatus.PARTIAL if records
                else IngestionRunStatus.BLOCKED
            )
            self._finish_run(run, terminal, observed)
            return {
                "run_id": run.ingestion_run_id,
                "status": terminal.value,
                "records": records,
                "candidate_periods": candidates,
                "raw_revisions": raw_records,
                "publication_evidence": list(accepted_evidence.values()),
                "items": [item],
            }
        finally:
            self.foundation.release_resource_lock(resource_id, run.ingestion_run_id)

    @staticmethod
    def _calendar_date(value: str) -> str:
        text = str(value).strip().replace("/", "").replace("-", "")
        if len(text) != 7 or not text.isdigit():
            raise ValueError("TWSE calendar Date must use ROC YYYMMDD")
        year = int(text[:3]) + 1911
        return f"{year:04d}-{text[3:5]}-{text[5:7]}"

    @staticmethod
    def _calendar_status(row: dict[str, Any]) -> TradingSessionStatus:
        text = f"{row.get('Name', '')} {row.get('Description', '')}"
        if "無交易" in text:
            return TradingSessionStatus.NO_TRADING
        if "放假" in text or "假日" in text:
            return TradingSessionStatus.HOLIDAY
        if "最後交易" in text:
            return TradingSessionStatus.SPECIAL
        if "交易" in text:
            return TradingSessionStatus.TRADING
        raise ValueError("official calendar row has no explicit session meaning")

    def ingest_twse_calendar(
        self,
        payload: list[dict],
        *,
        observed_at: str | None = None,
        actor_id: str = "internal.cli",
        trigger_type: TriggerType = TriggerType.MANUAL,
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        observed = normalize_utc_timestamp(observed_at or _utc_now(), "observed_at")
        resource_id = "twse.trading-calendar"
        run = self._start_run(
            (resource_id,), observed, trigger_type, actor_id, retry_of_run_id
        )
        try:
            self.foundation.acquire_resource_lock(
                resource_id, run.ingestion_run_id, observed
            )
        except RuntimeError:
            self._finish_run(run, IngestionRunStatus.BLOCKED, observed)
            return {
                "run_id": run.ingestion_run_id,
                "status": "blocked",
                "calendar_revisions": [],
                "items": [],
                "reason": "resource_ingestion_already_locked",
            }
        try:
            required = ("Name", "Date", "Weekday", "Description")
            if not isinstance(payload, list) or not payload:
                raise ValueError("official calendar response must be a non-empty list")
            parsed = []
            for row in payload:
                if not isinstance(row, dict) or any(field not in row for field in required):
                    raise ValueError("official calendar schema is missing required fields")
                trade_date = self._calendar_date(row["Date"])
                parsed.append((trade_date, self._calendar_status(row), row))
            schema_hash = schema_fingerprint(required)
            grouped: dict[str, list[tuple[str, TradingSessionStatus, dict]]] = {}
            for parsed_row in parsed:
                grouped.setdefault(parsed_row[0][:4], []).append(parsed_row)
            revisions = []
            for year, rows in grouped.items():
                normalized_rows = [row for _, _, row in rows]
                raw = self._add_raw(
                    provider_id="twse", resource_id=resource_id,
                    logical_key=year, payload_hash=_payload_hash(normalized_rows),
                    schema_hash=schema_hash, observed_at=observed,
                    health=DataHealthStatus.FRESH,
                    eligibility=EligibilityStatus.ELIGIBLE,
                    available_at=observed,
                    reason="observed_available_at_current_official_retrieval",
                )
                for trade_date, session_status, row in rows:
                    revisions.append(self.foundation.add_calendar_revision(
                        calendar_revision_id=_id("calendar"),
                        raw_resource_revision_id=raw["raw_resource_revision_id"],
                        market="TW", trade_date=trade_date,
                        session_status=session_status.value,
                        available_at=observed, ingested_at=observed,
                        note=str(row.get("Description") or row.get("Name")),
                    ))
            item = self.foundation.add_run_item(IngestionRunItem(
                ingestion_run_item_id=_id("item"), ingestion_run_id=run.ingestion_run_id,
                provider_id="twse", resource_id=resource_id,
                started_at=observed, completed_at=observed,
                status=IngestionItemStatus.ACCEPTED,
                quality_status=DataHealthStatus.FRESH,
                raw_payload_sha256=_payload_hash(payload),
                parser_version="1", schema_fingerprint=schema_hash,
                record_count=len(parsed), accepted_count=len(parsed), rejected_count=0,
            ))
            self._finish_run(run, IngestionRunStatus.SUCCEEDED, observed)
            return {
                "run_id": run.ingestion_run_id, "status": "succeeded",
                "calendar_revisions": revisions, "items": [item],
            }
        except (TypeError, ValueError) as exc:
            source = _OfficialSource(
                "twse", resource_id, TWSE_CALENDAR_URL, required,
                "Date", "TWSE", "holidaySchedule/holidaySchedule",
                lambda _payload, _date: None,
            )
            item = self._add_item(
                run, source, observed, observed,
                status=IngestionItemStatus.SCHEMA_CHANGED,
                health=DataHealthStatus.SCHEMA_CHANGED,
                reason=str(exc),
            )
            self._finish_run(run, IngestionRunStatus.FAILED, observed)
            return {"run_id": run.ingestion_run_id, "status": "failed", "items": [item]}
        finally:
            self.foundation.release_resource_lock(resource_id, run.ingestion_run_id)

    def fetch_twse_calendar(
        self,
        *,
        observed_at: str | None = None,
        actor_id: str = "internal.cli",
        trigger_type: TriggerType = TriggerType.MANUAL,
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        observed = normalize_utc_timestamp(observed_at or _utc_now(), "observed_at")
        source = _OfficialSource(
            "twse", "twse.trading-calendar", TWSE_CALENDAR_URL,
            ("Name", "Date", "Weekday", "Description"), "Date",
            "TWSE", "holidaySchedule/holidaySchedule",
            lambda _payload, _date: None,
        )
        try:
            response = self.fetcher(TWSE_CALENDAR_URL, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            return self._record_provider_failure(
                source, observed, actor_id, exc, trigger_type, retry_of_run_id
            )
        try:
            payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = []
        return self.ingest_twse_calendar(
            payload, observed_at=observed, actor_id=actor_id,
            trigger_type=trigger_type, retry_of_run_id=retry_of_run_id,
        )

    def fetch_cbc_m1b(
        self,
        publication_evidence_by_period: dict[str, Any],
        *,
        observed_at: str | None = None,
        actor_id: str = "internal.cli",
        trigger_type: TriggerType = TriggerType.MANUAL,
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        observed = normalize_utc_timestamp(observed_at or _utc_now(), "observed_at")
        source = _OfficialSource(
            "cbc", "cbc.m1b", CBCCollector.OFFICIAL_M1B_URL,
            ("period", "value_raw", "raw_unit"), "value_raw",
            "CBC", CBCCollector.OFFICIAL_M1B_DATASET,
            lambda _payload, _period: None,
        )
        try:
            response = self.fetcher(CBCCollector.OFFICIAL_M1B_URL, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            return self._record_provider_failure(
                source, observed, actor_id, exc, trigger_type, retry_of_run_id
            )
        try:
            payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        return self.ingest_cbc_m1b(
            payload, publication_evidence_by_period,
            observed_at=observed, actor_id=actor_id,
            trigger_type=trigger_type, retry_of_run_id=retry_of_run_id,
        )
