"""Set-based, query-only Phase 15 EOD coverage projection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.domain.eod_coverage import (
    CoverageItemKind,
    CoverageRequest,
    CoverageSourceState,
    CoverageStatus,
    DenominatorMembership,
    normalize_reason_codes,
    source_scope_for_venue,
)
from src.domain.eod_close import normalize_decimal_text
from src.repositories.eod_close_repository import EodCloseRepository


_SOURCE_BLOCKING_STATES = frozenset({
    "provider_error", "schema_changed", "blocked", "revoked", "rejected",
})
_CLASSIFICATION_EXCLUDED_TYPES = (
    "etf", "etn", "特別股", "優先股", "權證", "權利證書", "認股權憑證",
    "認購權證", "認售權證", "存託憑證", "臺灣存託憑證", "tdr",
    "depositary receipt", "受益證券", "指數股票型基金",
)


def _numeric_value_valid(value: Any, allow_zero: int) -> int:
    """Expose Phase 14's decimal parser to the set-based SQLite projection."""

    _, parsed, state = normalize_decimal_text(value)
    if state != "valid" or parsed is None:
        return 0
    return int(parsed >= 0 if allow_zero else parsed > 0)


def _stable_public_key(*values: Any) -> str:
    """Build a bounded deterministic tie-breaker without exposing DB IDs."""

    material = "\x1f".join("" if value is None else str(value) for value in values)
    return hashlib.sha256(
        b"eod-coverage-stable-v1:" + material.encode("utf-8")
    ).hexdigest()


