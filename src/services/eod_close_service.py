"""Read-only current and point-in-time EOD close context services."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.collectors.eod_close_collectors import (
    TPEX_EOD_DAILY_CLOSE_QUOTES_URL,
    TWSE_EOD_STOCK_DAY_ALL_URL,
)
from src.domain.eod_close import (
    EOD_ASOF_SELECTION_SCOPE,
    EOD_PRICE_SEMANTICS_VERSION,
    EodProductScope,
    compose_context,
    classify_product,
    normalize_decimal_text,
    parse_iso_date,
    positive_decimal_text,
)
from src.domain.universe import parse_canonical_symbol, validate_knowledge_cutoff_at
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.eod_close_repository import EodCloseRepository


_TAIPEI = ZoneInfo("Asia/Taipei")
_SOURCE_BLOCKING_STATES = {
    "provider_error", "schema_changed", "blocked", "revoked", "rejected",
}

_CLASSIFICATION_MARKET = {
    "TWSE": "上市",
    "TPEX": "上櫃",
}


class EodCloseService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        repository: EodCloseRepository | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository or EodCloseRepository(db_path)
        self.clock = clock or utc_now_timestamp

    @staticmethod
    def _evaluated_at(value: str | None, clock: Callable[[], str]) -> str:
        candidate = value or clock()
        return normalize_utc_timestamp(candidate, "evaluated_at")

    @staticmethod
    def _flags(value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return set()
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _source_url(resource_id: str) -> str:
        return (
            TWSE_EOD_STOCK_DAY_ALL_URL
            if resource_id == "twse.eod.stock_day_all"
            else TPEX_EOD_DAILY_CLOSE_QUOTES_URL
        )

    @staticmethod
    def _source_status_reasons(
        snapshot: dict[str, Any], *, current: bool, reasons: list[str], flags: set[str]
    ) -> bool:
        status = str(snapshot.get("status") or "unknown")
        if status == "provider_error":
            reasons.append("provider_error")
        elif status == "schema_changed":
            reasons.append("schema_changed")
        elif status in {"blocked", "rejected"}:
            reasons.extend(["latest_revision_blocked", "current_latest_blocked"] if current else ["latest_revision_blocked"])
        elif status == "revoked":
            reasons.extend([
                "latest_revision_blocked",
                "current_latest_blocked" if current else "source_revoke_without_replacement",
            ])
            if current:
                reasons.append("source_revoke_without_replacement")
        if snapshot.get("source_published_at") is None:
            flags.add("publication_instant_unproven")
        partial = snapshot.get("coverage_state") == "partial"
        if partial:
            flags.add("partial_venue_payload")
            if not (
                snapshot.get("coverage_proof_type")
                and snapshot.get("coverage_proof_reference")
            ):
                reasons.append("schema_changed")
                return False
        return partial

    @staticmethod
    def _identity_fields(identity: dict[str, Any] | None) -> dict[str, Any]:
        if not identity:
            return {
                "instrument_id": None,
                "official_code": None,
                "venue": None,
                "security_type": None,
            }
        return {
            "instrument_id": identity.get("instrument_id"),
            "official_code": identity.get("official_code"),
            "venue": identity.get("venue"),
            "security_type": identity.get("security_type"),
        }

    def _compose(self, bundle: dict[str, Any], *, current: bool) -> dict[str, Any]:
        canonical_symbol = f"{bundle['official_code']}.{'TW' if bundle['venue'] == 'TWSE' else 'TWO'}"
        snapshot = bundle.get("snapshot")
        observation = bundle.get("observation")
        identity = bundle.get("identity")
        resource = bundle.get("resource") or {}
        policy = bundle.get("policy")
        reasons: list[str] = []
        flags: set[str] = set()
        source_date: str | None = None
        source_date_valid = False
        source_is_active = False
        source_is_partial = False
        source_date_usable = False
        evaluated_at = bundle["evaluated_at"]
        selected_trade_date = bundle.get("selected_trade_date")

        if snapshot:
            source_date_raw = snapshot.get("source_trade_date")
            if snapshot.get("source_trade_date_status") == "valid" and source_date_raw:
                try:
                    source_date = parse_iso_date(source_date_raw, "source_trade_date")
                    source_date_valid = True
                except ValueError:
                    reasons.append("source_date_in_future_or_invalid")
            elif source_date_raw:
                reasons.append("source_date_in_future_or_invalid")
            source_is_partial = self._source_status_reasons(
                snapshot, current=current, reasons=reasons, flags=flags
            )
            source_status = str(snapshot.get("status") or "unknown")
            source_is_active = source_status in {"available", "accepted", "partial"}
            if source_status == "unknown":
                flags.add("source_empty")
                if not snapshot.get("row_count"):
                    reasons.append("source_empty")
            if source_status == "insufficient_data":
                flags.add("source_empty")
            if source_date_valid:
                source_date_usable = True
        else:
            reasons.extend(["no_same_day_snapshot", "current_session_unproven"] if current else ["no_cutoff_visible_observation"])
            flags.add("no_same_day_snapshot" if current else "no_cutoff_visible_observation")

        if current:
            local_date = datetime.fromisoformat(
                evaluated_at.replace("Z", "+00:00")
            ).astimezone(_TAIPEI).date().isoformat()
            if source_date_valid and source_date:
                selected_trade_date = source_date
                if source_date > local_date:
                    reasons.append("source_date_in_future_or_invalid")
                    source_date_usable = False
                elif source_date < local_date:
                    reasons.append("source_date_older_than_evaluated_date")
                    source_date_usable = False
                    # A stale positive partial is retained as a quality flag,
                    # but its quality state must not become top-level partial.
                    source_is_partial = False
            elif snapshot and source_is_active and not source_date_valid:
                reasons.extend(["no_same_day_snapshot", "current_session_unproven"])
        elif selected_trade_date:
            try:
                selected_trade_date = parse_iso_date(selected_trade_date, "selected_trade_date")
                source_date_usable = True
            except ValueError:
                reasons.append("source_date_in_future_or_invalid")
                selected_trade_date = None
                source_date_usable = False
        elif snapshot and source_date_valid:
            selected_trade_date = source_date

        if not current and source_date_valid and source_date:
            cutoff_local_date = datetime.fromisoformat(
                evaluated_at.replace("Z", "+00:00")
            ).astimezone(_TAIPEI).date().isoformat()
            if source_date > cutoff_local_date:
                reasons.append("source_date_in_future_or_invalid")
                source_date_usable = False

        source_blocked = any(
            reason in {
                "provider_error", "schema_changed", "latest_revision_blocked",
                "current_latest_blocked", "source_revoke_without_replacement",
                "source_date_in_future_or_invalid",
            }
            for reason in reasons
        )
        can_evaluate_row = bool(snapshot and source_is_active and source_date_usable and not source_blocked)
        if not current and snapshot and source_date_valid and source_is_active and not source_blocked:
            can_evaluate_row = True

        if can_evaluate_row:
            expected_venue = bundle["venue"]
            if observation is None:
                if identity and str(identity.get("trading_state")) in {"suspended", "no_trading"} and not source_is_partial:
                    reasons.append("instrument_suspended")
                else:
                    reasons.append("row_missing")
            else:
                if (
                    str(observation.get("venue") or "").upper() != expected_venue
                    or str(observation.get("official_code") or "") != bundle["official_code"]
                ):
                    reasons.append("cross_venue_mismatch")
                if identity is None:
                    reasons.append("identity_mapping_unverified")
                elif (
                    identity.get("venue") != expected_venue
                    or identity.get("official_code") != bundle["official_code"]
                    or identity.get("canonical_symbol") != canonical_symbol
                ):
                    reasons.append("cross_venue_mismatch")
                classification = bundle.get("classification")
                product_scope = EodProductScope.NEEDS_HUMAN_INPUT.value
                if classification is None:
                    reasons.append("classification_evidence_missing")
                else:
                    classifier_state = str(classification.get("classification_state") or "unknown")
                    if classifier_state == "schema_changed":
                        reasons.append("schema_changed")
                    elif classifier_state in {"blocked", "revoked"}:
                        reasons.extend(["latest_revision_blocked", "current_latest_blocked"] if current else ["latest_revision_blocked"])
                        if classifier_state == "revoked":
                            reasons.append("source_revoke_without_replacement")
                    elif classifier_state == "ambiguous":
                        reasons.append("classification_evidence_missing")
                    elif classifier_state != "accepted":
                        reasons.append("classification_evidence_missing")
                    else:
                        decision = classify_product(
                            official_code=classification.get("official_code"),
                            requested_code=bundle["official_code"],
                            market=classification.get("market_raw"),
                            expected_market=_CLASSIFICATION_MARKET[expected_venue],
                            security_type=classification.get("security_type_raw"),
                            listing_date=classification.get("listing_date"),
                            trade_date=selected_trade_date,
                            currency=classification.get("currency_raw"),
                        )
                        product_scope = str(decision["product_scope"])
                        reasons.extend(decision.get("reason_codes") or [])
                if policy is None or not int(policy.get("enabled", 0)):
                    reasons.append("currency_unit_unproven")
                elif policy.get("currency") != "TWD" or policy.get("unit") != "TWD_per_share":
                    reasons.append("foreign_currency_not_supported")
                elif not policy.get("approved_unit_evidence_reference"):
                    reasons.append("currency_unit_unproven")

                if identity and str(identity.get("trading_state")) in {"suspended", "no_trading"}:
                    reasons.append("instrument_suspended")

                close_source = observation.get("close_value")
                if close_source is None:
                    close_source = observation.get("raw_close_text")
                close_normalized, close_decimal, close_state = normalize_decimal_text(close_source)
                if close_state != "valid" or close_decimal is None or close_decimal <= 0:
                    reasons.append("close_missing")
                volume_source = observation.get("volume_value")
                if volume_source is None:
                    volume_source = observation.get("raw_volume_text")
                volume_normalized, volume_decimal, volume_state = normalize_decimal_text(volume_source)
                if volume_state != "valid" or volume_decimal is None:
                    reasons.append("volume_unusable")
                elif volume_decimal == 0:
                    reasons.append("official_zero_volume_not_public_eligible")
                if source_is_partial:
                    reasons.insert(0, "partial_venue_payload")
                if product_scope == EodProductScope.NOT_APPLICABLE.value and not any(
                    reason in reasons for reason in ("unsupported_security_type", "instrument_suspended")
                ):
                    reasons.append("unsupported_security_type")
            if source_is_partial and "partial_venue_payload" not in reasons:
                reasons.insert(0, "partial_venue_payload")

        if snapshot and source_is_partial and not can_evaluate_row:
            # Same-day partial is a venue-level state even if the requested row
            # is absent. Older partial was removed from status reasons above.
            if source_date_usable:
                reasons.insert(0, "partial_venue_payload")

        # Identity is a required public gate, but absence is only actionable
        # once a concrete EOD row exists; empty/source-missing states retain
        # their more precise unknown/insufficient status.
        identity_fields = self._identity_fields(identity)
        product_scope = EodProductScope.NEEDS_HUMAN_INPUT.value
        if observation is not None and bundle.get("classification"):
            classification = bundle["classification"]
            if classification.get("classification_state") == "accepted":
                decision = classify_product(
                    official_code=classification.get("official_code"),
                    requested_code=bundle["official_code"],
                    market=classification.get("market_raw"),
                    expected_market=_CLASSIFICATION_MARKET[bundle["venue"]],
                    security_type=classification.get("security_type_raw"),
                    listing_date=classification.get("listing_date"),
                    trade_date=selected_trade_date,
                    currency=classification.get("currency_raw"),
                )
                product_scope = str(decision["product_scope"])
        if any(reason == "unsupported_security_type" for reason in reasons):
            product_scope = EodProductScope.NOT_APPLICABLE.value

        freshness = "unknown"
        if source_blocked:
            freshness = "blocked"
        elif current and source_date_valid and selected_trade_date == datetime.fromisoformat(evaluated_at.replace("Z", "+00:00")).astimezone(_TAIPEI).date().isoformat():
            freshness = "current"
        elif not current and source_date_usable:
            freshness = "current"
        elif current and source_date_valid:
            freshness = "stale"

        row_complete = bool(observation)
        close_value = None
        if observation:
            close_value = positive_decimal_text(
                observation.get("close_value") if observation.get("close_value") is not None else observation.get("raw_close_text")
            )
        complete_source = bool(snapshot and source_is_active and not source_is_partial and source_date_usable)
        complete_identity = bool(identity and identity_fields["official_code"] == bundle["official_code"] and identity_fields["venue"] == bundle["venue"])
        non_gate_reasons = {
            "publication_instant_unproven", "source_empty", "no_same_day_snapshot",
            "current_session_unproven", "source_date_older_than_evaluated_date",
        }
        gate_reasons = [reason for reason in reasons if reason not in non_gate_reasons]
        evidence_complete = bool(
            complete_source
            and row_complete
            and complete_identity
            and not gate_reasons
            and close_value is not None
            and observation.get("public_eligibility_status") == "eligible"
        )
        if identity and str(identity.get("trading_state")) in {"suspended", "no_trading"}:
            evidence_complete = False
        flags.update(self._flags(observation.get("quality_flags_json") if observation else None))
        flags.update(reason for reason in reasons if reason in {
            "source_empty", "partial_venue_payload", "official_zero_volume_not_public_eligible",
            "volume_unusable", "close_missing", "row_missing", "instrument_suspended",
        })
        if snapshot and snapshot.get("source_published_at") is None:
            flags.add("publication_instant_unproven")

        resource_key = resource.get("logical_resource_key")
        fields = {
            "canonical_symbol": canonical_symbol,
            **identity_fields,
            "security_type": identity_fields["security_type"],
            "knowledge_cutoff_at": bundle.get("knowledge_cutoff_at"),
            "evaluated_at": evaluated_at,
            "selection_scope": bundle.get("selection_scope"),
            "selected_trade_date": selected_trade_date,
            "currency": policy.get("currency") if policy else None,
            "unit": policy.get("unit") if policy else None,
            "price_semantics": policy.get("price_semantics_version") if policy else EOD_PRICE_SEMANTICS_VERSION,
            "provider": "TWSE" if bundle["venue"] == "TWSE" else "TPEx",
            "resource_key": resource_key,
            "source_url": snapshot.get("source_url") if snapshot else self._source_url(bundle["resource_id"]),
            "source_trade_date": source_date,
            "observed_at": (snapshot.get("available_at") or snapshot.get("ingested_at")) if snapshot else None,
            "source_scope": snapshot.get("source_scope") if snapshot else (policy or {}).get("source_trading_scope"),
            "quality_flags": tuple(sorted(flags)),
        }
        return compose_context(
            reasons=reasons,
            freshness_state=freshness,
            current_complete=bool(current and evidence_complete),
            evidence_complete=evidence_complete,
            close_value=close_value,
            product_scope=product_scope,
            **fields,
        )

    def current(self, canonical_symbol: str, *, evaluated_at: str | None = None) -> dict[str, Any]:
        venue, code = parse_canonical_symbol(canonical_symbol)
        del venue, code
        normalized_evaluated_at = self._evaluated_at(evaluated_at, self.clock)
        with self.repository.read_transaction() as conn:
            bundle = self.repository.current_bundle(
                conn,
                canonical_symbol=canonical_symbol,
                evaluated_at=normalized_evaluated_at,
            )
            return self._compose(bundle, current=True)

    def as_of(self, canonical_symbol: str, *, knowledge_cutoff_at: str) -> dict[str, Any]:
        parse_canonical_symbol(canonical_symbol)
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        with self.repository.read_transaction() as conn:
            bundle = self.repository.as_of_bundle(
                conn,
                canonical_symbol=canonical_symbol,
                knowledge_cutoff_at=cutoff,
            )
            return self._compose(bundle, current=False)


__all__ = ["EodCloseService"]
