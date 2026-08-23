"""Read-only/as-of and guarded immutable persistence for Universe Foundation."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

from src.domain.universe import (
    AvailabilityMode,
    FreshnessMode,
    FreshnessStatus,
    ListingStatus,
    MembershipState,
    TradingState,
    UniverseVenue,
    canonical_symbol_for,
    coerce_venue,
    identity_binding_fingerprint,
    normalize_universe_timestamp,
    payload_fingerprint,
    parse_source_temporal,
    parse_canonical_symbol,
    validate_knowledge_cutoff_at,
    validate_official_code,
)
from src.domain.valuation import utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration
from src.services.universe_write_guard import (
    UniverseOperatorContext,
    UniverseOperatorContextRequired,
    UniverseWriteGuard,
)
from src.services.universe_audit import write_universe_audit


class UniverseStorageUnavailable(RuntimeError):
    code = "universe_storage_unavailable"


class UniverseIdempotencyConflict(ValueError):
    code = "idempotency_key_reused"


class UniverseIdempotencyRequired(ValueError):
    code = "idempotency_key_required"

    def __init__(self) -> None:
        super().__init__(self.code)


class UniverseRawProvenanceRequired(ValueError):
    code = "raw_resource_provenance_required"

    def __init__(self) -> None:
        super().__init__(self.code)


class UniverseIdentityCollision(ValueError):
    code = "identity_collision"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class UniverseRepository:
    """Universe repository.  Construction and reads never run migrations or write rows."""

    def __init__(self, db_path: str = "data/cache.db", *, guard: UniverseWriteGuard | None = None,
                 auto_migrate: bool = False):
        self.db_path = db_path
        self.guard = guard or UniverseWriteGuard()
        if auto_migrate:
            # Explicit opt-in is useful for operator tooling, never used by API reads.
            apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
        except sqlite3.Error as exc:
            raise UniverseStorageUnavailable(str(exc)) from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_instruments'"
            ).fetchone()
        except sqlite3.Error as exc:
            conn.close()
            raise UniverseStorageUnavailable(str(exc)) from exc
        if exists is None:
            conn.close()
            raise UniverseStorageUnavailable("phase13 migration is not available")
        return conn

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def _cutoff_where(alias: str = "r") -> str:
        # A historical reference is safe only when the source has an explicit,
        # proven availability instant.  NULL is an unknown publication state,
        # not an implicit "available since ingestion" value.  Manual sources
        # additionally need the exact accepted publication evidence bound to
        # the parent Universe revision and visible at this cutoff.
        return f"""(
            {alias}.available_at IS NOT NULL
            AND {alias}.available_at <= ?
            AND {alias}.ingested_at <= ?
            AND (
                {alias}.availability_mode <> 'manual_publication_evidence_required'
                OR EXISTS (
                    SELECT 1
                    FROM universe_revisions ur
                    JOIN resource_publication_evidence pe
                      ON pe.publication_evidence_id = ur.publication_evidence_id
                    WHERE ur.universe_revision_id = {alias}.universe_revision_id
                      AND pe.status = 'accepted'
                      AND pe.official_release_at <= {alias}.available_at
                      AND pe.ingested_at <= ?
                      AND pe.publication_evidence_id = (
                          SELECT latest.publication_evidence_id
                          FROM resource_publication_evidence latest
                          WHERE latest.resource_id = ur.resource_id
                            AND latest.logical_revision_key = ur.logical_revision_key
                            AND latest.ingested_at <= ?
                          ORDER BY latest.revision_number DESC,
                                   latest.ingested_at DESC,
                                   latest.publication_evidence_id DESC
                          LIMIT 1
                      )
                )
            )
        )"""

    @classmethod
    def _effective_excluded_instrument_revision_ids(
        cls,
        conn: sqlite3.Connection,
        *,
        instrument_ids: list[str],
        cutoff: str,
    ) -> dict[str, set[str]]:
        """Return all visible historical revisions superseded by a child.

        ``universe_instrument_revisions.supersedes_revision_id`` points to an
        ``instrument_revision_id`` (``uirev_*``), while the parent resource
        revision uses the separate ``universe_revision_id`` (``urev_*``)
        domain.  Historical eligibility must stay in the instrument-revision
        domain; mixing the two silently resurrects revoked identities.

        Both accepted corrections and revoked events are chain edges.  A
        visible child excludes its direct predecessor and every visible
        predecessor ancestor, so an accepted correction followed by a revoke
        cannot resurrect the original accepted row.
        """
        ids = list(dict.fromkeys(str(value) for value in instrument_ids))
        if not ids:
            return {}
        placeholders = cls._sql_placeholders(ids)
        rows = conn.execute(
            f"""
            SELECT r.instrument_id, r.instrument_revision_id, r.supersedes_revision_id
            FROM universe_instrument_revisions r
            WHERE r.instrument_id IN ({placeholders})
              AND r.status IN ('accepted', 'revoked')
              AND r.supersedes_revision_id IS NOT NULL
              AND {cls._cutoff_where('r')}
            """,
            ids + [cutoff, cutoff, cutoff, cutoff],
        ).fetchall()
        edges: dict[str, dict[str, str | None]] = {}
        for row in rows:
            edges.setdefault(str(row["instrument_id"]), {})[
                str(row["instrument_revision_id"])
            ] = str(row["supersedes_revision_id"])

        excluded: dict[str, set[str]] = {}
        for instrument_id, children in edges.items():
            blocked: set[str] = set()
            for parent in children.values():
                current = parent
                while current and current not in blocked:
                    blocked.add(current)
                    current = children.get(current)
            excluded[instrument_id] = blocked
        return excluded

    @staticmethod
    def _event_visible_at_cutoff(row: dict[str, Any], *, cutoff: str) -> bool:
        """Apply the event's source/observation boundary without inventing time.

        ``effective_at`` is authoritative when supplied.  A date-only event is
        deliberately not applied during that same calendar date because the
        source did not prove an intraday instant.  Events without source timing
        use their explicit ``available_at`` observation boundary.
        """
        available_at = row.get("available_at")
        ingested_at = row.get("ingested_at")
        if not available_at or not ingested_at or str(available_at) > cutoff or str(ingested_at) > cutoff:
            return False
        effective_at = row.get("effective_at")
        if effective_at:
            return str(effective_at) <= cutoff
        event_date = row.get("event_date")
        if event_date:
            return str(cutoff)[:10] > str(event_date)[:10]
        return True

    @staticmethod
    def _event_sort_key(row: dict[str, Any]) -> tuple[str, int, str, str, str]:
        effective_at = row.get("effective_at")
        event_date = row.get("event_date")
        if effective_at:
            boundary, precision = str(effective_at), 2
        elif event_date:
            boundary, precision = str(event_date)[:10], 1
        else:
            boundary, precision = str(row.get("available_at") or ""), 0
        return (
            boundary,
            precision,
            str(row.get("available_at") or ""),
            str(row.get("ingested_at") or ""),
            str(row.get("lifecycle_event_id") or row.get("operational_event_id") or ""),
        )

    @classmethod
    def _latest_visible_event(
        cls,
        rows: list[dict[str, Any]],
        *,
        cutoff: str,
    ) -> dict[str, Any] | None:
        visible = [
            row for row in rows
            if cls._event_visible_at_cutoff(row, cutoff=cutoff)
        ]
        return max(visible, key=cls._event_sort_key) if visible else None

    @classmethod
    def _compose_event_state(
        cls,
        lifecycle_rows: list[dict[str, Any]],
        operational_rows: list[dict[str, Any]],
        *,
        cutoff: str,
    ) -> dict[str, Any]:
        """Compose listing and trading channels from cutoff-visible events.

        Listing lifecycle events never change trading state; operational events
        never change listing status.  A visible non-accepted latest event is
        fail-closed to ``unknown`` rather than silently resurrecting an older
        event state.
        """
        listing_event = cls._latest_visible_event(
            [row for row in lifecycle_rows if row.get("event_type") in {"listed", "terminated"}],
            cutoff=cutoff,
        )
        operational_candidates = list(operational_rows)
        # ``resumed`` is a source-backed trading transition, not a listing
        # transition.  Keep it in the operational channel without inventing a
        # separate event table row.
        operational_candidates.extend(
            {
                **row,
                "trading_state": "normal",
                "operational_event_id": row.get("lifecycle_event_id"),
                "event_source": "lifecycle",
            }
            for row in lifecycle_rows
            if row.get("event_type") == "resumed"
        )
        operational_event = cls._latest_visible_event(
            operational_candidates,
            cutoff=cutoff,
        )
        state: dict[str, Any] = {}
        if listing_event:
            state["lifecycle_event_id"] = listing_event.get("lifecycle_event_id")
            state["lifecycle_event_type"] = listing_event.get("event_type")
            state["lifecycle_event_status"] = listing_event.get("status")
            state["lifecycle_event_available_at"] = listing_event.get("available_at")
            state["lifecycle_event_effective_at"] = listing_event.get("effective_at")
            state["lifecycle_event_date"] = listing_event.get("event_date")
            if listing_event.get("status") == "accepted":
                state["listing_status"] = (
                    ListingStatus.DELISTED.value
                    if listing_event.get("event_type") == "terminated"
                    else ListingStatus.LISTED.value
                )
            else:
                state["listing_status"] = ListingStatus.UNKNOWN.value
                state.setdefault("event_reasons", []).append("lifecycle_event_not_accepted")
        if operational_event:
            state["operational_event_id"] = operational_event.get("operational_event_id")
            state["operational_event_source"] = operational_event.get("event_source", "operational")
            state["operational_event_status"] = operational_event.get("status")
            state["operational_event_available_at"] = operational_event.get("available_at")
            state["operational_event_effective_at"] = operational_event.get("effective_at")
            if operational_event.get("status") == "accepted":
                state["trading_state"] = operational_event.get("trading_state") or TradingState.UNKNOWN.value
            else:
                state["trading_state"] = TradingState.UNKNOWN.value
                state.setdefault("event_reasons", []).append("operational_event_not_accepted")
        return state

    @classmethod
    def _effective_epoch_instrument_ids(
        cls,
        conn: sqlite3.Connection,
        *,
        cutoff: str,
        venue: str | None = None,
        official_code: str | None = None,
        event_rows_out: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    ) -> set[str]:
        """Select one effective identity epoch per venue/code at a cutoff.

        A later epoch can compete only after its immediate predecessor's latest
        cutoff-visible listing event is an accepted termination.  This keeps a
        future or not-yet-proven code reuse from displacing the old epoch and
        prevents an old high revision number from winning after reuse.
        """
        clauses = ["first_observed_at <= ?"]
        params: list[Any] = [cutoff]
        if venue:
            clauses.append("venue = ?")
            params.append(venue)
        if official_code:
            clauses.append("official_code = ?")
            params.append(official_code)
        joined_rows = [dict(row) for row in conn.execute(
            f"WITH event_rows AS ("
            "SELECT instrument_id, 'lifecycle' AS event_source, lifecycle_event_id, "
            "NULL AS operational_event_id, event_type, NULL AS trading_state, event_date, "
            "effective_at, available_at, ingested_at, source_reference, status, reason "
            "FROM universe_lifecycle_events "
            "UNION ALL "
            "SELECT instrument_id, 'operational' AS event_source, NULL AS lifecycle_event_id, "
            "operational_event_id, NULL AS event_type, trading_state, NULL AS event_date, "
            "effective_at, available_at, ingested_at, source_reference, status, reason "
            "FROM universe_operational_state_events) "
            "SELECT i.instrument_id, i.venue, i.official_code, i.identity_epoch, i.first_observed_at, "
            "e.event_source, e.lifecycle_event_id, e.operational_event_id, e.event_type, "
            "e.trading_state, e.event_date, e.effective_at, e.available_at, e.ingested_at, "
            "e.source_reference, e.status, e.reason "
            f"FROM universe_instruments i "
            f"LEFT JOIN event_rows e ON e.instrument_id = i.instrument_id "
            f"WHERE {' AND '.join('i.' + clause for clause in clauses)} "
            "ORDER BY i.venue, i.official_code, i.identity_epoch, e.available_at, e.ingested_at, "
            "COALESCE(e.lifecycle_event_id, e.operational_event_id)",
            params,
        ).fetchall()]
        anchors_by_id: dict[str, dict[str, Any]] = {}
        event_by_id: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in joined_rows:
            instrument_id = str(row["instrument_id"])
            anchors_by_id.setdefault(
                instrument_id,
                {
                    "instrument_id": instrument_id,
                    "venue": row["venue"],
                    "official_code": row["official_code"],
                    "identity_epoch": row["identity_epoch"],
                    "first_observed_at": row["first_observed_at"],
                },
            )
            event_id = row.get("lifecycle_event_id") or row.get("operational_event_id")
            if event_id:
                event = {
                    key: row[key]
                    for key in (
                        "event_source", "lifecycle_event_id", "operational_event_id", "event_type", "trading_state", "event_date", "effective_at",
                        "available_at", "ingested_at", "source_reference", "status", "reason",
                    )
                }
                channel = "lifecycle" if row.get("event_source") == "lifecycle" else "operational"
                existing_events = event_by_id.setdefault(
                    instrument_id, {"lifecycle": [], "operational": []}
                )[channel]
                if not any(
                    str(existing.get("lifecycle_event_id") or existing.get("operational_event_id")) == str(event_id)
                    for existing in existing_events
                ):
                    existing_events.append(event)
        anchors = list(anchors_by_id.values())
        if not anchors:
            return set()
        if event_rows_out is not None:
            event_rows_out.update(event_by_id)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in anchors:
            grouped.setdefault((str(row["venue"]), str(row["official_code"])), []).append(row)
        selected: set[str] = set()
        for rows in grouped.values():
            rows.sort(key=lambda row: int(row["identity_epoch"]))
            current = rows[0]
            for candidate in rows[1:]:
                if int(candidate["identity_epoch"]) != int(current["identity_epoch"]) + 1:
                    continue
                predecessor_events = event_by_id.get(str(current["instrument_id"]), {}).get("lifecycle", [])
                latest_listing = cls._latest_visible_event(
                    [row for row in predecessor_events if row.get("event_type") in {"listed", "terminated"}],
                    cutoff=cutoff,
                )
                if latest_listing and latest_listing.get("status") == "accepted" and latest_listing.get("event_type") == "terminated":
                    current = candidate
            selected.add(str(current["instrument_id"]))
        return selected

    @classmethod
    def _select_event_states_batch(
        cls,
        conn: sqlite3.Connection,
        *,
        instrument_ids: list[str],
        cutoff: str,
    ) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in instrument_ids))
        if not ids:
            return {}
        placeholders = cls._sql_placeholders(ids)
        lifecycle_by_id: dict[str, list[dict[str, Any]]] = {instrument_id: [] for instrument_id in ids}
        operational_by_id: dict[str, list[dict[str, Any]]] = {instrument_id: [] for instrument_id in ids}
        for row in conn.execute(
            f"""
            SELECT instrument_id, 'lifecycle' AS event_source,
                   lifecycle_event_id, NULL AS operational_event_id,
                   event_type, NULL AS trading_state, event_date, effective_at,
                   available_at, ingested_at, source_reference, status, reason
            FROM universe_lifecycle_events
            WHERE instrument_id IN ({placeholders})
            UNION ALL
            SELECT instrument_id, 'operational' AS event_source,
                   NULL AS lifecycle_event_id, operational_event_id,
                   NULL AS event_type, trading_state, NULL AS event_date, effective_at,
                   available_at, ingested_at, source_reference, status, reason
            FROM universe_operational_state_events
            WHERE instrument_id IN ({placeholders})
            """,
            ids + ids,
        ).fetchall():
            value = dict(row)
            instrument_id = str(value["instrument_id"])
            if value.get("event_source") == "lifecycle":
                lifecycle_by_id.setdefault(instrument_id, []).append(value)
            else:
                operational_by_id.setdefault(instrument_id, []).append(value)
        return {
            instrument_id: cls._compose_event_state(
                lifecycle_by_id.get(instrument_id, []),
                operational_by_id.get(instrument_id, []),
                cutoff=cutoff,
            )
            for instrument_id in ids
        }

    @classmethod
    def _apply_event_state_to_row(
        cls,
        row: dict[str, Any] | None,
        *,
        event_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for key in (
            "listing_status", "trading_state", "lifecycle_event_id",
            "lifecycle_event_type", "lifecycle_event_status",
            "lifecycle_event_available_at", "lifecycle_event_effective_at",
            "lifecycle_event_date", "operational_event_id",
            "operational_event_source", "operational_event_status",
            "operational_event_available_at", "operational_event_effective_at",
        ):
            if key in event_state:
                value[key] = event_state[key]
        if event_state.get("event_reasons"):
            value["event_reasons"] = list(event_state["event_reasons"])
        return value

    @staticmethod
    def _operational_where(alias: str = "r") -> str:
        """Visibility for the current/resource channel.

        A provider/transport/schema observation may have no source
        available_at. Its deterministic local observation boundary is the
        server-side ingested_at timestamp. This is intentionally a separate
        predicate: it can expose a blocked operational state after the
        observation, but it can never make that row a historical identity
        reference.
        """
        return f"({alias}.ingested_at IS NOT NULL AND {alias}.ingested_at <= ?)"

    @staticmethod
    def _reference(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "instrument_id": row.get("instrument_id"),
            "canonical_symbol": row.get("canonical_symbol"),
            "official_code": row.get("official_code"),
            "venue": row.get("venue"),
            "display_name": row.get("display_name"),
            "security_type": row.get("security_type", "unknown"),
            "listing_status": row.get("listing_status", "unknown"),
            "trading_state": row.get("trading_state", "unknown"),
            "membership_state": row.get("membership_state", "unknown"),
            "source_reference": row.get("source_reference"),
            "mapping_basis": row.get("mapping_basis"),
        }

    @classmethod
    def _safe_dto(cls, revision: dict[str, Any] | None, *, cutoff: str) -> dict[str, Any] | None:
        if not revision:
            return None
        freshness = {
            "freshness": revision.get("freshness_status", FreshnessStatus.UNKNOWN.value),
            "current_complete": bool(revision.get("current_complete", False)),
            "freshness_mode": revision.get("freshness_mode"),
            "availability_mode": revision.get("availability_mode"),
            "latest_visible_state": revision.get("status"),
            "reasons": [revision["reason"]] if revision.get("reason") else [],
        }
        return {
            "status": revision.get("public_status", "partial"),
            "status_policy_version": "universe_status_matrix_v1",
            "knowledge_cutoff_at": cutoff,
            "cutoff_policy": {"type": "aware_timestamp", "no_end_of_day_expansion": True},
            "identity_reference": cls._reference(revision),
            "operational_freshness": freshness,
            "effective": {
                "source_effective_date": revision.get("source_effective_date"),
                "source_effective_at": revision.get("source_effective_at"),
                "available_at": revision.get("available_at"),
                "ingested_at": revision.get("ingested_at"),
            },
            "provenance": {
                "resource_id": revision.get("resource_id"),
                "universe_revision_id": revision.get("universe_revision_id"),
                "instrument_revision_id": revision.get("instrument_revision_id"),
                "source_reference": revision.get("source_reference"),
                "parser_version": revision.get("parser_version"),
                "historical_reference": True,
                "operational_resource_id": revision.get("operational_resource_id"),
                "operational_revision_id": revision.get("operational_revision_id"),
                "operational_ingested_at": revision.get("operational_ingested_at"),
                "lifecycle_event_id": revision.get("lifecycle_event_id"),
                "lifecycle_event_type": revision.get("lifecycle_event_type"),
                "lifecycle_event_status": revision.get("lifecycle_event_status"),
                "lifecycle_event_available_at": revision.get("lifecycle_event_available_at"),
                "lifecycle_event_effective_at": revision.get("lifecycle_event_effective_at"),
                "lifecycle_event_date": revision.get("lifecycle_event_date"),
                "operational_event_id": revision.get("operational_event_id"),
                "operational_event_source": revision.get("operational_event_source"),
                "operational_event_status": revision.get("operational_event_status"),
                "operational_event_available_at": revision.get("operational_event_available_at"),
                "operational_event_effective_at": revision.get("operational_event_effective_at"),
            },
            "reasons": [revision["reason"]] if revision.get("reason") else [],
        }

    @staticmethod
    def _select_latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        return max(rows, key=lambda row: (
            int(row.get("revision_number") or 0),
            str(row.get("available_at") or ""),
            str(row.get("ingested_at") or ""),
            str(row.get("instrument_revision_id") or row.get("universe_revision_id") or ""),
        ))

    @staticmethod
    def _effective_current_complete(*, policy: sqlite3.Row | dict[str, Any],
                                    payload: dict[str, Any], status: str) -> bool:
        """Derive master completeness from policy and independently eligible evidence.

        The caller-supplied ``current_complete`` flag is intentionally ignored.
        Event and corroborating resources can contribute operational blockers,
        but can never establish master completeness.
        """
        # Freshness policy is registered/versioned evidence, not an ingestion
        # claim.  In particular, a caller must not promote a seeded master
        # whose cadence is still unknown by sending an official/licensed mode.
        freshness_mode = str(policy["freshness_mode"])
        freshness_status = str(payload.get("freshness_status") or FreshnessStatus.UNKNOWN.value)
        if freshness_mode == FreshnessMode.UNKNOWN_WITHOUT_OFFICIAL_CADENCE.value:
            freshness_status = FreshnessStatus.UNKNOWN.value
        return (
            status == "accepted"
            and str(policy["resource_role"]) == "master_snapshot"
            and str(policy["completeness_policy"]) == "accepted_master_complete"
            and freshness_mode in {
                FreshnessMode.OFFICIAL_CADENCE_WINDOW.value,
                FreshnessMode.LICENSED_REFERENCE.value,
            }
            and freshness_status == FreshnessStatus.CURRENT.value
            and bool(payload.get("coverage_complete", False))
        )

    @staticmethod
    def _effective_freshness_fields(*, policy: sqlite3.Row | dict[str, Any],
                                    payload: dict[str, Any], status: str) -> tuple[str, str]:
        """Return the registered freshness mode and its safe status.

        Resource policy rows are immutable registry evidence.  Payload modes
        are accepted as descriptive input only and are never persisted as a
        policy override.  An accepted master under the seeded
        ``unknown_without_official_cadence`` policy therefore remains
        explicitly unknown even when a caller claims ``current`` or an
        official cadence window.
        """
        mode = str(policy["freshness_mode"])
        supplied_status = str(payload.get("freshness_status") or FreshnessStatus.UNKNOWN.value)
        if status == "accepted" and mode == FreshnessMode.UNKNOWN_WITHOUT_OFFICIAL_CADENCE.value:
            return mode, FreshnessStatus.UNKNOWN.value
        return mode, supplied_status

    @classmethod
    def _latest_master_mappings(
        cls,
        conn: sqlite3.Connection,
        *,
        instrument_ids: list[str],
        cutoff: str,
    ) -> dict[str, dict[str, Any] | None]:
        """Return safe approved master mappings at one historical boundary.

        Corroborating/manual observations can enrich an existing anchored
        identity, but they cannot manufacture a ``.TW``/``.TWO`` mapping.  A
        latest accepted master row without a canonical mapping is therefore a
        deliberate fail-closed result rather than a reason to resurrect an
        older mapping.  Availability, publication evidence and revocation are
        evaluated with the same cutoff contract as historical references.
        """
        ids = list(dict.fromkeys(str(value) for value in instrument_ids))
        if not ids:
            return {}
        placeholders = cls._sql_placeholders(ids)
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.instrument_id, r.instrument_revision_id, r.canonical_symbol,
                   r.mapping_basis, r.revision_number, r.available_at, r.ingested_at
            FROM universe_instrument_revisions r
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id IN ({placeholders})
              AND r.status = 'accepted'
              AND p.resource_role = 'master_snapshot'
              AND {cls._cutoff_where('r')}
            ORDER BY r.instrument_id, r.revision_number DESC,
                     COALESCE(r.available_at,'' ) DESC, r.ingested_at DESC,
                     r.instrument_revision_id DESC
            """, ids + [cutoff, cutoff, cutoff, cutoff]
        ).fetchall()]
        excluded = cls._effective_excluded_instrument_revision_ids(
            conn, instrument_ids=ids, cutoff=cutoff,
        )
        candidates: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            candidates.setdefault(str(row["instrument_id"]), []).append(row)
        result: dict[str, dict[str, Any] | None] = {}
        for instrument_id in ids:
            blocked = excluded.get(instrument_id, set())
            safe = next(
                (
                    row for row in candidates.get(instrument_id, [])
                    if str(row["instrument_revision_id"]) not in blocked
                ),
                None,
            )
            result[instrument_id] = (
                {
                    "canonical_symbol": safe["canonical_symbol"],
                    "mapping_basis": safe.get("mapping_basis"),
                }
                if safe and safe.get("canonical_symbol")
                else None
            )
        return result

    @classmethod
    def _latest_master_mapping(
        cls, conn: sqlite3.Connection, *, instrument_id: str, cutoff: str,
    ) -> dict[str, Any] | None:
        return cls._latest_master_mappings(
            conn, instrument_ids=[instrument_id], cutoff=cutoff,
        ).get(str(instrument_id))

    @classmethod
    def _apply_effective_master_mapping(
        cls,
        conn: sqlite3.Connection,
        row: dict[str, Any],
        *,
        cutoff: str,
        master_mappings: dict[str, dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        """Project only a cutoff-safe master mapping onto non-master rows."""
        value = dict(row)
        if value.get("resource_role") == "master_snapshot":
            return value
        mapping = (
            master_mappings.get(str(value.get("instrument_id")))
            if master_mappings is not None
            else cls._latest_master_mapping(
                conn, instrument_id=str(value.get("instrument_id")), cutoff=cutoff,
            )
        )
        if mapping:
            value["canonical_symbol"] = mapping["canonical_symbol"]
            value["mapping_basis"] = mapping.get("mapping_basis")
        else:
            value["canonical_symbol"] = None
            value["mapping_basis"] = None
            if value.get("status") == "accepted" and not value.get("reason"):
                value["reason"] = "canonical_mapping_unverified"
        return value

    def _select_historical_reference(self, conn: sqlite3.Connection, *, instrument_id: str,
                                     cutoff: str) -> dict[str, Any] | None:
        """Select the safe identity/reference channel only.

        Accepted rows remain the reference across a later provider/partial/schema
        observation. A visible revoke that targets that row removes it from the
        eligible set; with no corrected accepted revision, no prior row is
        returned as a safe historical identity.
        """
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                   i.first_observed_at AS anchor_first_observed_at,
                   p.resource_role, p.completeness_policy,
                   p.freshness_mode AS policy_freshness_mode
            FROM universe_instrument_revisions r
            JOIN universe_instruments i ON i.instrument_id = r.instrument_id
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id = ? AND r.status = 'accepted' AND {self._cutoff_where('r')}
            ORDER BY r.revision_number DESC, COALESCE(r.available_at,'' ) DESC,
                     r.ingested_at DESC, r.instrument_revision_id DESC
            """, (instrument_id, cutoff, cutoff, cutoff, cutoff)
        ).fetchall()]
        master_mappings = self._latest_master_mappings(
            conn, instrument_ids=[instrument_id], cutoff=cutoff,
        )
        rows = [
            self._apply_effective_master_mapping(
                conn, row, cutoff=cutoff, master_mappings=master_mappings,
            )
            for row in rows
        ]
        event_state = self._select_event_states_batch(
            conn, instrument_ids=[instrument_id], cutoff=cutoff,
        ).get(str(instrument_id), {})
        rows = [
            self._apply_event_state_to_row(row, event_state=event_state) or row
            for row in rows
        ]
        excluded_by_id = self._effective_excluded_instrument_revision_ids(
            conn, instrument_ids=[instrument_id], cutoff=cutoff,
        )
        excluded_ids = excluded_by_id.get(str(instrument_id), set())
        for row in rows:
            parent = conn.execute(
                "SELECT logical_revision_key FROM universe_revisions WHERE universe_revision_id=?",
                (row.get("universe_revision_id"),),
            ).fetchone()
            row["logical_revision_key"] = parent[0] if parent else None
        if not rows:
            # Preserve the v1-compatible descriptive partial row when no
            # accepted identity exists. It is never considered complete and
            # remains visibly partial; provider/schema/revocation-only rows do
            # not receive this fallback.
            partial = conn.execute(
                f"""
                SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                       i.first_observed_at AS anchor_first_observed_at,
                       p.resource_role, p.completeness_policy,
                       p.freshness_mode AS policy_freshness_mode
                FROM universe_instrument_revisions r JOIN universe_instruments i ON i.instrument_id=r.instrument_id
                JOIN universe_resource_policies p ON p.resource_id = r.resource_id
                WHERE r.instrument_id=? AND r.status='partial' AND {self._cutoff_where('r')}
                ORDER BY r.revision_number DESC, COALESCE(r.available_at,'' ) DESC,
                         r.ingested_at DESC, r.instrument_revision_id DESC LIMIT 1
                """, (instrument_id, cutoff, cutoff, cutoff, cutoff)
            ).fetchone()
            value = self._row(partial)
            if value and not excluded_ids:
                value = self._apply_effective_master_mapping(
                    conn,
                    value,
                    cutoff=cutoff,
                    master_mappings=master_mappings,
                )
            if value and not excluded_ids:
                value = self._apply_event_state_to_row(value, event_state=event_state)
            if value and not excluded_ids:
                parent = conn.execute("SELECT logical_revision_key FROM universe_revisions WHERE universe_revision_id=?", (value.get("universe_revision_id"),)).fetchone()
                value["logical_revision_key"] = parent[0] if parent else None
            return value if not excluded_ids else None
        for row in rows:
            if str(row.get("instrument_revision_id")) not in excluded_ids:
                return row
        return None

    @staticmethod
    def _sql_placeholders(values: list[str]) -> str:
        return ",".join("?" for _ in values)

    def _select_historical_references_batch(
        self, conn: sqlite3.Connection, *, instrument_ids: list[str], cutoff: str,
        event_states: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any] | None]:
        """Select historical references for a page with bounded set queries.

        The single-item helper above remains for exact/resolve compatibility.
        List/search uses this batch form so reference, revoke and partial
        fallback reads are constant-query operations rather than N+1 lookups.
        """
        ids = list(dict.fromkeys(str(value) for value in instrument_ids))
        if not ids:
            return {}
        placeholders = self._sql_placeholders(ids)
        accepted_rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                   i.first_observed_at AS anchor_first_observed_at,
                   ur.logical_revision_key, p.resource_role, p.completeness_policy
            FROM universe_instrument_revisions r
            JOIN universe_instruments i ON i.instrument_id = r.instrument_id
            JOIN universe_revisions ur ON ur.universe_revision_id = r.universe_revision_id
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id IN ({placeholders})
              AND r.status = 'accepted' AND {self._cutoff_where('r')}
            ORDER BY r.instrument_id, r.revision_number DESC,
                     COALESCE(r.available_at,'') DESC, r.ingested_at DESC,
                     r.instrument_revision_id DESC
            """, ids + [cutoff, cutoff, cutoff, cutoff]
        ).fetchall()]
        accepted_by_id: dict[str, list[dict[str, Any]]] = {}
        for row in accepted_rows:
            accepted_by_id.setdefault(str(row["instrument_id"]), []).append(row)

        excluded_by_id = self._effective_excluded_instrument_revision_ids(
            conn, instrument_ids=ids, cutoff=cutoff,
        )
        master_mappings = self._latest_master_mappings(
            conn, instrument_ids=ids, cutoff=cutoff,
        )
        accepted_by_id = {
            instrument_id: [
                self._apply_effective_master_mapping(
                    conn, row, cutoff=cutoff, master_mappings=master_mappings,
                )
                for row in rows_for_instrument
            ]
            for instrument_id, rows_for_instrument in accepted_by_id.items()
        }

        partial_rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                   i.first_observed_at AS anchor_first_observed_at,
                   ur.logical_revision_key, p.resource_role, p.completeness_policy
            FROM universe_instrument_revisions r
            JOIN universe_instruments i ON i.instrument_id = r.instrument_id
            JOIN universe_revisions ur ON ur.universe_revision_id = r.universe_revision_id
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id IN ({placeholders})
              AND r.status = 'partial'
              AND {self._cutoff_where('r')}
            ORDER BY r.instrument_id, r.revision_number DESC,
                     COALESCE(r.available_at,'') DESC, r.ingested_at DESC,
                     r.instrument_revision_id DESC
            """, ids + [cutoff, cutoff, cutoff, cutoff]
        ).fetchall()]
        partial_by_id: dict[str, dict[str, Any]] = {}
        for row in partial_rows:
            instrument_id = str(row["instrument_id"])
            partial_by_id.setdefault(
                instrument_id,
                self._apply_effective_master_mapping(
                    conn, row, cutoff=cutoff, master_mappings=master_mappings,
                ),
            )

        result: dict[str, dict[str, Any] | None] = {}
        for instrument_id in ids:
            accepted = accepted_by_id.get(instrument_id, [])
            if accepted:
                excluded = excluded_by_id.get(instrument_id, set())
                result[instrument_id] = next(
                    (row for row in accepted if str(row.get("instrument_revision_id")) not in excluded),
                    None,
                )
            else:
                result[instrument_id] = (
                    None
                    if excluded_by_id.get(instrument_id)
                    else partial_by_id.get(instrument_id)
                )
        if event_states is None:
            event_states = self._select_event_states_batch(
                conn, instrument_ids=ids, cutoff=cutoff,
            )
        return {
            instrument_id: self._apply_event_state_to_row(
                row,
                event_state=event_states.get(instrument_id, {}),
            )
            for instrument_id, row in result.items()
        }

    def _select_operational_state(self, conn: sqlite3.Connection, *, instrument_id: str,
                                  cutoff: str, reference: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Select the actual latest operational/resource state at the same cutoff."""
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                   i.first_observed_at AS anchor_first_observed_at,
                   p.resource_role, p.completeness_policy,
                   p.freshness_mode AS policy_freshness_mode
            FROM universe_instrument_revisions r
            JOIN universe_instruments i ON i.instrument_id = r.instrument_id
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id = ? AND {self._operational_where('r')}
            ORDER BY r.revision_number DESC, COALESCE(r.available_at,'' ) DESC,
                     r.ingested_at DESC, r.instrument_revision_id DESC
            """, (instrument_id, cutoff)
        ).fetchall()]
        if reference and reference.get("resource_id"):
            resource_rows = [dict(row) for row in conn.execute(
                f"""
                SELECT ur.*, p.availability_mode AS policy_availability_mode,
                       p.freshness_mode AS policy_freshness_mode,
                       p.resource_role, p.completeness_policy, r.market
                FROM universe_revisions ur
                JOIN universe_resource_policies p ON p.resource_id = ur.resource_id
                JOIN data_resources r ON r.resource_id = ur.resource_id
                WHERE ur.resource_id = ? AND ur.logical_revision_key = ?
                  AND {self._operational_where('ur')}
                ORDER BY ur.revision_number DESC, COALESCE(ur.available_at,'' ) DESC,
                         ur.ingested_at DESC, ur.universe_revision_id DESC
                """, (reference["resource_id"], reference.get("logical_revision_key") or "", cutoff)
            ).fetchall()]
            for row in resource_rows:
                row["instrument_id"] = instrument_id
                row["venue"] = row.get("market") or reference.get("venue")
                row["official_code"] = reference.get("official_code")
                row["canonical_symbol"] = reference.get("canonical_symbol")
                row["identity_epoch"] = reference.get("identity_epoch")
                row["identity_binding_fingerprint"] = reference.get("identity_binding_fingerprint")
                row["availability_mode"] = row.get("policy_availability_mode")
                row["freshness_mode"] = row.get("policy_freshness_mode")
                row["freshness_status"] = row.get("freshness_status") or (
                    FreshnessStatus.CURRENT.value if row.get("status") == "accepted" and row.get("current_complete") else FreshnessStatus.BLOCKED.value
                )
                rows.append(row)
        return self._select_latest(rows)

    def _select_operational_states_batch(
        self, conn: sqlite3.Connection, *, instrument_ids: list[str],
        references: dict[str, dict[str, Any] | None], cutoff: str,
    ) -> dict[str, dict[str, Any] | None]:
        """Select operational state for a page using set-based reads."""
        ids = list(dict.fromkeys(str(value) for value in instrument_ids))
        if not ids:
            return {}
        placeholders = self._sql_placeholders(ids)
        states: dict[str, list[dict[str, Any]]] = {instrument_id: [] for instrument_id in ids}
        instrument_rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                   i.first_observed_at AS anchor_first_observed_at,
                   p.resource_role, p.completeness_policy,
                   p.freshness_mode AS policy_freshness_mode
            FROM universe_instrument_revisions r
            JOIN universe_instruments i ON i.instrument_id = r.instrument_id
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id IN ({placeholders}) AND {self._operational_where('r')}
            ORDER BY r.instrument_id, r.revision_number DESC,
                     COALESCE(r.available_at,'') DESC, r.ingested_at DESC,
                     r.instrument_revision_id DESC
            """, ids + [cutoff]
        ).fetchall()]
        for row in instrument_rows:
            states.setdefault(str(row["instrument_id"]), []).append(row)

        pair_to_ids: dict[tuple[str, str], list[str]] = {}
        for instrument_id in ids:
            reference = references.get(instrument_id)
            if not reference or not reference.get("resource_id") or not reference.get("logical_revision_key"):
                continue
            pair = (str(reference["resource_id"]), str(reference["logical_revision_key"]))
            pair_to_ids.setdefault(pair, []).append(instrument_id)
        if pair_to_ids:
            pair_predicates = " OR ".join("(ur.resource_id = ? AND ur.logical_revision_key = ?)" for _ in pair_to_ids)
            pair_params = [value for pair in pair_to_ids for value in pair]
            resource_rows = [dict(row) for row in conn.execute(
                f"""
                SELECT ur.*, p.availability_mode AS policy_availability_mode,
                       p.freshness_mode AS policy_freshness_mode,
                       p.resource_role, p.completeness_policy, dr.market AS resource_market
                FROM universe_revisions ur
                JOIN universe_resource_policies p ON p.resource_id = ur.resource_id
                JOIN data_resources dr ON dr.resource_id = ur.resource_id
                WHERE ({pair_predicates}) AND {self._operational_where('ur')}
                ORDER BY ur.resource_id, ur.logical_revision_key, ur.revision_number DESC,
                         COALESCE(ur.available_at,'') DESC, ur.ingested_at DESC,
                         ur.universe_revision_id DESC
                """, pair_params + [cutoff]
            ).fetchall()]
            for row in resource_rows:
                pair = (str(row["resource_id"]), str(row["logical_revision_key"]))
                for instrument_id in pair_to_ids.get(pair, []):
                    reference = references.get(instrument_id) or {}
                    value = dict(row)
                    value["instrument_id"] = instrument_id
                    value["venue"] = row.get("resource_market") or reference.get("venue")
                    value["official_code"] = reference.get("official_code")
                    value["canonical_symbol"] = reference.get("canonical_symbol")
                    value["identity_epoch"] = reference.get("identity_epoch")
                    value["identity_binding_fingerprint"] = reference.get("identity_binding_fingerprint")
                    value["availability_mode"] = row.get("policy_availability_mode")
                    value["freshness_mode"] = row.get("policy_freshness_mode")
                    value["freshness_status"] = row.get("freshness_status") or (
                        FreshnessStatus.CURRENT.value
                        if row.get("status") == "accepted" and row.get("current_complete")
                        else FreshnessStatus.BLOCKED.value
                    )
                    states.setdefault(instrument_id, []).append(value)
        return {instrument_id: self._select_latest(states.get(instrument_id, [])) for instrument_id in ids}

    def _compose_result(self, reference: dict[str, Any] | None, operational: dict[str, Any] | None,
                        *, cutoff: str, missing_reason: str = "instrument_not_found") -> dict[str, Any]:
        from src.services.universe_status_service import evaluate_universe_status
        if reference is None:
            reasons = [missing_reason]
            if operational and operational.get("status") == "revoked":
                reasons.append("source_revision_revoked_without_corrected_revision")
            result = evaluate_universe_status(None, reasons=tuple(reasons))
            dto = {
                "status": result["status"], "status_policy_version": result["status_policy_version"],
                "knowledge_cutoff_at": cutoff,
                "cutoff_policy": {"type": "aware_timestamp", "no_end_of_day_expansion": True},
                "identity_reference": None,
                "operational_freshness": {
                    "freshness": (operational or {}).get("freshness_status", FreshnessStatus.UNKNOWN.value),
                    "current_complete": False,
                    "latest_visible_state": (operational or {}).get("status"),
                    "freshness_mode": (operational or {}).get("freshness_mode") or (operational or {}).get("policy_freshness_mode"),
                    "reasons": reasons,
                },
                "reasons": reasons,
            }
            return dto
        op = operational or reference
        reasons: list[str] = []
        reasons.extend(str(reason) for reason in reference.get("event_reasons", []) if reason)
        status = str(op.get("status") or "unknown")
        completeness_source = op
        if status == "accepted" and op.get("resource_role") != "master_snapshot":
            # Accepted event/corroborating observations are neutral support.
            # They neither replace nor invalidate the eligible master state.
            completeness_source = reference
        reason_map = {
            "awaiting_review": "source_revision_awaiting_review",
            "schema_changed": "source_schema_review_required",
            "provider_error": "source_provider_error",
            "partial": "source_revision_partial",
            "revoked": "source_revision_revoked_without_corrected_revision",
        }
        if status in reason_map:
            reasons.append(reason_map[status])
        # A blocked operational attempt has no source publication instant by
        # design.  Unknown availability is actionable only for an otherwise
        # accepted identity fact; provider/partial observations remain
        # non-actionable partial health states.
        if status == "accepted" and (
            completeness_source.get("available_at") is None
            or str(completeness_source.get("available_at")) > cutoff
        ):
            reasons.append("availability_unproven")
        if op.get("reason"):
            reasons.append(str(op["reason"]))
        if reference.get("canonical_symbol") is None:
            reasons.append("canonical_mapping_unverified")
        policy_freshness_mode = completeness_source.get("policy_freshness_mode") or completeness_source.get("freshness_mode")
        freshness = completeness_source.get("freshness_status") or FreshnessStatus.UNKNOWN.value
        if (
            completeness_source.get("resource_role") == "master_snapshot"
            and policy_freshness_mode == FreshnessMode.UNKNOWN_WITHOUT_OFFICIAL_CADENCE.value
        ):
            # Re-evaluate legacy rows against the registered policy at read
            # time as well; persisted caller claims must never upgrade a
            # seeded unknown-cadence master.
            freshness = FreshnessStatus.UNKNOWN.value
        if status != "accepted" and freshness == FreshnessStatus.CURRENT.value:
            freshness = FreshnessStatus.BLOCKED.value
        if freshness == FreshnessStatus.STALE.value:
            reasons.append("freshness_stale")
        elif freshness == FreshnessStatus.UNKNOWN.value:
            reasons.append("freshness_unknown")
        current_complete = (
            bool(completeness_source.get("current_complete"))
            and status == "accepted"
            and freshness == FreshnessStatus.CURRENT.value
            and completeness_source.get("resource_role") == "master_snapshot"
            and completeness_source.get("completeness_policy") == "accepted_master_complete"
            and policy_freshness_mode in {
                FreshnessMode.OFFICIAL_CADENCE_WINDOW.value,
                FreshnessMode.LICENSED_REFERENCE.value,
            }
        )
        result = evaluate_universe_status(self._reference(reference), freshness=freshness,
                                          current_complete=current_complete, reasons=tuple(reasons))
        dto = self._safe_dto({**reference, **{
            "public_status": result["status"], "status": status,
            "freshness_status": freshness, "current_complete": current_complete,
            "reason": op.get("reason") or reference.get("reason"),
            "operational_revision_id": op.get("universe_revision_id"),
            "operational_resource_id": op.get("resource_id"),
            "operational_ingested_at": op.get("ingested_at"),
        }}, cutoff=cutoff)
        return {**dto, "reasons": list(dict.fromkeys(reasons))}

    def _find_revision(self, conn: sqlite3.Connection, *, instrument_id: str,
                       cutoff: str, current: bool = False) -> dict[str, Any] | None:
        # Kept as a compatibility helper; both historical and current calls are
        # cutoff-bound now. The public methods use the explicit dual channels.
        return self._select_operational_state(conn, instrument_id=instrument_id, cutoff=validate_knowledge_cutoff_at(cutoff))

    def _decorate(self, revision: dict[str, Any] | None, *, cutoff: str, current: bool) -> dict[str, Any]:
        return self._compose_result(revision, revision, cutoff=cutoff)

    def get_by_instrument_id(self, instrument_id: str, *, knowledge_cutoff_at: str,
                             current: bool = False) -> dict[str, Any]:
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        with self.read_transaction() as conn:
            reference = self._select_historical_reference(conn, instrument_id=instrument_id, cutoff=cutoff)
            operational = self._select_operational_state(conn, instrument_id=instrument_id, cutoff=cutoff, reference=reference)
            return self._compose_result(reference, operational, cutoff=cutoff)

    def get_instrument(self, instrument_id: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.get_by_instrument_id(instrument_id, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def get_by_canonical(self, canonical_symbol: str, *, knowledge_cutoff_at: str,
                         current: bool = False) -> dict[str, Any]:
        venue, code = parse_canonical_symbol(canonical_symbol)
        canonical = canonical_symbol_for(venue, code)
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        with self.read_transaction() as conn:
            effective_ids = self._effective_epoch_instrument_ids(
                conn, cutoff=cutoff, venue=venue.value, official_code=code,
            )
            if not effective_ids:
                return self._compose_result(None, None, cutoff=cutoff, missing_reason="instrument_not_found")
            instrument_id = sorted(effective_ids)[0]
            reference = self._select_historical_reference(conn, instrument_id=instrument_id, cutoff=cutoff)
            operational = self._select_operational_state(conn, instrument_id=instrument_id, cutoff=cutoff, reference=reference)
            # A canonical lookup is valid only when the effective historical
            # reference still carries the requested, cutoff-safe mapping.  A
            # stale persisted corroborating suffix must not resurrect a
            # revoked or not-yet-visible master mapping.
            if reference is None or reference.get("canonical_symbol") != canonical:
                return self._compose_result(
                    None,
                    operational,
                    cutoff=cutoff,
                    missing_reason="canonical_mapping_unverified",
                )
            return self._compose_result(reference, operational, cutoff=cutoff)

    def get_by_symbol(self, canonical_symbol: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.get_by_canonical(canonical_symbol, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def find_by_canonical_symbol(self, canonical_symbol: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.get_by_canonical(canonical_symbol, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def resolve(self, *, official_code: str, venue: UniverseVenue | str,
                knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        code = validate_official_code(official_code)
        venue_value = coerce_venue(venue)
        return self.get_by_canonical(canonical_symbol_for(venue_value, code), knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def resolve_by_code_venue(self, *, official_code: str, venue: UniverseVenue | str,
                              knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.resolve(official_code=official_code, venue=venue, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def list_instruments(self, *, knowledge_cutoff_at: str, query: str | None = None,
                         venue: UniverseVenue | str | None = None, security_type: str | None = None,
                         listing_status: ListingStatus | str | None = None, limit: int = 25,
                         cursor: str | None = None, current: bool = False) -> dict[str, Any]:
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit must be between 1 and 100")
        venue_value = coerce_venue(venue).value if venue else None
        listing_value = ListingStatus(str(listing_status).lower()).value if listing_status else None
        cursor_token: dict[str, Any] | None = None
        if cursor:
            try:
                token = json.loads(cursor)
                if (
                    not isinstance(token, dict)
                    or token.get("cutoff") != cutoff
                    or token.get("venue") != venue_value
                    or token.get("query") != (query or "")
                    or token.get("security_type") != (security_type or "")
                    or token.get("listing_status") != (listing_value or "")
                    or token.get("order") != "venue ASC, canonical_symbol ASC, instrument_id ASC"
                ):
                    raise ValueError("cursor_query_mismatch")
                cursor_token = token
            except (json.JSONDecodeError, TypeError, KeyError):
                raise ValueError("cursor_query_mismatch")
        with self.read_transaction() as conn:
            # The recursive edge CTE removes every visible accepted correction
            # ancestor and revoked target before ranking.  List membership,
            # filters, ordering and cursors therefore operate on the same
            # effective historical reference that the batch selector returns.
            epoch_event_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
            effective_epoch_ids = sorted(
                self._effective_epoch_instrument_ids(
                    conn, cutoff=cutoff, venue=venue_value,
                    event_rows_out=epoch_event_rows,
                )
            )
            epoch_event_states = {
                instrument_id: self._compose_event_state(
                    rows.get("lifecycle", []),
                    rows.get("operational", []),
                    cutoff=cutoff,
                )
                for instrument_id, rows in epoch_event_rows.items()
            }
            if listing_value and effective_epoch_ids:
                # Listing-status filters operate on the same composed event
                # state returned in each public item, not on the stale raw
                # revision column before lifecycle events are applied.
                effective_references = self._select_historical_references_batch(
                    conn,
                    instrument_ids=effective_epoch_ids,
                    cutoff=cutoff,
                    event_states=epoch_event_states,
                )
                effective_epoch_ids = [
                    instrument_id
                    for instrument_id in effective_epoch_ids
                    if (
                        effective_references.get(instrument_id)
                        and effective_references[instrument_id].get("listing_status") == listing_value
                    )
                ]
            if effective_epoch_ids:
                effective_epoch_predicate = (
                    f"r.instrument_id IN ({self._sql_placeholders(effective_epoch_ids)})"
                )
            else:
                effective_epoch_predicate = "0=1"
            cutoff_predicate = self._cutoff_where("r")
            filters: list[str] = []
            filter_params: list[Any] = []
            if venue_value:
                filters.append("anchor_venue = ?"); filter_params.append(venue_value)
            if security_type:
                filters.append("security_type = ?"); filter_params.append(security_type)
            # ``listing_value`` has already been applied to the composed
            # cutoff-visible lifecycle state above; filtering the raw revision
            # column here would incorrectly drop a termination overlay.
            if query:
                filters.append("(anchor_code LIKE ? OR effective_canonical_key LIKE ? OR COALESCE(display_name,'') LIKE ?)")
                term = f"%{query.strip().upper()}%"; filter_params += [term, term, term]
            cursor_clause = ""
            cursor_params: list[Any] = []
            if cursor_token is not None:
                cursor_clause = "(anchor_venue > ? OR (anchor_venue = ? AND effective_canonical_key > ?) OR (anchor_venue = ? AND effective_canonical_key = ? AND instrument_id > ?))"
                cursor_params = [
                    cursor_token.get("venue_key"), cursor_token.get("venue_key"), cursor_token.get("canonical_key") or "",
                    cursor_token.get("venue_key"), cursor_token.get("canonical_key") or "", cursor_token.get("instrument_id"),
                ]
            outer_clauses = [*filters]
            if cursor_clause:
                outer_clauses.append(cursor_clause)
            outer_where = " AND ".join(outer_clauses) if outer_clauses else "1=1"
            base_rows = [dict(row) for row in conn.execute(
                f"""
                WITH RECURSIVE visible_edges AS (
                    SELECT r.instrument_id, r.instrument_revision_id AS child_revision_id,
                           r.supersedes_revision_id AS parent_revision_id
                    FROM universe_instrument_revisions r
                    WHERE r.status IN ('accepted', 'revoked')
                      AND r.supersedes_revision_id IS NOT NULL
                      AND {cutoff_predicate}
                ), excluded(instrument_id, instrument_revision_id) AS (
                    SELECT instrument_id, parent_revision_id
                    FROM visible_edges
                    UNION
                    SELECT e.instrument_id, e.parent_revision_id
                    FROM visible_edges e
                    JOIN excluded x
                      ON x.instrument_id = e.instrument_id
                     AND x.instrument_revision_id = e.child_revision_id
                ), accepted_visible AS (
                    SELECT r.*, i.venue AS anchor_venue, i.official_code AS anchor_code,
                           i.identity_epoch, i.identity_binding_fingerprint,
                           p.resource_role, p.completeness_policy,
                           p.freshness_mode AS policy_freshness_mode
                    FROM universe_instrument_revisions r
                    JOIN universe_instruments i ON i.instrument_id = r.instrument_id
                    JOIN universe_resource_policies p ON p.resource_id = r.resource_id
                    WHERE r.status = 'accepted'
                      AND {effective_epoch_predicate}
                      AND {cutoff_predicate}
                      AND NOT EXISTS (
                          SELECT 1 FROM excluded x
                          WHERE x.instrument_id = r.instrument_id
                            AND x.instrument_revision_id = r.instrument_revision_id
                      )
                ), master_ranked AS (
                    SELECT a.instrument_id, a.canonical_symbol, a.mapping_basis,
                           ROW_NUMBER() OVER (
                               PARTITION BY a.instrument_id
                               ORDER BY a.revision_number DESC,
                                        COALESCE(a.available_at,'') DESC,
                                        a.ingested_at DESC, a.instrument_revision_id DESC
                           ) AS master_rn
                    FROM accepted_visible a
                    WHERE a.resource_role = 'master_snapshot'
                ), latest_master AS (
                    SELECT instrument_id, canonical_symbol, mapping_basis
                    FROM master_ranked
                    WHERE master_rn = 1
                ), ranked AS (
                    SELECT a.*,
                           CASE WHEN a.resource_role = 'master_snapshot'
                                THEN a.canonical_symbol
                                ELSE lm.canonical_symbol
                           END AS effective_canonical_symbol,
                           COALESCE(
                               CASE WHEN a.resource_role = 'master_snapshot'
                                    THEN a.canonical_symbol
                                    ELSE lm.canonical_symbol
                               END, ''
                           ) AS effective_canonical_key,
                           ROW_NUMBER() OVER (
                               PARTITION BY a.instrument_id
                                ORDER BY a.revision_number DESC, COALESCE(a.available_at,'') DESC,
                                         a.ingested_at DESC, a.instrument_revision_id DESC
                           ) AS rn
                    FROM accepted_visible a
                    LEFT JOIN latest_master lm ON lm.instrument_id = a.instrument_id
                ), latest AS (
                    SELECT * FROM ranked WHERE rn = 1
                )
                SELECT * FROM latest
                WHERE {outer_where}
                ORDER BY anchor_venue ASC, effective_canonical_key ASC, instrument_id ASC
                LIMIT ?
                """, ([cutoff] * 4) + effective_epoch_ids + ([cutoff] * 4) + filter_params + cursor_params + [int(limit) + 1]
            ).fetchall()]
            selected = base_rows
            page_rows = selected[:int(limit)]
            instrument_ids = [str(row["instrument_id"]) for row in page_rows]
            references = self._select_historical_references_batch(
                conn,
                instrument_ids=instrument_ids,
                cutoff=cutoff,
                event_states=epoch_event_states,
            )
            operational = self._select_operational_states_batch(
                conn, instrument_ids=instrument_ids, references=references, cutoff=cutoff,
            )
            items = [
                self._compose_result(references.get(instrument_id), operational.get(instrument_id), cutoff=cutoff)
                for instrument_id in instrument_ids
            ]
            scoped_venues = [venue_value] if venue_value else ["TWSE", "TPEX"]
            resource_status = self._latest_resource_statuses(conn, venues=scoped_venues, cutoff=cutoff)
        next_cursor = None
        if len(selected) > limit and items:
            last = selected[int(limit) - 1]
            next_cursor = json.dumps({"cutoff": cutoff, "venue": venue_value, "query": query or "",
                                      "security_type": security_type or "", "listing_status": listing_value or "",
                                      "order": "venue ASC, canonical_symbol ASC, instrument_id ASC",
                                      "venue_key": last.get("anchor_venue"), "canonical_key": last.get("effective_canonical_key") or "",
                                      "instrument_id": last.get("instrument_id")}, separators=(",", ":"))
        scoped = [venue_value] if venue_value else ["TWSE", "TPEX"]
        per_venue = {}
        for v in scoped:
            item_status = self._venue_status(items, v)
            source_status = resource_status.get(v, "insufficient_data")
            precedence = {"needs_human_input": 3, "partial": 2, "available": 1, "insufficient_data": 0}
            per_venue[v] = source_status if precedence.get(source_status, 0) >= precedence.get(item_status, 0) else item_status
        statuses = list(per_venue.values())
        overall = "needs_human_input" if "needs_human_input" in statuses else ("partial" if "partial" in statuses or (items and any(i["status"] == "partial" for i in items)) else ("available" if statuses and all(s == "available" for s in statuses) else "insufficient_data"))
        return {"status": overall, "status_policy_version": "universe_status_matrix_v1",
                "knowledge_cutoff_at": cutoff, "cutoff_policy": {"type": "aware_timestamp", "no_end_of_day_expansion": True},
                "items": items, "per_venue_status": per_venue, "next_cursor": next_cursor,
                "order": "venue ASC, canonical_symbol ASC, instrument_id ASC", "limit": int(limit)}

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return self.list_instruments(**kwargs)

    @staticmethod
    def _venue_status(items: list[dict[str, Any]], venue: str) -> str:
        rows = [item for item in items if (item.get("identity_reference") or {}).get("venue") == venue]
        if not rows:
            return "insufficient_data"
        if any(item["status"] == "needs_human_input" for item in rows): return "needs_human_input"
        if any(item["status"] == "partial" for item in rows): return "partial"
        if any(item["status"] == "available" for item in rows): return "available"
        return "insufficient_data"

    def _latest_resource_statuses(self, conn: sqlite3.Connection, *, venues: list[str], cutoff: str) -> dict[str, str]:
        """Compose resource health per venue without cross-resource masking.

        Each logical feed gets its own latest visible operational revision first.
        Only then are those resource states reduced to the venue using the
        locked status precedence.  A newer healthy resource or logical feed
        therefore cannot hide a blocker from a different feed in the same
        venue.
        """
        scoped = list(dict.fromkeys(str(value) for value in venues))
        if not scoped:
            return {}
        placeholders = self._sql_placeholders(scoped)
        rows = [dict(row) for row in conn.execute(
            f"""
            WITH ranked AS (
                SELECT ur.*, p.freshness_mode AS policy_freshness_mode,
                       p.resource_role, p.completeness_policy,
                       dr.market AS resource_market,
                       ROW_NUMBER() OVER (
                           PARTITION BY dr.market, ur.resource_id, ur.logical_revision_key
                            ORDER BY ur.revision_number DESC,
                                     COALESCE(ur.available_at,'') DESC,
                                     ur.ingested_at DESC,
                                     ur.universe_revision_id DESC
                       ) AS rn
                FROM universe_revisions ur
                JOIN data_resources dr ON dr.resource_id = ur.resource_id
                JOIN universe_resource_policies p ON p.resource_id = ur.resource_id
                WHERE dr.market IN ({placeholders}) AND {self._operational_where('ur')}
            )
            SELECT * FROM ranked WHERE rn = 1
            ORDER BY resource_market, resource_id
            """, scoped + [cutoff]
        ).fetchall()]
        statuses_by_venue: dict[str, list[str]] = {venue: [] for venue in scoped}
        master_statuses_by_venue: dict[str, list[str]] = {venue: [] for venue in scoped}
        for row in rows:
            venue = str(row["resource_market"])
            resource_status = self._resource_status_from_row(row, cutoff=cutoff)
            if resource_status is not None:
                statuses_by_venue.setdefault(venue, []).append(resource_status)
            if row.get("resource_role") == "master_snapshot" and resource_status is not None:
                master_statuses_by_venue.setdefault(venue, []).append(resource_status)
        precedence = {"needs_human_input": 3, "partial": 2, "available": 1, "insufficient_data": 0}
        result: dict[str, str] = {}
        for venue in scoped:
            blockers = [status for status in statuses_by_venue.get(venue, []) if status in {"needs_human_input", "partial"}]
            if blockers:
                result[venue] = max(blockers, key=lambda status: precedence[status])
                continue
            masters = master_statuses_by_venue.get(venue, [])
            result[venue] = "available" if "available" in masters else "insufficient_data"
        return result

    @staticmethod
    def _resource_status_from_row(latest: dict[str, Any] | None, *, cutoff: str) -> str | None:
        if latest is None:
            return "insufficient_data"
        resource_role = str(latest.get("resource_role") or "")
        status = str(latest.get("status"))
        if resource_role == "corroborating_identity_observation":
            return None
        if status in {"awaiting_review", "schema_changed", "revoked"}:
            return "needs_human_input"
        if resource_role != "master_snapshot" and status == "accepted":
            return None
        freshness_mode = latest.get("policy_freshness_mode") or latest.get("freshness_mode")
        freshness_status = latest.get("freshness_status")
        current_complete = bool(latest.get("current_complete"))
        if (
            resource_role == "master_snapshot"
            and freshness_mode == FreshnessMode.UNKNOWN_WITHOUT_OFFICIAL_CADENCE.value
        ):
            freshness_status = FreshnessStatus.UNKNOWN.value
            current_complete = False
        if (
            status != "accepted"
            or latest.get("available_at") is None
            or str(latest.get("available_at")) > cutoff
            or freshness_status in {None, "unknown", "stale", "blocked"}
        ):
            return "partial"
        if latest.get("completeness_policy") != "accepted_master_complete":
            return "partial"
        if not current_complete:
            return "partial"
        return "available"

    # ----- Guarded mutation paths used by operator/CLI ingestion -----
    def _require_context(self, context: UniverseOperatorContext | None) -> UniverseOperatorContext:
        return self.guard.require_enabled(context)

    @staticmethod
    def _provenance(conn: sqlite3.Connection, *, resource_id: str, logical_revision_key: str,
                    payload: dict[str, Any], normalized_hash: str) -> dict[str, Any]:
        resource = conn.execute(
            "SELECT provider_id, logical_resource_key, parser_id, parser_version, schema_version, storage_policy FROM data_resources WHERE resource_id=?",
            (resource_id,),
        ).fetchone()
        if resource is None:
            raise ValueError("universe_resource_not_registered")
        parser_version = str(payload.get("parser_version") or resource["parser_version"])
        if parser_version != str(resource["parser_version"]):
            raise ValueError("parser_evidence_mismatch")
        query_dimensions = payload.get("query_dimensions") or {}
        if not isinstance(query_dimensions, dict):
            raise ValueError("query_dimensions must be an object")
        contract_key = str(payload.get("source_contract_key") or query_dimensions.get("source_contract_key") or resource["logical_resource_key"])
        observed_fields = query_dimensions.get("observed_row_fields")
        if not isinstance(observed_fields, list) or not all(isinstance(item, str) for item in observed_fields):
            # Direct operator payloads may contain one normalized source row.
            # Derive evidence from that observed row rather than a static
            # resource identity; collector-produced envelopes override this
            # fallback through query_dimensions.
            metadata_fields = {
                "venue", "official_code", "canonical_symbol", "mapping_basis", "security_type",
                "display_name", "listing_status", "trading_state", "membership_state",
                "source_effective_date", "source_effective_at", "source_published_at",
                "fetched_at", "received_at", "first_observed_at", "available_at", "ingested_at",
                "status", "reason", "freshness_mode", "freshness_status", "current_complete",
                "coverage_complete", "effective_from", "effective_to", "supersedes_revision_id",
                "source_reference", "publication_evidence_id", "raw_resource_revision_id",
                "raw_payload_sha256", "normalized_payload_sha256", "query_dimensions",
                "source_contract_key", "schema_fingerprint", "parser_version", "source_record_reference",
            }
            observed_fields = sorted(str(key) for key in payload if key not in metadata_fields)
        else:
            observed_fields = sorted(set(observed_fields))
        required_groups = query_dimensions.get("required_field_groups")
        if not isinstance(required_groups, dict):
            required_groups = {}
        envelope = str(query_dimensions.get("source_envelope") or "row")
        schema_identity = {
            "logical_resource_key": contract_key,
            "schema_version": resource["schema_version"],
            "envelope": envelope,
            "required_field_groups": required_groups,
            "observed_row_fields": observed_fields,
        }
        derived_schema = hashlib.sha256(_json(schema_identity).encode()).hexdigest()
        supplied_schema = payload.get("schema_fingerprint")
        if supplied_schema and str(supplied_schema).lower() != derived_schema:
            raise ValueError("schema_evidence_mismatch")
        raw_id = str(payload.get("raw_resource_revision_id") or "").strip()
        raw_hash = payload.get("raw_payload_sha256")
        if not raw_id or raw_hash is None or not str(raw_hash).strip():
            raise UniverseRawProvenanceRequired()
        raw_hash = str(raw_hash).strip().lower()
        if len(raw_hash) != 64 or any(ch not in "0123456789abcdef" for ch in raw_hash):
            raise ValueError("raw_payload_sha256 must be a SHA-256 digest")
        raw = conn.execute("SELECT * FROM raw_resource_revisions WHERE raw_resource_revision_id=?", (raw_id,)).fetchone()
        if raw is None:
            raise UniverseRawProvenanceRequired()
        if raw["resource_id"] != resource_id:
            raise ValueError("raw_resource_revision_mismatch")
        if raw["raw_payload_sha256"].lower() != raw_hash:
            raise ValueError("raw_payload_sha256_mismatch")
        source_record_reference = str(payload.get("source_record_reference") or f"{resource_id}:{logical_revision_key}").strip()
        if not source_record_reference:
            raise ValueError("source_record_reference is required")
        parser_evidence = hashlib.sha256(_json({"parser_id": resource["parser_id"], "parser_version": parser_version}).encode()).hexdigest()
        return {
            "raw_resource_revision_id": raw_id,
            "raw_payload_sha256": raw_hash,
            "normalized_payload_sha256": normalized_hash,
            "query_dimensions_json": _json(query_dimensions),
            "source_record_reference": source_record_reference,
            "parser_version": parser_version,
            "schema_fingerprint": derived_schema,
            "parser_evidence_fingerprint": parser_evidence,
            "schema_evidence_fingerprint": hashlib.sha256(_json(schema_identity).encode()).hexdigest(),
        }

    def allocate_instrument(self, *, venue: UniverseVenue | str, official_code: str,
                            source_identity: str, first_observed_at: str, source_reference: str,
                            context: UniverseOperatorContext | None = None,
                            continuity_proven: bool = False) -> dict[str, Any]:
        ctx = self._require_context(context)
        venue_value = coerce_venue(venue)
        code = validate_official_code(official_code)
        first_observed = normalize_universe_timestamp(first_observed_at, "first_observed_at")
        source_identity = str(source_identity).strip()
        source_reference = str(source_reference).strip()
        if not source_identity or not source_reference:
            raise ValueError("source_identity and source_reference are required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            binding = identity_binding_fingerprint(venue_value, code, 1, source_identity)
            existing = conn.execute("SELECT * FROM universe_instruments WHERE identity_binding_fingerprint=?", (binding,)).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            same = conn.execute("SELECT * FROM universe_instruments WHERE venue=? AND official_code=? ORDER BY identity_epoch DESC LIMIT 1", (venue_value.value, code)).fetchone()
            epoch = 1
            if same:
                if continuity_proven:
                    return {**dict(same), "created": False, "continuity_reused": True}
                lifecycle_rows = [dict(row) for row in conn.execute(
                    "SELECT * FROM universe_lifecycle_events WHERE instrument_id=?",
                    (same["instrument_id"],),
                ).fetchall()]
                latest_listing = self._latest_visible_event(
                    [row for row in lifecycle_rows if row.get("event_type") in {"listed", "terminated"}],
                    cutoff=utc_now_timestamp(),
                )
                if not (
                    latest_listing
                    and latest_listing.get("status") == "accepted"
                    and latest_listing.get("event_type") == "terminated"
                ):
                    raise UniverseIdentityCollision("identity_collision")
                epoch = int(same["identity_epoch"]) + 1
            binding = identity_binding_fingerprint(venue_value, code, epoch, source_identity)
            instrument_id = f"uinstr_{binding[:24]}"
            conn.execute("INSERT INTO universe_instruments VALUES (?,?,?,?,?,?,?,?,?,?)", (instrument_id, venue_value.value, code, epoch, binding, first_observed, source_reference, source_identity, None, utc_now_timestamp()))
            write_universe_audit(ctx, command="allocate_instrument", outcome="created", venue=venue_value.value,
                                 channel="identity", reason="identity_anchor_created")
            return {**dict(conn.execute("SELECT * FROM universe_instruments WHERE instrument_id=?", (instrument_id,)).fetchone()), "created": True, "actor_id": ctx.actor_id}

    def add_resource_revision(self, *, resource_id: str, logical_revision_key: str,
                              revision_number: int, payload: dict[str, Any],
                              context: UniverseOperatorContext | None = None,
                              idempotency_key: str | None = None) -> dict[str, Any]:
        """Persist a resource/provider observation even when it has zero rows."""
        ctx = self._require_context(context)
        if idempotency_key is None or not str(idempotency_key).strip():
            raise UniverseIdempotencyRequired()
        key = str(idempotency_key).strip()
        fetched_at = normalize_universe_timestamp(payload["fetched_at"], "fetched_at")
        received_at = normalize_universe_timestamp(payload["received_at"], "received_at")
        ingested_at = normalize_universe_timestamp(payload["ingested_at"], "ingested_at")
        available_at = normalize_universe_timestamp(payload["available_at"], "available_at") if payload.get("available_at") else None
        first_observed_at = normalize_universe_timestamp(payload["first_observed_at"], "first_observed_at") if payload.get("first_observed_at") else None
        source_published_at = payload.get("source_published_at")
        if source_published_at and "T" in str(source_published_at):
            source_published_at = normalize_universe_timestamp(str(source_published_at), "source_published_at")
        if payload.get("source_effective_date"):
            parse_source_temporal(str(payload["source_effective_date"]), "source_effective_date")
        normalized_hash = payload_fingerprint(payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            policy = conn.execute(
                """SELECT p.availability_mode, p.enabled, p.resource_role, p.freshness_mode,
                          p.completeness_policy,
                          r.market, r.logical_resource_key
                   FROM universe_resource_policies p JOIN data_resources r ON r.resource_id=p.resource_id
                   WHERE p.resource_id=?""", (resource_id,)
            ).fetchone()
            if policy is None or not int(policy["enabled"]):
                raise ValueError("universe_resource_not_registered")
            provenance = self._provenance(conn, resource_id=resource_id, logical_revision_key=logical_revision_key,
                                          payload=payload, normalized_hash=normalized_hash)
            old = conn.execute("SELECT * FROM universe_ingestion_idempotency WHERE idempotency_key=?", (key,)).fetchone()
            if old:
                if old["payload_fingerprint"] != normalized_hash:
                    raise UniverseIdempotencyConflict("idempotency_key_reused")
                row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (old["universe_revision_id"],)).fetchone()
                return {**dict(row), "created": False, "idempotent": True}
            existing = conn.execute("SELECT * FROM universe_ingestion_idempotency WHERE resource_id=? AND payload_fingerprint=?", (resource_id, normalized_hash)).fetchone()
            if existing:
                conn.execute("INSERT INTO universe_ingestion_idempotency VALUES (?,?,?,?,?,?)", (key, normalized_hash, resource_id, existing["universe_revision_id"], ctx.actor_id, ingested_at))
                row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (existing["universe_revision_id"],)).fetchone()
                return {**dict(row), "created": False, "idempotent": True}
            previous = conn.execute("SELECT * FROM universe_revisions WHERE resource_id=? AND logical_revision_key=? ORDER BY revision_number DESC LIMIT 1", (resource_id, logical_revision_key)).fetchone()
            supersedes = payload.get("supersedes_revision_id")
            if previous and supersedes != previous["universe_revision_id"]:
                raise ValueError("corrected universe revision must supersede the latest revision")
            if previous is None and supersedes is not None:
                raise ValueError("first universe revision cannot supersede another revision")
            status = str(payload.get("status", "accepted"))
            reason = payload.get("reason")
            if policy["availability_mode"] == AvailabilityMode.MANUAL_PUBLICATION_EVIDENCE_REQUIRED.value and not payload.get("publication_evidence_id"):
                status, reason = "awaiting_review", "manual_publication_evidence_required"
            current_complete = self._effective_current_complete(
                policy=policy, payload=payload, status=status,
            )
            freshness_mode, freshness_status = self._effective_freshness_fields(
                policy=policy, payload=payload, status=status,
            )
            revision_id = f"urev_{uuid.uuid4().hex}"
            conn.execute("""INSERT INTO universe_revisions
                (universe_revision_id,resource_id,logical_revision_key,revision_number,raw_resource_revision_id,source_published_at,source_effective_date,fetched_at,received_at,first_observed_at,available_at,ingested_at,status,reason,payload_sha256,normalized_payload_sha256,raw_payload_sha256,query_dimensions_json,source_record_reference,parser_evidence_fingerprint,schema_evidence_fingerprint,schema_fingerprint,parser_version,source_reference,publication_evidence_id,supersedes_revision_id,availability_mode,freshness_mode,freshness_status,current_complete,coverage_complete)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                revision_id, resource_id, logical_revision_key, int(revision_number), provenance["raw_resource_revision_id"], source_published_at,
                payload.get("source_effective_date"), fetched_at, received_at, first_observed_at, available_at, ingested_at,
                status, reason, normalized_hash, provenance["normalized_payload_sha256"], provenance["raw_payload_sha256"], provenance["query_dimensions_json"],
                provenance["source_record_reference"], provenance["parser_evidence_fingerprint"], provenance["schema_evidence_fingerprint"], provenance["schema_fingerprint"],
                provenance["parser_version"], payload.get("source_reference"), payload.get("publication_evidence_id"), supersedes,
                policy["availability_mode"], freshness_mode, freshness_status,
                int(current_complete), int(bool(payload.get("coverage_complete", False)))))
            conn.execute("INSERT INTO universe_ingestion_idempotency VALUES (?,?,?,?,?,?)", (key, normalized_hash, resource_id, revision_id, ctx.actor_id, ingested_at))
            row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (revision_id,)).fetchone()
            write_universe_audit(ctx, command="add_resource_revision", outcome="created", resource_id=resource_id,
                                 resource_role=policy["resource_role"], venue=policy["market"],
                                 channel="operational", reason=str(status))
            return {**dict(row), "created": True, "idempotent": False}

    def add_revision(self, *, instrument_id: str, resource_id: str, logical_revision_key: str,
                     revision_number: int, payload: dict[str, Any], context: UniverseOperatorContext | None = None,
                     idempotency_key: str | None = None, actor_id: str | None = None) -> dict[str, Any]:
        ctx = self._require_context(context)
        if idempotency_key is None or not str(idempotency_key).strip():
            raise UniverseIdempotencyRequired()
        idempotency_key = str(idempotency_key).strip()
        if actor_id is not None and str(actor_id).strip() != ctx.actor_id:
            raise UniverseOperatorContextRequired("actor_id")
        actor = ctx.actor_id
        fetched_at = normalize_universe_timestamp(payload["fetched_at"], "fetched_at")
        received_at = normalize_universe_timestamp(payload["received_at"], "received_at")
        ingested_at = normalize_universe_timestamp(payload["ingested_at"], "ingested_at")
        available_at = normalize_universe_timestamp(payload["available_at"], "available_at") if payload.get("available_at") else None
        first_observed_at = normalize_universe_timestamp(payload["first_observed_at"], "first_observed_at") if payload.get("first_observed_at") else None
        source_published_at = payload.get("source_published_at")
        if source_published_at and "T" in str(source_published_at):
            source_published_at = normalize_universe_timestamp(str(source_published_at), "source_published_at")
        if payload.get("source_effective_date"):
            parse_source_temporal(str(payload["source_effective_date"]), "source_effective_date")
        source_effective_at = payload.get("source_effective_at")
        if source_effective_at:
            source_effective_at = normalize_universe_timestamp(str(source_effective_at), "source_effective_at")
        effective_from = payload.get("effective_from")
        effective_to = payload.get("effective_to")
        if effective_from:
            effective_from = normalize_universe_timestamp(str(effective_from), "effective_from")
        if effective_to:
            effective_to = normalize_universe_timestamp(str(effective_to), "effective_to")
        if effective_from and effective_to and effective_from >= effective_to:
            raise ValueError("effective_from must be earlier than effective_to")
        fingerprint = payload_fingerprint(payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            policy = conn.execute(
                """SELECT p.availability_mode, p.enabled, p.resource_role, p.freshness_mode,
                          p.completeness_policy, r.market, r.logical_resource_key
                   FROM universe_resource_policies p
                   JOIN data_resources r ON r.resource_id = p.resource_id
                   WHERE p.resource_id=?""",
                (resource_id,),
            ).fetchone()
            if policy is None or not int(policy["enabled"]):
                raise ValueError("universe_resource_not_registered")
            provenance = self._provenance(
                conn, resource_id=resource_id, logical_revision_key=logical_revision_key,
                payload=payload, normalized_hash=fingerprint,
            )
            payload_venue = coerce_venue(payload["venue"])
            payload_code = validate_official_code(payload["official_code"])
            if str(policy["market"]).upper() != payload_venue.value:
                raise ValueError("universe_resource_venue_mismatch")
            status = payload.get("status", "accepted")
            reason = payload.get("reason")
            publication_evidence_id = payload.get("publication_evidence_id")
            if policy["availability_mode"] == AvailabilityMode.MANUAL_PUBLICATION_EVIDENCE_REQUIRED.value and not publication_evidence_id:
                status = "awaiting_review"
                reason = "manual_publication_evidence_required"
            current_complete = self._effective_current_complete(
                policy=policy, payload=payload, status=str(status),
            )
            freshness_mode, freshness_status = self._effective_freshness_fields(
                policy=policy, payload=payload, status=str(status),
            )
            if idempotency_key:
                old = conn.execute("SELECT * FROM universe_ingestion_idempotency WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if old:
                    if old["payload_fingerprint"] != fingerprint:
                        raise UniverseIdempotencyConflict("idempotency_key_reused")
                    row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (old["universe_revision_id"],)).fetchone()
                    return {**dict(row), "created": False, "idempotent": True}
            # A different idempotency key carrying the same payload must bind to
            # the existing immutable revision rather than attempting a new
            # revision (and therefore must not require a new supersession link).
            existing = conn.execute(
                "SELECT * FROM universe_ingestion_idempotency WHERE resource_id=? AND payload_fingerprint=?",
                (resource_id, fingerprint),
            ).fetchone()
            if existing:
                if idempotency_key:
                    try:
                        conn.execute(
                            "INSERT INTO universe_ingestion_idempotency VALUES (?,?,?,?,?,?)",
                            (idempotency_key, fingerprint, resource_id, existing["universe_revision_id"], actor, ingested_at),
                        )
                    except sqlite3.IntegrityError:
                        raise UniverseIdempotencyConflict("idempotency_key_reused") from None
                row = conn.execute(
                    "SELECT * FROM universe_revisions WHERE universe_revision_id=?",
                    (existing["universe_revision_id"],),
                ).fetchone()
                return {**dict(row), "created": False, "idempotent": True}
            previous = conn.execute(
                "SELECT * FROM universe_revisions WHERE resource_id=? AND logical_revision_key=? ORDER BY revision_number DESC LIMIT 1",
                (resource_id, logical_revision_key),
            ).fetchone()
            supersedes = payload.get("supersedes_revision_id")
            if previous and supersedes != previous["universe_revision_id"]:
                raise ValueError("corrected universe revision must supersede the latest revision")
            if previous is None and supersedes is not None:
                raise ValueError("first universe revision cannot supersede another revision")
            instrument_supersedes = None
            if supersedes is not None:
                parent = conn.execute(
                    """
                    SELECT ir.instrument_revision_id, ir.instrument_id,
                           ir.resource_id, ur.logical_revision_key, ir.venue
                    FROM universe_instrument_revisions ir
                    JOIN universe_revisions ur
                      ON ur.universe_revision_id = ir.universe_revision_id
                    WHERE ir.universe_revision_id = ? AND ir.instrument_id = ?
                    ORDER BY ir.instrument_revision_id DESC
                    LIMIT 1
                    """,
                    (supersedes, instrument_id),
                ).fetchone()
                if parent is None:
                    foreign_parent = conn.execute(
                        """
                        SELECT ir.instrument_id
                        FROM universe_instrument_revisions ir
                        WHERE ir.universe_revision_id = ?
                        LIMIT 1
                        """,
                        (supersedes,),
                    ).fetchone()
                    if foreign_parent is not None:
                        raise ValueError("supersedes revision belongs to another instrument")
                    raise ValueError("supersedes revision has no normalized instrument row")
                if (
                    str(parent["resource_id"]) != str(resource_id)
                    or str(parent["logical_revision_key"]) != str(logical_revision_key)
                ):
                    raise ValueError("supersedes revision is outside the logical source chain")
                if str(parent["venue"]) != payload_venue.value:
                    raise ValueError("supersedes revision belongs to another venue")
                instrument_supersedes = str(parent["instrument_revision_id"])
            mapping_basis = payload.get("mapping_basis") if policy["resource_role"] == "master_snapshot" else None
            if policy["resource_role"] == "master_snapshot":
                canonical_symbol = payload.get("canonical_symbol") or canonical_symbol_for(payload_venue, payload_code)
                mapping_basis = mapping_basis or "approved_resource_scope"
            else:
                # Non-master observations may only carry forward a mapping
                # already established by an effective approved master row.
                # Caller-supplied canonical symbols are intentionally ignored.
                # Carry-forward is bounded by the corroborating observation's
                # knowledge boundary.  The observation must still have an
                # explicit available instant; the server ingestion timestamp
                # is when that observation became known to the repository.
                master_mapping = (
                    self._latest_master_mapping(
                        conn, instrument_id=instrument_id, cutoff=ingested_at,
                    )
                    if status == "accepted" and available_at is not None
                    else None
                )
                canonical_symbol = master_mapping["canonical_symbol"] if master_mapping else None
                mapping_basis = master_mapping.get("mapping_basis") if master_mapping else None
                if status == "accepted" and canonical_symbol is None:
                    reason = "canonical_mapping_unverified"
            if canonical_symbol:
                try:
                    canonical_venue, canonical_code = parse_canonical_symbol(canonical_symbol)
                except ValueError:
                    canonical_venue = canonical_code = None
                if canonical_venue is None or canonical_venue is not payload_venue or canonical_code != payload_code:
                    canonical_symbol = None
                    if status == "accepted":
                        reason = "canonical_mapping_unverified"
            listing_status = payload.get("listing_status", ListingStatus.UNKNOWN.value)
            trading_state = payload.get("trading_state", TradingState.UNKNOWN.value)
            membership_state = payload.get("membership_state", MembershipState.UNKNOWN.value)
            if status != "accepted":
                listing_status = ListingStatus.UNKNOWN.value
                trading_state = TradingState.UNKNOWN.value
                membership_state = MembershipState.PARTIAL.value if status == "partial" else MembershipState.BLOCKED.value
            revision_id = f"urev_{uuid.uuid4().hex}"
            conn.execute("""INSERT INTO universe_revisions
                    (universe_revision_id,resource_id,logical_revision_key,revision_number,raw_resource_revision_id,source_published_at,source_effective_date,fetched_at,received_at,first_observed_at,available_at,ingested_at,status,reason,payload_sha256,normalized_payload_sha256,raw_payload_sha256,query_dimensions_json,source_record_reference,parser_evidence_fingerprint,schema_evidence_fingerprint,schema_fingerprint,parser_version,source_reference,publication_evidence_id,supersedes_revision_id,availability_mode,freshness_mode,freshness_status,current_complete,coverage_complete)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    revision_id, resource_id, logical_revision_key, int(revision_number), provenance["raw_resource_revision_id"], source_published_at, payload.get("source_effective_date"), fetched_at, received_at, first_observed_at, available_at, ingested_at, status, reason, fingerprint, provenance["normalized_payload_sha256"], provenance["raw_payload_sha256"], provenance["query_dimensions_json"], provenance["source_record_reference"], provenance["parser_evidence_fingerprint"], provenance["schema_evidence_fingerprint"], provenance["schema_fingerprint"], provenance["parser_version"], payload.get("source_reference"), publication_evidence_id, payload.get("supersedes_revision_id"), policy["availability_mode"], freshness_mode, freshness_status, int(current_complete), int(bool(payload.get("coverage_complete", False)))))
            conn.execute("""INSERT INTO universe_instrument_revisions
                    (instrument_revision_id,instrument_id,universe_revision_id,resource_id,revision_number,venue,official_code,canonical_symbol,mapping_basis,security_type,display_name,listing_status,trading_state,membership_state,source_effective_date,source_effective_at,source_published_at,first_observed_at,received_at,fetched_at,available_at,ingested_at,availability_mode,freshness_mode,freshness_status,current_complete,coverage_complete,status,reason,source_reference,payload_sha256,normalized_payload_sha256,raw_payload_sha256,raw_resource_revision_id,query_dimensions_json,source_record_reference,parser_evidence_fingerprint,schema_evidence_fingerprint,schema_fingerprint,parser_version,effective_from,effective_to,supersedes_revision_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    f"uirev_{uuid.uuid4().hex}", instrument_id, revision_id, resource_id, int(revision_number), payload_venue.value, payload_code, canonical_symbol, mapping_basis, payload.get("security_type","unknown"), payload.get("display_name"), listing_status, trading_state, membership_state, payload.get("source_effective_date"), source_effective_at, source_published_at, first_observed_at, received_at, fetched_at, available_at, ingested_at, policy["availability_mode"], freshness_mode, freshness_status, int(current_complete), int(bool(payload.get("coverage_complete", False))), status, reason, payload.get("source_reference"), fingerprint, provenance["normalized_payload_sha256"], provenance["raw_payload_sha256"], provenance["raw_resource_revision_id"], provenance["query_dimensions_json"], provenance["source_record_reference"], provenance["parser_evidence_fingerprint"], provenance["schema_evidence_fingerprint"], provenance["schema_fingerprint"], provenance["parser_version"], effective_from, effective_to, instrument_supersedes))
            if idempotency_key:
                try:
                    conn.execute("INSERT INTO universe_ingestion_idempotency VALUES (?,?,?,?,?,?)", (idempotency_key, fingerprint, resource_id, revision_id, actor, ingested_at))
                except sqlite3.IntegrityError:
                    raise UniverseIdempotencyConflict("idempotency_key_reused") from None
            row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (revision_id,)).fetchone()
            write_universe_audit(ctx, command="add_revision", outcome="created", resource_id=resource_id,
                                 resource_role=policy["resource_role"], venue=payload_venue.value,
                                 channel="historical_and_operational", reason=str(status))
            return {**dict(row), "created": existing is None, "idempotent": False}

    def add_lifecycle_event(self, *, instrument_id: str, event_type: str, available_at: str,
                            ingested_at: str, source_reference: str, reason: str,
                            context: UniverseOperatorContext | None = None, event_date: str | None = None,
                            effective_at: str | None = None, status: str = "accepted") -> dict[str, Any]:
        ctx = self._require_context(context)
        if event_type not in {"listed", "terminated", "resumed", "successor"}:
            raise ValueError("unsupported lifecycle event")
        available = normalize_universe_timestamp(available_at, "available_at")
        ingested = normalize_universe_timestamp(ingested_at, "ingested_at")
        effective = normalize_universe_timestamp(effective_at, "effective_at") if effective_at else None
        normalized_event_date = None
        if event_date:
            try:
                normalized_event_date = date.fromisoformat(str(event_date)).isoformat()
            except ValueError as exc:
                raise ValueError("event_date must be an ISO-8601 date") from exc
        if status not in {"accepted", "revoked", "awaiting_review"}:
            raise ValueError("unsupported lifecycle event status")
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"ulife_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (event_id, instrument_id, event_type, normalized_event_date, effective, available, ingested, source_reference.strip(), status, reason.strip()))
            write_universe_audit(ctx, command="add_lifecycle_event", outcome="created",
                                 channel="historical", reason=event_type)
            return {**dict(conn.execute("SELECT * FROM universe_lifecycle_events WHERE lifecycle_event_id=?", (event_id,)).fetchone()), "actor_id": ctx.actor_id}

    def add_operational_event(self, *, instrument_id: str, trading_state: str, available_at: str,
                              ingested_at: str, source_reference: str, reason: str,
                              context: UniverseOperatorContext | None = None, effective_at: str | None = None,
                              status: str = "accepted") -> dict[str, Any]:
        ctx = self._require_context(context)
        if trading_state not in {"normal", "suspended", "altered", "periodic", "managed", "unknown"}:
            raise ValueError("unsupported trading_state")
        available = normalize_universe_timestamp(available_at, "available_at")
        ingested = normalize_universe_timestamp(ingested_at, "ingested_at")
        effective = normalize_universe_timestamp(effective_at, "effective_at") if effective_at else None
        if status not in {"accepted", "revoked", "awaiting_review"}:
            raise ValueError("unsupported operational event status")
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"uoper_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_operational_state_events VALUES (?,?,?,?,?,?,?,?,?)",
                         (event_id, instrument_id, trading_state, effective, available, ingested, source_reference.strip(), status, reason.strip()))
            write_universe_audit(ctx, command="add_operational_event", outcome="created",
                                 channel="operational", reason=trading_state)
            return {**dict(conn.execute("SELECT * FROM universe_operational_state_events WHERE operational_event_id=?", (event_id,)).fetchone()), "actor_id": ctx.actor_id}

    def add_alias_event(self, *, from_instrument_id: str, to_instrument_id: str, alias_code: str,
                        alias_venue: UniverseVenue | str, alias_type: str, available_at: str,
                        ingested_at: str, source_reference: str, reason: str,
                        context: UniverseOperatorContext | None = None, effective_at: str | None = None) -> dict[str, Any]:
        ctx = self._require_context(context)
        if from_instrument_id == to_instrument_id:
            raise ValueError("alias event must link distinct instrument anchors")
        if alias_type not in {"successor", "previous_code", "official_alias"}:
            raise ValueError("unsupported alias_type")
        venue = coerce_venue(alias_venue)
        code = validate_official_code(alias_code)
        available = normalize_universe_timestamp(available_at, "available_at")
        ingested = normalize_universe_timestamp(ingested_at, "ingested_at")
        effective = normalize_universe_timestamp(effective_at, "effective_at") if effective_at else None
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"ualias_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_identity_alias_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (event_id, from_instrument_id, to_instrument_id, code, venue.value, alias_type, effective, available, ingested, source_reference.strip(), reason.strip()))
            write_universe_audit(ctx, command="add_alias_event", outcome="created", venue=venue.value,
                                 channel="identity", reason=alias_type)
            return {**dict(conn.execute("SELECT * FROM universe_identity_alias_events WHERE alias_event_id=?", (event_id,)).fetchone()), "actor_id": ctx.actor_id}


class UniverseIdentityRepository:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseRepository | None = None,
                 guard: UniverseWriteGuard | None = None):
        self.repository = repository or UniverseRepository(db_path, guard=guard)

    def allocate(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.allocate_instrument(**kwargs)

    def get_or_create(self, **kwargs: Any) -> dict[str, Any]:
        return self.allocate(**kwargs)

    def resolve(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.resolve(**kwargs)


class UniverseIngestionRepository:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseRepository | None = None,
                 guard: UniverseWriteGuard | None = None):
        self.repository = repository or UniverseRepository(db_path, guard=guard)

    def add_revision(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.add_revision(**kwargs)

    def add_resource_revision(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.add_resource_revision(**kwargs)


__all__ = [
    "UniverseIdentityCollision", "UniverseIdentityRepository", "UniverseIdempotencyConflict",
    "UniverseIdempotencyRequired", "UniverseRawProvenanceRequired",
    "UniverseIngestionRepository", "UniverseRepository", "UniverseStorageUnavailable",
]