def _timestamp_leq(value: Any, boundary: Any) -> int:
    """Compare stored ISO timestamps semantically, including cutoff ties."""

    try:
        left = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(boundary).replace("Z", "+00:00"))
        if left.tzinfo is None or right.tzinfo is None:
            return 0
        return int(left.astimezone(timezone.utc) <= right.astimezone(timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return 0


def _revision_d_state(
    source_effective_date: Any,
    source_effective_at: Any,
    effective_from: Any,
    effective_to: Any,
    target_date: Any,
) -> int:
    """Classify a K-visible identity revision against the requested D date.

    The Universe write contract permits date-only or aware timestamps for the
    source effective date, while the remaining interval fields are aware
    timestamps.  A missing boundary preserves the established legacy
    behavior.  An explicit boundary on D, malformed evidence, or conflicting
    boundaries is conservative and unresolved; a boundary wholly after D (or
    an interval already ended before D) is deterministically not applicable.
    """

    def local_date(value: Any, *, allow_date: bool) -> date:
        if value is None:
            raise ValueError("missing boundary")
        text = str(value).strip()
        if not text:
            raise ValueError("blank boundary")
        if "T" not in text and "t" not in text:
            if not allow_date:
                raise ValueError("timestamp required")
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00").replace("z", "+00:00")
        )
        if parsed.tzinfo is None:
            raise ValueError("aware timestamp required")
        return parsed.astimezone(timezone(timedelta(hours=8))).date()

    try:
        target = date.fromisoformat(str(target_date).strip())
        start_dates: list[date] = []
        if source_effective_date is not None:
            start_dates.append(local_date(source_effective_date, allow_date=True))
        for value in (source_effective_at, effective_from):
            if value is not None:
                start_dates.append(local_date(value, allow_date=False))
        end_date = (
            local_date(effective_to, allow_date=False)
            if effective_to is not None
            else None
        )
    except (TypeError, ValueError, OverflowError):
        return -1

    if any(boundary == target for boundary in start_dates) or end_date == target:
        return -1

    start_after = any(boundary > target for boundary in start_dates)
    start_before = any(boundary < target for boundary in start_dates)
    end_before = end_date is not None and end_date < target
    if start_after and (start_before or end_before):
        return -1
    if start_after or end_before:
        return 0
    return 1


@dataclass(frozen=True)
class CoverageProjection:
    source: dict[str, Any]
    aggregate: dict[str, Any]
    items: tuple[dict[str, Any], ...]


class EodCoverageRepository:
    """Read the complete diagnostic projection in one SQLite snapshot.

    The common CTE is used by both the aggregate and page statements.  The
    aggregate is deliberately reduced by SQL, while the item query uses a
    tuple keyset and a bounded look-ahead.  No public Universe list or
    per-symbol query is used as the denominator.
    """

    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        storage: EodCloseRepository | None = None,
    ) -> None:
        self.storage = storage or EodCloseRepository(db_path)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _source_state(row: dict[str, Any] | None) -> tuple[str, str]:
        if row is None:
            return CoverageSourceState.UNKNOWN.value, "no_exact_D"
        status = str(row.get("status") or "unknown")
        if status in _SOURCE_BLOCKING_STATES:
            if status == "revoked":
                return CoverageSourceState.BLOCKED.value, "source_revoke_without_replacement"
            return CoverageSourceState.BLOCKED.value, status
        if status in {"unknown", "insufficient_data"}:
            return CoverageSourceState.UNKNOWN.value, status
        if row.get("coverage_state") == "partial":
            if (
                str(row.get("coverage_proof_type") or "").strip()
                and str(row.get("coverage_proof_reference") or "").strip()
            ):
                return CoverageSourceState.PARTIAL.value, "source_partial"
            return CoverageSourceState.UNKNOWN.value, "source_partial_unproven"
        if status == "partial":
            if (
                str(row.get("coverage_proof_type") or "").strip()
                and str(row.get("coverage_proof_reference") or "").strip()
            ):
                return CoverageSourceState.PARTIAL.value, "source_partial"
            return CoverageSourceState.UNKNOWN.value, "source_partial_unproven"
        if int(row.get("row_count") or 0) == 0:
            return CoverageSourceState.UNKNOWN.value, "source_empty"
        if status == "available":
            return CoverageSourceState.USABLE.value, "source_usable"
        return CoverageSourceState.UNKNOWN.value, status

    @staticmethod
    def _safe_invalid_binding(row: dict[str, Any], target_date: str) -> bool:
        """Accept only an explicit existing query-dimension binding.

        The Phase 14 parser normally rejects malformed dates before a stored
        snapshot exists.  This branch therefore remains conservative: an
        opaque source reference or a mere mismatch is not treated as causal.
        ``query_dimensions_json`` is an existing Phase 14 field and is used
        only when it explicitly names the caller's target date.
        """

        if row.get("source_trade_date_status") != "invalid":
            return False
        try:
            dimensions = json.loads(str(row.get("query_dimensions_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(dimensions, dict):
            return False
        for key in ("source_trade_date", "requested_source_trade_date", "target_trade_date"):
            if dimensions.get(key) == target_date:
                return True
        return False

    @staticmethod
    def _source_order_key(row: dict[str, Any]) -> tuple[str, str, int, str, str]:
        """Apply the locked post-lineage source tie-break order."""

        try:
            revision_number = int(row.get("revision_number") or 0)
        except (TypeError, ValueError):
            revision_number = 0
        return (
            str(row.get("available_at") or ""),
            str(row.get("ingested_at") or ""),
            revision_number,
            str(row.get("source_snapshot_id") or ""),
            str(row.get("normalized_payload_sha256") or ""),
        )

    def _source_context(
        self,
        conn: sqlite3.Connection,
        request: CoverageRequest,
    ) -> dict[str, Any]:
        visible_rows = conn.execute(
            """
            WITH RECURSIVE
            source_visible_rows AS (
                SELECT s.*
                FROM eod_close_source_snapshots s
                WHERE s.resource_id = ?
                  AND s.source_scope = ?
                  AND s.available_at IS NOT NULL
                  AND eod_timestamp_leq(s.available_at, ?) = 1
                  AND s.ingested_at IS NOT NULL
                  AND eod_timestamp_leq(s.ingested_at, ?) = 1
            ),
            source_edges AS (
                SELECT child.source_snapshot_id AS child_snapshot_id,
                       child.supersedes_source_snapshot_id AS parent_snapshot_id
                FROM source_visible_rows child
                WHERE child.supersedes_source_snapshot_id IS NOT NULL
            ),
            source_excluded(source_snapshot_id) AS (
                SELECT parent_snapshot_id
                FROM source_edges
                UNION
                SELECT edge.parent_snapshot_id
                FROM source_edges edge
                JOIN source_excluded excluded
                  ON excluded.source_snapshot_id = edge.child_snapshot_id
            )
            SELECT visible.*
            FROM source_visible_rows visible
            WHERE NOT EXISTS (
                SELECT 1
                FROM source_excluded excluded
                WHERE excluded.source_snapshot_id = visible.source_snapshot_id
            )
            """,
            (
                request.resource_id,
                request.source_scope,
                request.knowledge_cutoff_at,
                request.knowledge_cutoff_at,
            ),
        ).fetchall()
        effective_rows = [dict(candidate) for candidate in visible_rows]
        exact_rows = [
            candidate
            for candidate in effective_rows
            if candidate.get("source_trade_date_status") == "valid"
            and candidate.get("source_trade_date") == request.source_trade_date
        ]
        row = max(exact_rows, key=self._source_order_key) if exact_rows else None
        if row is not None:
            state, reason = self._source_state(row)
            return {
                "source_state": state,
                "source_reason": reason,
                "source_status": str(row.get("status") or "unknown"),
                "source_trade_date": row.get("source_trade_date"),
                "source_record_reference": row.get("source_record_reference"),
                "source_available_at": row.get("available_at"),
                "source_ingested_at": row.get("ingested_at"),
                "source_coverage_state": row.get("coverage_state"),
                "source_proof_present": bool(
                    str(row.get("coverage_proof_type") or "").strip()
                    and str(row.get("coverage_proof_reference") or "").strip()
                ),
                "source_snapshot_id": row.get("source_snapshot_id"),
                "source_url": row.get("source_url"),
            }

        bound_invalid_rows = [
            candidate
            for candidate in effective_rows
            if self._safe_invalid_binding(candidate, request.source_trade_date)
        ]
        bound_invalid = bool(bound_invalid_rows)
        return {
            "source_state": (
                CoverageSourceState.BLOCKED.value
                if bound_invalid
                else CoverageSourceState.UNKNOWN.value
            ),
            "source_reason": (
                "source_date_in_future_or_invalid"
                if bound_invalid
                else "no_exact_D"
            ),
            "source_status": (
                "blocked" if bound_invalid else "no_exact_D"
            ),
            "source_trade_date": None,
            "source_record_reference": None,
            "source_available_at": None,
            "source_ingested_at": None,
            "source_coverage_state": None,
            "source_proof_present": False,
            "source_snapshot_id": None,
            "source_url": None,
        }

    @staticmethod
    def _base_sql() -> str:
        """Return the common parameterized D×K projection CTE."""

        excluded_values = ", ".join(repr(value) for value in _CLASSIFICATION_EXCLUDED_TYPES)
        return f"""
        WITH RECURSIVE
        params(cutoff, trade_date, venue, resource_id, source_scope) AS (
            SELECT ?, ?, ?, ?, ?
        ),
        anchor_candidates AS (
            SELECT i.instrument_id, i.venue, i.official_code,
                   i.identity_epoch, i.first_observed_at,
                   i.identity_binding_fingerprint
            FROM universe_instruments i
            CROSS JOIN params p
            WHERE i.venue = p.venue
              AND i.first_observed_at IS NOT NULL
              AND eod_timestamp_leq(i.first_observed_at, p.cutoff) = 1
        ),
        event_rows AS (
            SELECT e.instrument_id, 'lifecycle' AS event_source,
                   e.lifecycle_event_id AS event_id,
                   e.lifecycle_event_id, NULL AS operational_event_id,
                   e.event_type, NULL AS trading_state,
                   e.event_date, e.effective_at, e.available_at,
                   e.ingested_at, e.status, e.reason
            FROM universe_lifecycle_events e
            JOIN anchor_candidates a ON a.instrument_id = e.instrument_id
            CROSS JOIN params p
            WHERE e.available_at IS NOT NULL AND eod_timestamp_leq(e.available_at, p.cutoff) = 1
              AND e.ingested_at IS NOT NULL AND eod_timestamp_leq(e.ingested_at, p.cutoff) = 1
              AND e.event_type <> 'resumed'
            UNION ALL
            SELECT e.instrument_id, 'operational' AS event_source,
                   e.operational_event_id AS event_id,
                   NULL AS lifecycle_event_id, e.operational_event_id,
                   NULL AS event_type, e.trading_state,
                   NULL AS event_date, e.effective_at, e.available_at,
                   e.ingested_at, e.status, e.reason
            FROM universe_operational_state_events e
            JOIN anchor_candidates a ON a.instrument_id = e.instrument_id
            CROSS JOIN params p
            WHERE e.available_at IS NOT NULL AND eod_timestamp_leq(e.available_at, p.cutoff) = 1
              AND e.ingested_at IS NOT NULL AND eod_timestamp_leq(e.ingested_at, p.cutoff) = 1
            UNION ALL
            SELECT e.instrument_id, 'lifecycle' AS event_source,
                   e.lifecycle_event_id AS event_id,
                   e.lifecycle_event_id, NULL AS operational_event_id,
                   e.event_type, 'normal' AS trading_state,
                   e.event_date, e.effective_at, e.available_at,
                   e.ingested_at, e.status, e.reason
            FROM universe_lifecycle_events e
            JOIN anchor_candidates a ON a.instrument_id = e.instrument_id
            CROSS JOIN params p
            WHERE e.event_type = 'resumed'
              AND e.available_at IS NOT NULL AND eod_timestamp_leq(e.available_at, p.cutoff) = 1
              AND e.ingested_at IS NOT NULL AND eod_timestamp_leq(e.ingested_at, p.cutoff) = 1
        ),
        event_temporal AS (
            SELECT e.*,
                   CASE
                     WHEN e.effective_at IS NOT NULL THEN
                       CASE
                         WHEN date(e.effective_at, '+8 hours') < p.trade_date THEN 'before'
                         WHEN date(e.effective_at, '+8 hours') > p.trade_date THEN 'after'
                         ELSE 'same'
                       END
                     WHEN e.event_date IS NOT NULL THEN
                       CASE
                         WHEN substr(e.event_date, 1, 10) < p.trade_date THEN 'before'
                         WHEN substr(e.event_date, 1, 10) > p.trade_date THEN 'after'
                         ELSE 'same'
                       END
                     ELSE 'unresolved'
                   END AS d_state,
                   CASE
                     WHEN e.effective_at IS NOT NULL THEN date(e.effective_at, '+8 hours')
                     WHEN e.event_date IS NOT NULL THEN substr(e.event_date, 1, 10)
                     ELSE ''
                   END AS d_boundary,
                   CASE
                     WHEN e.effective_at IS NOT NULL THEN 2
                     WHEN e.event_date IS NOT NULL THEN 1
                     ELSE 0
                   END AS d_precision
            FROM event_rows e
            CROSS JOIN params p
        ),
        listing_ranked AS (
            SELECT e.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY e.instrument_id
                     ORDER BY e.d_boundary DESC, e.d_precision DESC,
                              e.available_at DESC, e.ingested_at DESC,
                              e.event_id DESC
                   ) AS rn
            FROM event_temporal e
            WHERE e.event_type IN ('listed', 'terminated')
              AND e.d_state <> 'after'
        ),
        latest_listing AS (
            SELECT * FROM listing_ranked WHERE rn = 1
        ),
        operational_ranked AS (
            SELECT e.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY e.instrument_id
                     ORDER BY e.d_boundary DESC, e.d_precision DESC,
                              e.available_at DESC, e.ingested_at DESC,
                              e.event_id DESC
                   ) AS rn
            FROM event_temporal e
            WHERE (e.event_source = 'operational' OR e.event_type = 'resumed')
              AND e.d_state <> 'after'
        ),
        latest_operational AS (
            SELECT * FROM operational_ranked WHERE rn = 1
        ),
        epoch_starts AS (
            SELECT a.*
            FROM anchor_candidates a
            WHERE NOT EXISTS (
                SELECT 1
                FROM anchor_candidates prior
                WHERE prior.venue = a.venue
                  AND prior.official_code = a.official_code
                  AND prior.identity_epoch < a.identity_epoch
            )
        ),
        epoch_chain(instrument_id, venue, official_code, identity_epoch,
                    first_observed_at, identity_binding_fingerprint) AS (
            SELECT instrument_id, venue, official_code, identity_epoch,
                   first_observed_at, identity_binding_fingerprint
            FROM epoch_starts
            UNION ALL
            SELECT next_epoch.instrument_id, next_epoch.venue,
                   next_epoch.official_code, next_epoch.identity_epoch,
                   next_epoch.first_observed_at,
                   next_epoch.identity_binding_fingerprint
            FROM epoch_chain current_epoch
            JOIN anchor_candidates next_epoch
              ON next_epoch.venue = current_epoch.venue
             AND next_epoch.official_code = current_epoch.official_code
             AND next_epoch.identity_epoch = current_epoch.identity_epoch + 1
            JOIN latest_listing termination
              ON termination.instrument_id = current_epoch.instrument_id
             AND termination.event_type = 'terminated'
             AND termination.status = 'accepted'
             AND termination.d_state = 'before'
        ),
        effective_epochs AS (
            SELECT venue, official_code, MAX(identity_epoch) AS identity_epoch
            FROM epoch_chain
            GROUP BY venue, official_code
        ),
        effective_anchors AS (
            SELECT a.*
            FROM anchor_candidates a
            JOIN effective_epochs e
              ON e.venue = a.venue
             AND e.official_code = a.official_code
             AND e.identity_epoch = a.identity_epoch
        ),
        visible_revision_rows AS (
            SELECT r.*, ur.logical_revision_key,
                   ur.source_effective_date AS universe_source_effective_date,
                   ea.venue AS anchor_venue,
                   ea.official_code AS anchor_code,
                   ea.identity_epoch AS anchor_identity_epoch,
                   p.resource_role, p.completeness_policy,
                   p.freshness_mode AS policy_freshness_mode,
                   p.availability_mode AS policy_availability_mode
            FROM effective_anchors ea
            JOIN universe_instrument_revisions r
              ON r.instrument_id = ea.instrument_id
            JOIN universe_revisions ur
              ON ur.universe_revision_id = r.universe_revision_id
            JOIN universe_resource_policies p
              ON p.resource_id = r.resource_id
            CROSS JOIN params q
            WHERE r.status IN ('accepted', 'revoked', 'partial')
              AND r.available_at IS NOT NULL AND eod_timestamp_leq(r.available_at, q.cutoff) = 1
              AND r.ingested_at IS NOT NULL AND eod_timestamp_leq(r.ingested_at, q.cutoff) = 1
              AND (
                r.availability_mode <> 'manual_publication_evidence_required'
                OR EXISTS (
                  SELECT 1
                  FROM resource_publication_evidence pe
                  WHERE pe.publication_evidence_id = ur.publication_evidence_id
                    AND pe.status = 'accepted'
                    AND eod_timestamp_leq(pe.official_release_at, r.available_at) = 1
                    AND eod_timestamp_leq(pe.ingested_at, q.cutoff) = 1
                    AND pe.publication_evidence_id = (
                      SELECT latest.publication_evidence_id
                      FROM resource_publication_evidence latest
                      WHERE latest.resource_id = ur.resource_id
                        AND latest.logical_revision_key = ur.logical_revision_key
                        AND eod_timestamp_leq(latest.ingested_at, q.cutoff) = 1
                      ORDER BY latest.revision_number DESC,
                               latest.ingested_at DESC,
                               latest.publication_evidence_id DESC
                      LIMIT 1
                    )
              )
          )
        ),
        revision_temporal AS (
            SELECT v.*,
                   eod_revision_d_state(
                       COALESCE(v.source_effective_date, v.universe_source_effective_date),
                       v.source_effective_at,
                       v.effective_from,
                       v.effective_to,
                       q.trade_date
                   ) AS revision_d_state
            FROM visible_revision_rows v
            CROSS JOIN params q
        ),
        visible_revision_edges AS (
            SELECT instrument_id, instrument_revision_id AS child_revision_id,
                   supersedes_revision_id AS parent_revision_id
            FROM visible_revision_rows
            WHERE status IN ('accepted', 'revoked')
              AND supersedes_revision_id IS NOT NULL
        ),
        excluded_revisions(instrument_id, instrument_revision_id) AS (
            SELECT instrument_id, parent_revision_id
            FROM visible_revision_edges
            UNION
            SELECT e.instrument_id, e.parent_revision_id
            FROM visible_revision_edges e
            JOIN excluded_revisions x
              ON x.instrument_id = e.instrument_id
             AND x.instrument_revision_id = e.child_revision_id
        ),
        d_applicable_revision_rows AS (
            SELECT *
            FROM revision_temporal
            WHERE revision_d_state = 1
        ),
        revision_d_context AS (
            SELECT instrument_id,
                   MAX(CASE WHEN revision_d_state <> 1 THEN 1 ELSE 0 END)
                     AS revision_d_non_applicable
            FROM revision_temporal
            GROUP BY instrument_id
        ),
        accepted_ranked AS (
            SELECT v.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY v.instrument_id
                     ORDER BY v.revision_number DESC,
                              COALESCE(v.available_at, '') DESC,
                              v.ingested_at DESC, v.instrument_revision_id DESC
                   ) AS rn
            FROM d_applicable_revision_rows v
            WHERE v.status = 'accepted'
              AND NOT EXISTS (
                SELECT 1 FROM excluded_revisions x
                WHERE x.instrument_id = v.instrument_id
                  AND x.instrument_revision_id = v.instrument_revision_id
              )
        ),
        latest_accepted AS (
            SELECT * FROM accepted_ranked WHERE rn = 1
        ),
        partial_ranked AS (
            SELECT v.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY v.instrument_id
                     ORDER BY v.revision_number DESC,
                              COALESCE(v.available_at, '') DESC,
                              v.ingested_at DESC, v.instrument_revision_id DESC
                   ) AS rn
            FROM d_applicable_revision_rows v
            WHERE v.status = 'partial'
        ),
        latest_partial AS (
            SELECT * FROM partial_ranked WHERE rn = 1
        ),
        reference_candidates AS (
            SELECT a.*, 'accepted' AS reference_status
            FROM latest_accepted a
            WHERE a.resource_role = 'master_snapshot'
            UNION ALL
            SELECT p.*, 'partial' AS reference_status
            FROM latest_partial p
            WHERE p.resource_role = 'master_snapshot'
              AND NOT EXISTS (
                SELECT 1 FROM latest_accepted a
                WHERE a.instrument_id = p.instrument_id
                  AND a.resource_role = 'master_snapshot'
              )
              AND NOT EXISTS (
                SELECT 1 FROM excluded_revisions x
                WHERE x.instrument_id = p.instrument_id
              )
        ),
        master_ranked AS (
            SELECT a.instrument_id, a.canonical_symbol, a.mapping_basis,
                   ROW_NUMBER() OVER (
                     PARTITION BY a.instrument_id
                     ORDER BY a.revision_number DESC,
                              COALESCE(a.available_at, '') DESC,
                              a.ingested_at DESC, a.instrument_revision_id DESC
                   ) AS rn
            FROM accepted_ranked a
            WHERE a.resource_role = 'master_snapshot'
        ),
        latest_master AS (
            SELECT instrument_id, canonical_symbol, mapping_basis
            FROM master_ranked
            WHERE rn = 1
        ),
        reference_projected AS (
            SELECT r.*,
                   CASE WHEN r.resource_role = 'master_snapshot'
                        THEN r.canonical_symbol
                        ELSE m.canonical_symbol END AS effective_canonical_symbol,
                   CASE WHEN r.resource_role = 'master_snapshot'
                        THEN r.mapping_basis
                        ELSE m.mapping_basis END AS effective_mapping_basis
            FROM reference_candidates r
            LEFT JOIN latest_master m ON m.instrument_id = r.instrument_id
        ),
        source_visible_rows AS (
            SELECT s.*
            FROM eod_close_source_snapshots s
            CROSS JOIN params p
            WHERE s.resource_id = p.resource_id
              AND s.source_scope = p.source_scope
              AND s.available_at IS NOT NULL
              AND eod_timestamp_leq(s.available_at, p.cutoff) = 1
              AND s.ingested_at IS NOT NULL
              AND eod_timestamp_leq(s.ingested_at, p.cutoff) = 1
        ),
        source_edges AS (
            SELECT child.source_snapshot_id AS child_snapshot_id,
                   child.supersedes_source_snapshot_id AS parent_snapshot_id
            FROM source_visible_rows child
            WHERE child.supersedes_source_snapshot_id IS NOT NULL
        ),
        source_excluded(source_snapshot_id) AS (
            SELECT parent_snapshot_id
            FROM source_edges
            UNION
            SELECT edge.parent_snapshot_id
            FROM source_edges edge
            JOIN source_excluded excluded
              ON excluded.source_snapshot_id = edge.child_snapshot_id
        ),
        source_effective_rows AS (
            SELECT visible.*
            FROM source_visible_rows visible
            WHERE NOT EXISTS (
                SELECT 1
                FROM source_excluded excluded
                WHERE excluded.source_snapshot_id = visible.source_snapshot_id
            )
        ),
        source_ranked AS (
            SELECT s.*,
                   ROW_NUMBER() OVER (
                     ORDER BY s.available_at DESC, s.ingested_at DESC,
                              s.revision_number DESC, s.source_snapshot_id DESC,
                              s.normalized_payload_sha256 DESC
                   ) AS rn
            FROM source_effective_rows s
            CROSS JOIN params p
            WHERE s.resource_id = p.resource_id
              AND s.source_scope = p.source_scope
              AND s.source_trade_date = p.trade_date
              AND s.source_trade_date_status = 'valid'
              AND s.available_at IS NOT NULL AND eod_timestamp_leq(s.available_at, p.cutoff) = 1
              AND s.ingested_at IS NOT NULL AND eod_timestamp_leq(s.ingested_at, p.cutoff) = 1
        ),
        source_projected AS (
            SELECT s.*,
                   CASE
                     WHEN s.status IN ('provider_error','schema_changed',
                                       'blocked','revoked','rejected') THEN 'blocked'
                     WHEN s.status IN ('unknown','insufficient_data') THEN 'unknown'
                     WHEN s.coverage_state = 'partial'
                          AND trim(COALESCE(s.coverage_proof_type, '')) <> ''
                          AND trim(COALESCE(s.coverage_proof_reference, '')) <> '' THEN 'partial'
                     WHEN s.coverage_state = 'partial' THEN 'unknown'
                     WHEN s.status = 'partial'
                          AND trim(COALESCE(s.coverage_proof_type, '')) <> ''
                          AND trim(COALESCE(s.coverage_proof_reference, '')) <> '' THEN 'partial'
                     WHEN s.status = 'partial' THEN 'unknown'
                     WHEN COALESCE(s.row_count, 0) = 0 THEN 'unknown'
                     WHEN s.status = 'available' THEN 'usable'
                     ELSE 'unknown'
                   END AS source_state,
                   CASE WHEN (s.coverage_state = 'partial' OR s.status = 'partial')
                              AND trim(COALESCE(s.coverage_proof_type, '')) <> ''
                              AND trim(COALESCE(s.coverage_proof_reference, '')) <> ''
                        THEN 1 ELSE 0 END AS source_proof_present
            FROM source_ranked s
            WHERE s.rn = 1
        ),
        classification_visible_rows AS (
            SELECT c.*
            FROM eod_product_classification_evidence c
            CROSS JOIN params p
            WHERE c.resource_id = 'twse.isin.security_classification'
              AND c.available_at IS NOT NULL AND eod_timestamp_leq(c.available_at, p.cutoff) = 1
              AND c.ingested_at IS NOT NULL AND eod_timestamp_leq(c.ingested_at, p.cutoff) = 1
        ),
        classification_edges AS (
            SELECT child.classification_evidence_id AS child_evidence_id,
                   child.supersedes_classification_evidence_id AS parent_evidence_id
            FROM classification_visible_rows child
            WHERE child.supersedes_classification_evidence_id IS NOT NULL
        ),
        classification_excluded(classification_evidence_id) AS (
            SELECT parent_evidence_id
            FROM classification_edges
            UNION
            SELECT edge.parent_evidence_id
            FROM classification_edges edge
            JOIN classification_excluded excluded
              ON excluded.classification_evidence_id = edge.child_evidence_id
        ),
        classification_ranked AS (
            SELECT c.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY c.official_code, c.market_raw
                     ORDER BY c.revision_number DESC,
                              COALESCE(c.available_at, '') DESC,
                              c.ingested_at DESC,
                              c.classification_evidence_id DESC
                   ) AS rn
            FROM classification_visible_rows c
            WHERE NOT EXISTS (
                SELECT 1
                FROM classification_excluded excluded
                WHERE excluded.classification_evidence_id = c.classification_evidence_id
            )
        ),
        classification_context AS (
            -- Phase 14 classifier evidence has no approved D-effective field.
            -- available_at/ingested_at establish K visibility only; a
            -- material superseding revision therefore stays unresolved for D.
            SELECT latest.*,
                   CASE
                     WHEN latest.supersedes_classification_evidence_id IS NOT NULL
                      AND (
                        parent.classification_evidence_id IS NULL
                        OR COALESCE(latest.classification_state, '')
                           <> COALESCE(parent.classification_state, '')
                        OR COALESCE(latest.classification_decision, '')
                           <> COALESCE(parent.classification_decision, '')
                        OR COALESCE(latest.official_code, '')
                           <> COALESCE(parent.official_code, '')
                        OR COALESCE(latest.market_raw, '')
                           <> COALESCE(parent.market_raw, '')
                        OR COALESCE(latest.security_type_raw, '')
                           <> COALESCE(parent.security_type_raw, '')
                        OR COALESCE(latest.listing_date, '')
                           <> COALESCE(parent.listing_date, '')
                        OR COALESCE(latest.cfi_raw, '')
                           <> COALESCE(parent.cfi_raw, '')
                        OR COALESCE(latest.currency_raw, '')
                           <> COALESCE(parent.currency_raw, '')
                        OR COALESCE(latest.remarks_raw, '')
                           <> COALESCE(parent.remarks_raw, '')
                      ) THEN 1 ELSE 0
                   END AS classification_d_unresolved
            FROM classification_ranked latest
            LEFT JOIN eod_product_classification_evidence parent
              ON parent.classification_evidence_id = latest.supersedes_classification_evidence_id
            WHERE latest.rn = 1
        ),
        observed_ranked AS (
            SELECT o.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY o.venue, o.official_code
                     ORDER BY o.revision_number DESC,
                              COALESCE(o.available_at, '') DESC,
                              o.ingested_at DESC, o.close_observation_id DESC
                   ) AS rn
            FROM eod_close_observations o
            JOIN source_projected s
              ON s.source_snapshot_id = o.source_snapshot_id
            CROSS JOIN params p
            WHERE o.resource_id = p.resource_id
              AND o.venue = p.venue
              AND o.official_code IS NOT NULL
              AND o.trade_date = p.trade_date
              AND o.trade_date_status = 'valid'
              AND o.available_at IS NOT NULL AND eod_timestamp_leq(o.available_at, p.cutoff) = 1
              AND o.ingested_at IS NOT NULL AND eod_timestamp_leq(o.ingested_at, p.cutoff) = 1
        ),
        candidate_facts AS (
            SELECT
                'denominator_candidate' AS item_kind,
                ea.venue,
                ea.official_code,
                ea.identity_epoch,
                eod_stable_key(ea.venue, ea.official_code,
                               ea.identity_epoch, 'denominator_candidate') AS stable_id,
                ea.instrument_id,
                ref.effective_canonical_symbol AS canonical_symbol,
                ref.instrument_revision_id AS reference_instrument_revision_id,
                ref.venue AS reference_venue,
                ref.official_code AS reference_official_code,
                ref.reference_status,
                ref.security_type,
                CASE
                  WHEN COALESCE(cc.classification_d_unresolved, 0) = 0
                   AND cc.classification_state = 'accepted'
                   AND cc.classification_decision = 'supported_stock'
                   AND cc.official_code = ea.official_code
                   AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                   AND trim(COALESCE(cc.security_type_raw, '')) = '股票'
                   AND upper(trim(COALESCE(cc.currency_raw, ''))) IN ('TWD','新台幣','台幣')
                   AND cc.listing_date IS NOT NULL
                   AND substr(cc.listing_date, 1, 10) > p.trade_date
                   THEN 'not_yet_listed'
                  WHEN ll.lifecycle_event_id IS NULL THEN COALESCE(ref.listing_status, 'unknown')
                  WHEN ll.d_state = 'before' AND ll.status = 'accepted'
                    THEN CASE WHEN ll.event_type = 'terminated' THEN 'delisted' ELSE 'listed' END
                  ELSE 'unknown'
                END AS listing_status,
                CASE
                  WHEN op.event_id IS NULL THEN COALESCE(ref.trading_state, 'unknown')
                  WHEN op.d_state = 'before' AND op.status = 'accepted'
                    THEN COALESCE(op.trading_state, 'unknown')
                  ELSE 'unknown'
                END AS trading_state,
                cc.classification_evidence_id,
                COALESCE(cc.classification_state, 'missing') AS classification_state,
                cc.classification_decision,
                cc.official_code AS classification_official_code,
                cc.market_raw AS classification_market_raw,
                cc.security_type_raw AS classification_security_type_raw,
                cc.listing_date AS classification_listing_date,
                cc.currency_raw AS classification_currency_raw,
                cc.cfi_raw AS classification_cfi_raw,
                cc.remarks_raw AS classification_remarks_raw,
                COALESCE(cc.classification_d_unresolved, 0) AS classification_d_unresolved,
                COALESCE(rdc.revision_d_non_applicable, 0) AS revision_d_non_applicable,
                CASE
                  WHEN COALESCE(cc.classification_d_unresolved, 0) = 0
                   AND cc.classification_state = 'accepted'
                   AND cc.classification_decision = 'supported_stock'
                   AND cc.official_code = ea.official_code
                   AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                   AND trim(COALESCE(cc.security_type_raw, '')) = '股票'
                   AND upper(trim(COALESCE(cc.currency_raw, ''))) IN ('TWD','新台幣','台幣')
                   AND cc.listing_date IS NOT NULL
                   AND substr(cc.listing_date, 1, 10) > p.trade_date
                   THEN 1 ELSE 0
                END AS classification_lifecycle_excluded,
                CASE
                  WHEN COALESCE(cc.classification_d_unresolved, 0) = 0
                   AND cc.classification_state = 'accepted'
                   AND cc.official_code = ea.official_code
                   AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                   AND (
                     lower(trim(COALESCE(cc.security_type_raw, ''))) IN ({excluded_values})
                     OR upper(trim(COALESCE(cc.cfi_raw, ''))) = 'EPNRAR'
                     OR lower(trim(COALESCE(cc.remarks_raw, ''))) IN
                       ('特別股','優先股','preferred stock','preferred stocks',
                        'preferred share','preferred shares')
                     OR cc.classification_decision = 'not_applicable'
                   ) THEN 1 ELSE 0
                END AS classification_product_excluded,
                CASE
                  WHEN COALESCE(cc.classification_d_unresolved, 0) = 1
                   THEN 'unresolved'
                  WHEN COALESCE(cc.classification_d_unresolved, 0) = 0
                   AND cc.classification_state = 'accepted'
                   AND cc.classification_decision = 'supported_stock'
                   AND cc.official_code = ea.official_code
                   AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                   AND trim(COALESCE(cc.security_type_raw, '')) = '股票'
                   AND upper(trim(COALESCE(cc.currency_raw, ''))) IN ('TWD','新台幣','台幣')
                   AND cc.listing_date IS NOT NULL
                   AND substr(cc.listing_date, 1, 10) > p.trade_date
                   THEN 'excluded'
                  WHEN COALESCE(cc.classification_d_unresolved, 0) = 0
                   AND cc.classification_state = 'accepted'
                   AND cc.official_code = ea.official_code
                   AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                   AND (
                     lower(trim(COALESCE(cc.security_type_raw, ''))) IN ({excluded_values})
                     OR upper(trim(COALESCE(cc.cfi_raw, ''))) = 'EPNRAR'
                     OR lower(trim(COALESCE(cc.remarks_raw, ''))) IN
                       ('特別股','優先股','preferred stock','preferred stocks',
                        'preferred share','preferred shares')
                     OR cc.classification_decision = 'not_applicable'
                   ) THEN 'excluded'
                  WHEN cc.classification_state = 'accepted'
                   AND cc.classification_decision = 'supported_stock'
                   AND cc.official_code = ea.official_code
                   AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                   AND trim(COALESCE(cc.security_type_raw, '')) = '股票'
                   AND upper(trim(COALESCE(cc.currency_raw, ''))) IN ('TWD','新台幣','台幣')
                   AND cc.listing_date IS NOT NULL
                   AND substr(cc.listing_date, 1, 10) <= p.trade_date
                   AND pp.currency = 'TWD' AND pp.unit = 'TWD_per_share'
                   THEN 'expected'
                  ELSE 'unresolved'
                END AS classification_membership_hint,
                CASE
                  WHEN ref.instrument_id IS NULL
                    OR ref.reference_status <> 'accepted'
                    OR ref.venue <> ea.venue
                    OR ref.official_code <> ea.official_code
                    OR ref.effective_canonical_symbol IS NULL THEN 1 ELSE 0
                END AS identity_unresolved,
                CASE
                  WHEN (ll.lifecycle_event_id IS NOT NULL
                        AND (ll.d_state <> 'before' OR ll.status <> 'accepted'))
                    OR (op.event_id IS NOT NULL
                        AND (op.d_state <> 'before' OR op.status <> 'accepted'))
                    THEN 1 ELSE 0
                END AS event_unresolved,
                CASE
                  WHEN (CASE
                    WHEN ll.lifecycle_event_id IS NULL THEN COALESCE(ref.listing_status, 'unknown')
                    WHEN ll.d_state = 'before' AND ll.status = 'accepted'
                      THEN CASE WHEN ll.event_type = 'terminated' THEN 'delisted' ELSE 'listed' END
                    ELSE 'unknown' END) = 'delisted'
                    OR (
                      COALESCE(cc.classification_d_unresolved, 0) = 0
                      AND cc.classification_state = 'accepted'
                      AND cc.classification_decision = 'supported_stock'
                      AND cc.official_code = ea.official_code
                      AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
                      AND trim(COALESCE(cc.security_type_raw, '')) = '股票'
                      AND upper(trim(COALESCE(cc.currency_raw, ''))) IN ('TWD','新台幣','台幣')
                      AND cc.listing_date IS NOT NULL
                      AND substr(cc.listing_date, 1, 10) > p.trade_date
                    ) THEN 1 ELSE 0
                END AS listing_excluded,
                CASE
                  WHEN (CASE
                    WHEN op.event_id IS NULL THEN COALESCE(ref.trading_state, 'unknown')
                    WHEN op.d_state = 'before' AND op.status = 'accepted'
                      THEN COALESCE(op.trading_state, 'unknown')
                    ELSE 'unknown' END) IN ('suspended','no_trading') THEN 1 ELSE 0
                END AS operational_excluded,
                obs.close_observation_id AS observation_id,
                obs.instrument_id AS observation_instrument_id,
                obs.instrument_revision_id AS observation_instrument_revision_id,
                obs.trade_date AS observation_trade_date,
                obs.observation_status,
                obs.public_eligibility_status,
                obs.close_value AS observation_close_value,
                obs.volume_value AS observation_volume_value,
                obs.product_scope AS observation_product_scope,
                obs.currency AS observation_currency,
                obs.unit AS observation_unit,
                obs.quality_status AS observation_quality_status,
                obs.source_trading_scope AS observation_source_scope,
                eod_numeric_valid(obs.close_value, 0) AS observation_close_valid,
                eod_numeric_valid(obs.volume_value, 0) AS observation_volume_valid,
                obs.source_record_reference AS observation_source_record_reference,
                obs.quality_flags_json,
                COALESCE(sp.source_state, 'unknown') AS source_state,
                COALESCE(sp.status, 'no_exact_D') AS source_status,
                CASE
                  WHEN sp.source_state = 'unknown' AND COALESCE(sp.row_count, 0) = 0
                    THEN 'source_empty'
                  ELSE COALESCE(sp.reason, '')
                END AS source_reason,
                sp.source_snapshot_id,
                sp.source_record_reference,
                sp.source_trade_date,
                sp.available_at AS source_available_at,
                sp.ingested_at AS source_ingested_at,
                sp.coverage_state AS source_coverage_state,
                COALESCE(sp.source_proof_present, 0) AS source_proof_present
            FROM effective_anchors ea
            LEFT JOIN reference_projected ref ON ref.instrument_id = ea.instrument_id
            LEFT JOIN latest_listing ll ON ll.instrument_id = ea.instrument_id
            LEFT JOIN latest_operational op ON op.instrument_id = ea.instrument_id
            LEFT JOIN revision_d_context rdc ON rdc.instrument_id = ea.instrument_id
            LEFT JOIN classification_context cc
              ON cc.official_code = ea.official_code
             AND cc.market_raw = CASE WHEN ea.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
            LEFT JOIN observed_ranked obs
              ON obs.venue = ea.venue
             AND obs.official_code = ea.official_code
             AND obs.rn = 1
            LEFT JOIN source_projected sp ON 1 = 1
            LEFT JOIN eod_price_resource_policies pp
              ON pp.resource_id = (SELECT resource_id FROM params)
            CROSS JOIN params p
        ),
        candidate_projection AS (
            SELECT facts.*,
                   CASE
                     WHEN (facts.observation_instrument_id IS NOT NULL
                           AND facts.instrument_id IS NOT NULL
                           AND facts.observation_instrument_id <> facts.instrument_id)
                       OR (facts.observation_instrument_revision_id IS NOT NULL
                           AND facts.reference_instrument_revision_id IS NOT NULL
                           AND facts.observation_instrument_revision_id
                               <> facts.reference_instrument_revision_id)
                       OR facts.identity_unresolved = 1
                       OR facts.event_unresolved = 1
                       THEN 'identity_unresolved'
                     WHEN facts.classification_membership_hint = 'unresolved'
                       THEN 'classification_unresolved'
                     WHEN facts.listing_excluded = 1 THEN 'excluded_by_lifecycle'
                     WHEN facts.operational_excluded = 1 THEN 'excluded_by_operational_state'
                     WHEN facts.classification_product_excluded = 1
                       THEN 'excluded_by_product_scope'
                     WHEN facts.observation_id IS NULL
                       AND facts.source_state = 'blocked' THEN 'source_blocked'
                     WHEN facts.observation_id IS NULL
                       AND facts.source_state = 'unknown' THEN 'source_unknown'
                     WHEN facts.observation_id IS NULL
                       AND facts.source_state = 'partial' THEN 'source_partial'
                     WHEN facts.observation_id IS NULL THEN 'not_observed_unproven'
                    WHEN facts.observation_status = 'available'
                       AND facts.public_eligibility_status = 'eligible'
                       AND facts.observation_product_scope = 'supported_stock'
                       AND facts.observation_currency = 'TWD'
                       AND facts.observation_unit = 'TWD_per_share'
                       AND facts.observation_quality_status = 'fresh'
                       AND facts.observation_source_scope = (SELECT source_scope FROM params)
                       AND facts.observation_close_valid = 1
                       AND facts.observation_volume_valid = 1
                       THEN 'observed_eligible'
                     ELSE 'observed_ineligible'
                   END AS coverage_status_hint,
                   CASE
                     WHEN facts.listing_excluded = 1
                       OR facts.operational_excluded = 1
                       OR facts.classification_product_excluded = 1
                       THEN 'excluded'
                     WHEN facts.identity_unresolved = 1
                       OR facts.event_unresolved = 1
                       OR facts.classification_membership_hint = 'unresolved'
                       THEN 'unresolved'
                     ELSE 'expected'
                   END AS denominator_membership_hint
            FROM candidate_facts facts
        ),
        orphan_projection AS (
            SELECT
                'source_observation_orphan' AS item_kind,
                o.venue,
                o.official_code,
                NULL AS identity_epoch,
                eod_stable_key(o.venue, o.official_code, o.trade_date,
                               o.source_record_reference) AS stable_id,
                NULL AS instrument_id,
                NULL AS canonical_symbol,
                NULL AS reference_instrument_revision_id,
                NULL AS reference_venue,
                NULL AS reference_official_code,
                NULL AS reference_status,
                NULL AS security_type,
                NULL AS listing_status,
                NULL AS trading_state,
                cc.classification_evidence_id,
                COALESCE(cc.classification_state, 'missing') AS classification_state,
                cc.classification_decision,
                cc.official_code AS classification_official_code,
                cc.market_raw AS classification_market_raw,
                cc.security_type_raw AS classification_security_type_raw,
                cc.listing_date AS classification_listing_date,
                cc.currency_raw AS classification_currency_raw,
                cc.cfi_raw AS classification_cfi_raw,
                cc.remarks_raw AS classification_remarks_raw,
                COALESCE(cc.classification_d_unresolved, 0) AS classification_d_unresolved,
                0 AS revision_d_non_applicable,
                0 AS classification_lifecycle_excluded,
                0 AS classification_product_excluded,
                'unresolved' AS classification_membership_hint,
                1 AS identity_unresolved,
                0 AS event_unresolved,
                0 AS listing_excluded,
                0 AS operational_excluded,
                o.close_observation_id AS observation_id,
                o.instrument_id AS observation_instrument_id,
                o.instrument_revision_id AS observation_instrument_revision_id,
                o.trade_date AS observation_trade_date,
                o.observation_status,
                o.public_eligibility_status,
                o.close_value AS observation_close_value,
                o.volume_value AS observation_volume_value,
                o.product_scope AS observation_product_scope,
                o.currency AS observation_currency,
                o.unit AS observation_unit,
                o.quality_status AS observation_quality_status,
                o.source_trading_scope AS observation_source_scope,
                eod_numeric_valid(o.close_value, 0) AS observation_close_valid,
                eod_numeric_valid(o.volume_value, 0) AS observation_volume_valid,
                o.source_record_reference AS observation_source_record_reference,
                o.quality_flags_json,
                COALESCE(sp.source_state, 'unknown') AS source_state,
                COALESCE(sp.status, 'no_exact_D') AS source_status,
                COALESCE(sp.reason, '') AS source_reason,
                sp.source_snapshot_id,
                sp.source_record_reference,
                sp.source_trade_date,
                sp.available_at AS source_available_at,
                sp.ingested_at AS source_ingested_at,
                sp.coverage_state AS source_coverage_state,
                COALESCE(sp.source_proof_present, 0) AS source_proof_present,
                'source_observation_unmapped' AS coverage_status_hint,
                NULL AS denominator_membership_hint
            FROM observed_ranked o
            LEFT JOIN classification_context cc
              ON cc.official_code = o.official_code
             AND cc.market_raw = CASE WHEN o.venue = 'TWSE' THEN '上市' ELSE '上櫃' END
            LEFT JOIN source_projected sp ON 1 = 1
            WHERE o.rn = 1
              AND NOT EXISTS (
                SELECT 1
                FROM effective_anchors ea
                WHERE ea.venue = o.venue
                  AND ea.official_code = o.official_code
              )
        ),
        raw_items AS (
            SELECT * FROM candidate_projection
            UNION ALL
            SELECT * FROM orphan_projection
        )
        SELECT raw_items.*,
               CASE WHEN raw_items.identity_epoch IS NULL THEN 1 ELSE 0 END
                 AS identity_epoch_null_rank,
               CASE WHEN raw_items.identity_epoch IS NULL THEN 2147483647
                    ELSE raw_items.identity_epoch END AS identity_epoch_order
        FROM raw_items
        """

    @staticmethod
    def _base_params(request: CoverageRequest) -> list[Any]:
        return [
            request.knowledge_cutoff_at,
            request.source_trade_date,
            request.venue,
            request.resource_id,
            request.source_scope,
        ]

    @staticmethod
    def _status_counts(
        conn: sqlite3.Connection,
        base_sql: str,
        params: list[Any],
        *,
        bound_invalid_source: bool = False,
    ) -> dict[str, Any]:
        rows = conn.execute(
            f"""
            SELECT item_kind, denominator_membership_hint, coverage_status_hint,
                   COUNT(*) AS item_count
            FROM ({base_sql}) projected
            GROUP BY item_kind, denominator_membership_hint, coverage_status_hint
            """,
            params,
        ).fetchall()
        expected = excluded = unresolved = candidates = orphan_count = 0
        status_counts: dict[str, int] = {}
        for row in rows:
            item_kind = str(row[0])
            membership = row[1]
            status = EodCoverageRepository._effective_status(
                item_kind=item_kind,
                denominator_membership=membership,
                status=row[2],
                bound_invalid_source=bound_invalid_source,
            )
            count = int(row[3])
            status_counts[status] = status_counts.get(status, 0) + count
            if item_kind == CoverageItemKind.SOURCE_OBSERVATION_ORPHAN.value:
                orphan_count += count
                continue
            candidates += count
            if membership == DenominatorMembership.EXPECTED.value:
                expected += count
            elif membership == DenominatorMembership.EXCLUDED.value:
                excluded += count
            elif membership == DenominatorMembership.UNRESOLVED.value:
                unresolved += count
        return {
            "denominator_candidate_count": candidates,
            "denominator_expected_count": expected,
            "denominator_excluded_count": excluded,
            "denominator_unresolved_count": unresolved,
            "source_observation_orphan_count": orphan_count,
            "item_status_counts": dict(sorted(status_counts.items())),
        }

    @staticmethod
    def _effective_status(
        *,
        item_kind: Any,
        denominator_membership: Any,
        status: Any,
        bound_invalid_source: bool,
    ) -> str:
        normalized_status = str(status or CoverageStatus.SOURCE_UNKNOWN.value)
        if (
            bound_invalid_source
            and str(item_kind or "") == CoverageItemKind.DENOMINATOR_CANDIDATE.value
            and str(denominator_membership or "") == DenominatorMembership.EXPECTED.value
            and normalized_status == CoverageStatus.SOURCE_UNKNOWN.value
        ):
            return CoverageStatus.SOURCE_BLOCKED.value
        return normalized_status

    @staticmethod
    def _item_from_row(
        row: dict[str, Any],
        *,
        bound_invalid_source: bool = False,
    ) -> dict[str, Any]:
        raw_status = str(row.get("coverage_status_hint") or CoverageStatus.SOURCE_UNKNOWN.value)
        status = EodCoverageRepository._effective_status(
            item_kind=row.get("item_kind"),
            denominator_membership=row.get("denominator_membership_hint"),
            status=raw_status,
            bound_invalid_source=bound_invalid_source,
        )
        reasons: list[str] = [status]
        source_state = str(row.get("source_state") or CoverageSourceState.UNKNOWN.value)
        source_status = str(row.get("source_status") or "")
        source_reason = str(row.get("source_reason") or "")
        if (
            bound_invalid_source
            and raw_status == CoverageStatus.SOURCE_UNKNOWN.value
            and status == CoverageStatus.SOURCE_BLOCKED.value
        ):
            source_state = CoverageSourceState.BLOCKED.value
            source_status = CoverageSourceState.BLOCKED.value
            source_reason = "source_date_in_future_or_invalid"
        if source_state == CoverageSourceState.UNKNOWN.value:
            reasons.append(source_reason or "no_exact_D")
        elif source_state == CoverageSourceState.BLOCKED.value:
            reasons.append(source_reason or source_status or "source_blocked")
        elif source_state == CoverageSourceState.PARTIAL.value:
            reasons.append("source_partial")
        if row.get("identity_unresolved"):
            reasons.append("identity_unresolved")
        if row.get("event_unresolved"):
            reasons.append("event_d_applicability_unresolved")
        if row.get("identity_unresolved") and row.get("revision_d_non_applicable"):
            reasons.append("identity_d_applicability_unresolved")
        if row.get("listing_excluded"):
            reasons.append("excluded_by_lifecycle")
        if row.get("listing_status") == "not_yet_listed":
            reasons.append("not_yet_listed_on_source_trade_date")
        if row.get("operational_excluded"):
            reasons.append("excluded_by_operational_state")
        classification_hint = str(row.get("classification_membership_hint") or "unresolved")
        classification_state = str(row.get("classification_state") or "missing")
        if row.get("classification_product_excluded"):
            reasons.append("excluded_by_product_scope")
        elif classification_hint == "unresolved":
            reasons.append("classification_unresolved")
        if row.get("classification_d_unresolved"):
            reasons.append("classification_d_applicability_unresolved")
        if status == CoverageStatus.NOT_OBSERVED_UNPROVEN.value:
            reasons.append("not_observed_unproven")
        if status == CoverageStatus.SOURCE_OBSERVATION_UNMAPPED.value:
            reasons.append("source_observation_unmapped")
        if status == CoverageStatus.OBSERVED_INELIGIBLE.value and row.get("observation_id"):
            if row.get("observation_close_valid") != 1:
                reasons.append("close_unusable")
            if row.get("observation_volume_valid") != 1:
                reasons.append("volume_unusable")
            if row.get("observation_quality_status") != "fresh":
                reasons.append("observation_quality_unverified")
            if row.get("observation_product_scope") != "supported_stock":
                reasons.append("product_scope_unverified")
            if row.get("observation_currency") != "TWD" or row.get("observation_unit") != "TWD_per_share":
                reasons.append("currency_unit_unproven")
            if row.get("observation_source_scope") != source_scope_for_venue(str(row.get("venue") or "")):
                reasons.append("observation_source_scope_mismatch")
        quality_flags = row.get("quality_flags_json")
        if isinstance(quality_flags, str):
            try:
                parsed_flags = json.loads(quality_flags)
                if isinstance(parsed_flags, list):
                    reasons.extend(str(flag) for flag in parsed_flags)
            except json.JSONDecodeError:
                pass
        product_scope = "needs_human_input"
        if row.get("classification_product_excluded"):
            product_scope = "not_applicable"
        elif row.get("classification_lifecycle_excluded") or classification_hint == "expected":
            product_scope = "supported_stock"
        canonical = row.get("canonical_symbol")
        if canonical and (
            str(canonical).strip() != f"{row.get('official_code')}."
            + ("TW" if row.get("venue") == "TWSE" else "TWO")
        ):
            canonical = None
            reasons.append("identity_mapping_unverified")
        return {
            "item_kind": str(row.get("item_kind")),
            "venue": row.get("venue"),
            "official_code": row.get("official_code"),
            "canonical_symbol": canonical,
            "identity_epoch": row.get("identity_epoch"),
            "denominator_membership": row.get("denominator_membership_hint"),
            "coverage_status": status,
            "reason_codes": normalize_reason_codes(reasons),
            "listing_status": row.get("listing_status") or "unknown",
            "trading_state": row.get("trading_state") or "unknown",
            "classification_status": classification_state,
            "product_scope": product_scope,
            "observed_trade_date": row.get("observation_trade_date"),
            "observed_status": row.get("observation_status"),
            "source_record_reference": row.get("observation_source_record_reference"),
            "_source_state": source_state,
            "_stable_id": row.get("stable_id"),
        }

    def read(
        self,
        request: CoverageRequest,
        *,
        cursor_last_key: tuple[str, str, int | None, str, str] | None = None,
    ) -> CoverageProjection:
        with self.storage.read_transaction() as conn:
            conn.create_function(
                "eod_numeric_valid",
                2,
                _numeric_value_valid,
                deterministic=True,
            )
            conn.create_function(
                "eod_timestamp_leq",
                2,
                _timestamp_leq,
                deterministic=True,
            )
            conn.create_function(
                "eod_revision_d_state",
                5,
                _revision_d_state,
                deterministic=True,
            )
            conn.create_function(
                "eod_stable_key",
                4,
                _stable_public_key,
                deterministic=True,
            )
            source = self._source_context(conn, request)
            base_sql = self._base_sql()
            params = self._base_params(request)
            bound_invalid_source = (
                source["source_state"] == CoverageSourceState.BLOCKED.value
                and source["source_reason"] == "source_date_in_future_or_invalid"
            )
            aggregate = self._status_counts(
                conn,
                base_sql,
                params,
                bound_invalid_source=bound_invalid_source,
            )
            page_params = list(params)
            cursor_clause = ""
            if cursor_last_key is not None:
                venue, official_code, identity_epoch, item_kind, stable_id = cursor_last_key
                null_rank = 1 if identity_epoch is None else 0
                epoch_order = 2147483647 if identity_epoch is None else int(identity_epoch)
                cursor_clause = """
                WHERE (
                    projected.venue,
                    projected.official_code,
                    projected.identity_epoch_null_rank,
                    projected.identity_epoch_order,
                    projected.item_kind,
                    projected.stable_id
                ) > (?, ?, ?, ?, ?, ?)
                """
                page_params.extend([
                    venue, official_code, null_rank, epoch_order, item_kind, stable_id,
                ])
            page_params.append(request.limit + 1)
            page_rows = conn.execute(
                f"""
                SELECT projected.*
                FROM ({base_sql}) projected
                {cursor_clause}
                ORDER BY projected.venue ASC,
                         projected.official_code ASC,
                         projected.identity_epoch_null_rank ASC,
                         projected.identity_epoch_order ASC,
                         projected.item_kind ASC,
                         projected.stable_id ASC
                LIMIT ?
                """,
                page_params,
            ).fetchall()
            items = tuple(
                self._item_from_row(
                    dict(row),
                    bound_invalid_source=bound_invalid_source,
                )
                for row in page_rows
            )
            return CoverageProjection(source=source, aggregate=aggregate, items=items)


__all__ = ["CoverageProjection", "EodCoverageRepository"]
