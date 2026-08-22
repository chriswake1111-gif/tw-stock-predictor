"""Read-only/as-of and guarded immutable persistence for Universe Foundation."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
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

    @staticmethod
    def _latest_master_mapping(conn: sqlite3.Connection, *, instrument_id: str) -> dict[str, Any] | None:
        """Return the latest effective approved master mapping, if any.

        Corroborating/manual observations can enrich an existing anchored
        identity, but they cannot manufacture a ``.TW``/``.TWO`` mapping.  A
        latest accepted master row without a canonical mapping is therefore a
        deliberate fail-closed result rather than a reason to resurrect an
        older mapping.
        """
        rows = [dict(row) for row in conn.execute(
            """
            SELECT r.universe_revision_id, r.canonical_symbol, r.mapping_basis
            FROM universe_instrument_revisions r
            JOIN universe_resource_policies p ON p.resource_id = r.resource_id
            WHERE r.instrument_id = ?
              AND r.status = 'accepted'
              AND p.resource_role = 'master_snapshot'
            ORDER BY r.revision_number DESC, COALESCE(r.available_at,'' ) DESC,
                     r.ingested_at DESC, r.instrument_revision_id DESC
            """, (instrument_id,)
        ).fetchall()]
        if not rows:
            return None
        revoked = {
            str(row[0]) for row in conn.execute(
                """
                SELECT supersedes_revision_id
                FROM universe_instrument_revisions
                WHERE instrument_id = ? AND status = 'revoked'
                  AND supersedes_revision_id IS NOT NULL
                """, (instrument_id,)
            ).fetchall()
        }
        for row in rows:
            if str(row.get("universe_revision_id")) in revoked:
                continue
            if row.get("canonical_symbol"):
                return {
                    "canonical_symbol": row["canonical_symbol"],
                    "mapping_basis": row.get("mapping_basis"),
                }
            return None
        return None

    def _select_historical_reference(self, conn: sqlite3.Connection, *, instrument_id: str,
                                     cutoff: str) -> dict[str, Any] | None:
        """Select the safe identity/reference channel only.

        Accepted rows remain the reference across a later provider/partial/schema
        observation. A visible revoke that targets that row removes it from the
        eligible set; the public composition may still expose it as a historical
        prior reference while the operational channel reports the revoke.
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
                WHERE r.instrument_id=? AND r.status='partial' AND r.canonical_symbol IS NOT NULL AND {self._cutoff_where('r')}
                ORDER BY r.revision_number DESC, COALESCE(r.available_at,'' ) DESC,
                         r.ingested_at DESC, r.instrument_revision_id DESC LIMIT 1
                """, (instrument_id, cutoff, cutoff, cutoff, cutoff)
            ).fetchone()
            value = self._row(partial)
            if value:
                parent = conn.execute("SELECT logical_revision_key FROM universe_revisions WHERE universe_revision_id=?", (value.get("universe_revision_id"),)).fetchone()
                value["logical_revision_key"] = parent[0] if parent else None
            return value
        visible_revokes = [dict(row) for row in conn.execute(
            f"""
            SELECT r.universe_revision_id, r.supersedes_revision_id, r.ingested_at
            FROM universe_instrument_revisions r
            WHERE r.instrument_id = ? AND r.status = 'revoked' AND {self._cutoff_where('r')}
            """, (instrument_id, cutoff, cutoff, cutoff, cutoff)
        ).fetchall()]
        revoked_ids = {
            row.get("supersedes_revision_id") for row in visible_revokes
            if row.get("supersedes_revision_id")
        }
        for row in rows:
            if row.get("universe_revision_id") not in revoked_ids:
                return row
        # Keep a prior immutable identity available for a human-visible revoke;
        # _compose_result marks it needs_human_input and never current_complete.
        return rows[0]

    @staticmethod
    def _sql_placeholders(values: list[str]) -> str:
        return ",".join("?" for _ in values)

    def _select_historical_references_batch(
        self, conn: sqlite3.Connection, *, instrument_ids: list[str], cutoff: str
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

        revoke_rows = [dict(row) for row in conn.execute(
            f"""
            SELECT r.instrument_id, r.supersedes_revision_id
            FROM universe_instrument_revisions r
            WHERE r.instrument_id IN ({placeholders})
              AND r.status = 'revoked' AND {self._cutoff_where('r')}
            """, ids + [cutoff, cutoff, cutoff, cutoff]
        ).fetchall()]
        revoked_by_id: dict[str, set[str]] = {}
        for row in revoke_rows:
            superseded = row.get("supersedes_revision_id")
            if superseded:
                revoked_by_id.setdefault(str(row["instrument_id"]), set()).add(str(superseded))

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
              AND r.status = 'partial' AND r.canonical_symbol IS NOT NULL
              AND {self._cutoff_where('r')}
            ORDER BY r.instrument_id, r.revision_number DESC,
                     COALESCE(r.available_at,'') DESC, r.ingested_at DESC,
                     r.instrument_revision_id DESC
            """, ids + [cutoff, cutoff, cutoff, cutoff]
        ).fetchall()]
        partial_by_id: dict[str, dict[str, Any]] = {}
        for row in partial_rows:
            partial_by_id.setdefault(str(row["instrument_id"]), row)

        result: dict[str, dict[str, Any] | None] = {}
        for instrument_id in ids:
            accepted = accepted_by_id.get(instrument_id, [])
            if accepted:
                revoked = revoked_by_id.get(instrument_id, set())
                result[instrument_id] = next(
                    (row for row in accepted if row.get("universe_revision_id") not in revoked),
                    accepted[0],
                )
            else:
                result[instrument_id] = partial_by_id.get(instrument_id)
        return result

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
            candidates = [dict(row) for row in conn.execute(
                f"""
                SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                       i.first_observed_at AS anchor_first_observed_at
                FROM universe_instrument_revisions r
                JOIN universe_instruments i ON i.instrument_id = r.instrument_id
                WHERE i.venue = ? AND i.official_code = ? AND r.canonical_symbol = ? AND {self._cutoff_where('r')}
                ORDER BY r.revision_number DESC, COALESCE(r.available_at,'' ) DESC,
                         r.ingested_at DESC, r.instrument_revision_id DESC
                """, (venue.value, code, canonical, cutoff, cutoff, cutoff, cutoff)
            ).fetchall()]
            if not candidates:
                return self._compose_result(None, None, cutoff=cutoff, missing_reason="instrument_not_found")
            instrument_id = candidates[0]["instrument_id"]
            reference = self._select_historical_reference(conn, instrument_id=instrument_id, cutoff=cutoff)
            operational = self._select_operational_state(conn, instrument_id=instrument_id, cutoff=cutoff, reference=reference)
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
            # Select the latest safe accepted reference first.  Search/list
            # predicates must not change which revision is authoritative for
            # an instrument; otherwise an older matching revision can be
            # resurrected when the current name/type/status no longer
            # matches the request.
            source_clauses = [f"{self._cutoff_where('r')}", "r.status = 'accepted'"]
            source_params: list[Any] = [cutoff, cutoff, cutoff, cutoff]
            filters: list[str] = []
            filter_params: list[Any] = []
            if venue_value:
                filters.append("anchor_venue = ?"); filter_params.append(venue_value)
            if security_type:
                filters.append("security_type = ?"); filter_params.append(security_type)
            if listing_value:
                filters.append("listing_status = ?"); filter_params.append(listing_value)
            if query:
                filters.append("(anchor_code LIKE ? OR canonical_key LIKE ? OR COALESCE(display_name,'') LIKE ?)")
                term = f"%{query.strip().upper()}%"; filter_params += [term, term, term]
            cursor_clause = ""
            cursor_params: list[Any] = []
            if cursor_token is not None:
                cursor_clause = "(anchor_venue > ? OR (anchor_venue = ? AND canonical_key > ?) OR (anchor_venue = ? AND canonical_key = ? AND instrument_id > ?))"
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
                WITH ranked AS (
                    SELECT r.*, i.venue AS anchor_venue, i.official_code AS anchor_code,
                           i.identity_epoch, i.identity_binding_fingerprint,
                           COALESCE(r.canonical_symbol,'') AS canonical_key,
                           ROW_NUMBER() OVER (
                               PARTITION BY r.instrument_id
                                ORDER BY r.revision_number DESC, COALESCE(r.available_at,'') DESC,
                                         r.ingested_at DESC, r.instrument_revision_id DESC
                           ) AS rn
                    FROM universe_instrument_revisions r
                    JOIN universe_instruments i ON i.instrument_id = r.instrument_id
                    WHERE {' AND '.join(source_clauses)}
                ), latest AS (
                    SELECT * FROM ranked WHERE rn = 1
                )
                SELECT * FROM latest
                WHERE {outer_where}
                ORDER BY anchor_venue ASC, canonical_key ASC, instrument_id ASC
                LIMIT ?
                """, source_params + filter_params + cursor_params + [int(limit) + 1]
            ).fetchall()]
            selected = base_rows
            page_rows = selected[:int(limit)]
            instrument_ids = [str(row["instrument_id"]) for row in page_rows]
            references = self._select_historical_references_batch(conn, instrument_ids=instrument_ids, cutoff=cutoff)
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
                                      "venue_key": last.get("anchor_venue"), "canonical_key": last.get("canonical_symbol") or "",
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
                terminated = conn.execute("SELECT 1 FROM universe_lifecycle_events WHERE instrument_id=? AND event_type='terminated' AND status='accepted' LIMIT 1", (same["instrument_id"],)).fetchone()
                if not terminated:
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
            mapping_basis = payload.get("mapping_basis") if policy["resource_role"] == "master_snapshot" else None
            if policy["resource_role"] == "master_snapshot":
                canonical_symbol = payload.get("canonical_symbol") or canonical_symbol_for(payload_venue, payload_code)
                mapping_basis = mapping_basis or "approved_resource_scope"
            else:
                # Non-master observations may only carry forward a mapping
                # already established by an effective approved master row.
                # Caller-supplied canonical symbols are intentionally ignored.
                master_mapping = self._latest_master_mapping(conn, instrument_id=instrument_id)
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
            previous_instrument = conn.execute(
                "SELECT instrument_revision_id FROM universe_instrument_revisions WHERE instrument_id=? ORDER BY revision_number DESC LIMIT 1",
                (instrument_id,),
            ).fetchone()
            instrument_supersedes = previous_instrument["instrument_revision_id"] if previous_instrument else None
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
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"ulife_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (event_id, instrument_id, event_type, event_date, effective, available, ingested, source_reference.strip(), status, reason.strip()))
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
