"""Query-only Phase 16 batch projection over the Phase 13-15 evidence model."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from src.domain.eod_coverage import (
    CoverageItemKind,
    CoverageRequest,
    CoverageSourceState,
    DenominatorMembership,
)
from src.domain.neutral_batch_market_context import (
    NeutralBatchMarketContextRequest,
    VenueMapping,
    item_order_key,
)
from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.eod_coverage_repository import (
    EodCoverageRepository,
    _numeric_value_valid,
    _revision_d_state,
    _stable_public_key,
    _timestamp_leq,
)


@dataclass(frozen=True)
class NeutralBatchMarketContextProjection:
    """Internal projection assembled from one query-only SQLite snapshot."""

    sources: Mapping[str, dict[str, Any]]
    aggregates: Mapping[str, dict[str, Any]]
    items: tuple[dict[str, Any], ...]


# Normative implementation-plan spelling retained as a public alias.
NeutralBatchContextProjection = NeutralBatchMarketContextProjection


class NeutralBatchMarketContextRepository:
    """Read both venues with bounded, set-based statements and no writes."""

    _PAGE_LOOKAHEAD = 1

    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        storage: EodCloseRepository | None = None,
    ) -> None:
        # EodCloseRepository is used only for its query-only transaction.  It
        # does not auto-migrate and none of its append helpers are reachable.
        self.storage = storage or EodCloseRepository(db_path)
        self.coverage_repository = EodCoverageRepository(storage=self.storage)

    @staticmethod
    def _register_functions(conn: sqlite3.Connection) -> None:
        conn.create_function(
            "eod_numeric_valid", 2, _numeric_value_valid, deterministic=True
        )
        conn.create_function(
            "eod_timestamp_leq", 2, _timestamp_leq, deterministic=True
        )
        conn.create_function(
            "eod_revision_d_state", 5, _revision_d_state, deterministic=True
        )
        conn.create_function(
            "eod_stable_key", 4, _stable_public_key, deterministic=True
        )

    @staticmethod
    def _lineage_visible_rows(
        conn: sqlite3.Connection,
        coverage_request: CoverageRequest,
    ) -> list[dict[str, Any]]:
        """Resolve every K-visible source revision before selecting exact D."""

        rows = conn.execute(
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
                coverage_request.resource_id,
                coverage_request.source_scope,
                coverage_request.knowledge_cutoff_at,
                coverage_request.knowledge_cutoff_at,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _request_bound_invalid_or_future(row: Mapping[str, Any], target_date: str) -> bool:
        status = str(row.get("source_trade_date_status") or "")
        try:
            dimensions = json.loads(str(row.get("query_dimensions_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(dimensions, dict):
            return False
        bound = any(
            dimensions.get(key) == target_date
            for key in (
                "source_trade_date",
                "requested_source_trade_date",
                "target_trade_date",
            )
        )
        if not bound:
            return False
        if status in {"invalid", "future"}:
            return True
        if status == "valid":
            source_date = str(row.get("source_trade_date") or "")
            return bool(source_date and source_date > target_date)
        return False

    def _source_lineage_cte(
        self,
        conn: sqlite3.Connection,
        mapping: VenueMapping,
        coverage_request: CoverageRequest,
    ) -> dict[str, Any]:
        """Return safe source state after K visibility and correction lineage."""

        effective_rows = self._lineage_visible_rows(conn, coverage_request)
        exact_rows = [
            row
            for row in effective_rows
            if row.get("source_trade_date_status") == "valid"
            and row.get("source_trade_date") == coverage_request.source_trade_date
        ]
        exact = max(
            exact_rows,
            key=EodCoverageRepository._source_order_key,
            default=None,
        )
        if exact is not None:
            state, reason = EodCoverageRepository._source_state(exact)
            return self._source_public_context(mapping, exact, state, reason)

        bound_invalid = any(
            self._request_bound_invalid_or_future(
                row, coverage_request.source_trade_date
            )
            for row in effective_rows
        )
        if bound_invalid:
            return self._source_public_context(
                mapping,
                None,
                CoverageSourceState.BLOCKED.value,
                "source_date_in_future_or_invalid",
            )
        return self._source_public_context(
            mapping,
            None,
            CoverageSourceState.UNKNOWN.value,
            "no_exact_D",
        )

    @staticmethod
    def _source_public_context(
        mapping: VenueMapping,
        row: Mapping[str, Any] | None,
        state: str,
        reason: str,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "source_state": state,
                "source_reason": reason,
                "source_status": "blocked" if state == "blocked" else "no_exact_D",
                "source_trade_date": None,
                "source_record_reference": None,
                "source_available_at": None,
                "source_ingested_at": None,
                "source_coverage_state": None,
                "source_proof_present": False,
                "source_snapshot_id": None,
                "source_url": None,
                "provider": mapping.provider,
                "resource_id": mapping.resource_id,
                "source_scope": mapping.source_scope,
                "reason_codes": [reason],
            }
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
            "provider": mapping.provider,
            "resource_id": mapping.resource_id,
            "source_scope": mapping.source_scope,
            "reason_codes": [reason] if reason else [],
        }

    @staticmethod
    def _projection_sql(coverage_request: CoverageRequest) -> str:
        """Use the audited Phase 15 set projection for U/C/O in one statement.

        Its CTE chain is the locked universe, classification, and observation
        projection.  Phase 16 composes those venue-local streams in one read
        transaction; it does not change the Phase 15 implementation.
        """

        del coverage_request
        return EodCoverageRepository._base_sql()

    @staticmethod
    def _projection_params(coverage_request: CoverageRequest) -> list[Any]:
        return EodCoverageRepository._base_params(coverage_request)

    @staticmethod
    def _universe_projection_cte(coverage_request: CoverageRequest) -> str:
        return NeutralBatchMarketContextRepository._projection_sql(coverage_request)

    @staticmethod
    def _classification_projection_cte(coverage_request: CoverageRequest) -> str:
        return NeutralBatchMarketContextRepository._projection_sql(coverage_request)

    @staticmethod
    def _observation_projection_cte(coverage_request: CoverageRequest) -> str:
        return NeutralBatchMarketContextRepository._projection_sql(coverage_request)

    @staticmethod
    def _combined_items_cte(coverage_request: CoverageRequest) -> str:
        return NeutralBatchMarketContextRepository._observation_projection_cte(
            coverage_request
        )

    @staticmethod
    def _page_order_sql() -> str:
        return """
            CASE WHEN projected.official_code IS NULL THEN 1 ELSE 0 END ASC,
            COALESCE(projected.official_code, '') ASC,
            projected.identity_epoch_null_rank ASC,
            projected.identity_epoch_order ASC,
            CASE WHEN projected.item_kind = 'denominator_candidate' THEN 0 ELSE 1 END ASC,
            projected.stable_id ASC
        """

    @staticmethod
    def _page_rows(
        conn: sqlite3.Connection,
        coverage_request: CoverageRequest,
        *,
        limit: int,
        cursor_last_key: tuple[Any, ...] | None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        base_sql = NeutralBatchMarketContextRepository._combined_items_cte(
            coverage_request
        )
        params = NeutralBatchMarketContextRepository._projection_params(coverage_request)
        cursor_clause = ""
        if cursor_last_key is not None:
            _, _, code_rank, code_value, epoch_rank, epoch_value, item_order, stable_id = cursor_last_key
            cursor_clause = """
                WHERE (
                    CASE WHEN projected.official_code IS NULL THEN 1 ELSE 0 END,
                    COALESCE(projected.official_code, ''),
                    projected.identity_epoch_null_rank,
                    projected.identity_epoch_order,
                    CASE WHEN projected.item_kind = 'denominator_candidate' THEN 0 ELSE 1 END,
                    projected.stable_id
                ) > (?, ?, ?, ?, ?, ?)
            """
            params.extend([
                code_rank,
                code_value,
                epoch_rank,
                epoch_value,
                item_order,
                stable_id,
            ])
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT projected.*
            FROM ({base_sql}) projected
            {cursor_clause}
            ORDER BY {NeutralBatchMarketContextRepository._page_order_sql()}
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _aggregate(
        conn: sqlite3.Connection,
        coverage_request: CoverageRequest,
        *,
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        base_sql = NeutralBatchMarketContextRepository._universe_projection_cte(
            coverage_request
        )
        aggregate = EodCoverageRepository._status_counts(
            conn,
            base_sql,
            NeutralBatchMarketContextRepository._projection_params(coverage_request),
            bound_invalid_source=(
                source["source_state"] == CoverageSourceState.BLOCKED.value
                and source["source_reason"] == "source_date_in_future_or_invalid"
            ),
        )
        candidate_count = int(aggregate.get("denominator_candidate_count", 0))
        unresolved_count = int(aggregate.get("denominator_unresolved_count", 0))
        if candidate_count == 0:
            projection_state = "empty"
        elif unresolved_count == candidate_count:
            projection_state = "entirely_unresolved"
        else:
            projection_state = "usable"
        aggregate["denominator_projection_state"] = projection_state
        return aggregate

    @staticmethod
    def _page(
        conn: sqlite3.Connection,
        coverage_request: CoverageRequest,
        *,
        limit: int,
        cursor_last_key: tuple[Any, ...] | None,
    ) -> list[dict[str, Any]]:
        return NeutralBatchMarketContextRepository._page_rows(
            conn,
            coverage_request,
            limit=limit,
            cursor_last_key=cursor_last_key,
        )

    @staticmethod
    def _phase16_item(
        row: Mapping[str, Any],
        source: Mapping[str, Any],
        *,
        bound_invalid_source: bool,
    ) -> dict[str, Any]:
        source_row = dict(row)
        for key in (
            "source_state",
            "source_status",
            "source_reason",
            "source_snapshot_id",
            "source_record_reference",
            "source_trade_date",
            "source_available_at",
            "source_ingested_at",
            "source_coverage_state",
            "source_proof_present",
        ):
            source_row[key] = source.get(key)
        item = EodCoverageRepository._item_from_row(
            source_row,
            bound_invalid_source=bound_invalid_source,
        )
        raw_item_kind = str(row.get("item_kind") or "")
        identity_unresolved = bool(
            row.get("identity_unresolved")
            or row.get("observation_identity_unresolved")
            or raw_item_kind == CoverageItemKind.SOURCE_OBSERVATION_ORPHAN.value
        )
        if raw_item_kind == CoverageItemKind.SOURCE_OBSERVATION_ORPHAN.value:
            d_applicability = "unresolved"
        elif (
            identity_unresolved
            or row.get("event_unresolved")
            or row.get("classification_d_unresolved")
        ):
            d_applicability = "unresolved"
        elif row.get("listing_excluded") or row.get("operational_excluded") or row.get("classification_product_excluded"):
            d_applicability = "not_applicable"
        elif row.get("denominator_membership_hint") == DenominatorMembership.EXPECTED.value:
            d_applicability = "applicable"
        elif row.get("denominator_membership_hint") == DenominatorMembership.EXCLUDED.value:
            d_applicability = "not_applicable"
        else:
            d_applicability = "unresolved"
        coverage_status = str(item.get("coverage_status") or "unknown")
        if raw_item_kind == CoverageItemKind.SOURCE_OBSERVATION_ORPHAN.value:
            item_state = "source_observation_unmapped"
        elif coverage_status == "identity_unresolved":
            item_state = "identity_unresolved"
        elif coverage_status == "classification_unresolved":
            item_state = "classification_unresolved"
        elif row.get("trading_state") == "suspended":
            item_state = "suspended"
        elif row.get("trading_state") == "no_trading":
            item_state = "no_trade"
        elif coverage_status in {
            "excluded_by_lifecycle",
            "excluded_by_operational_state",
            "excluded_by_product_scope",
        }:
            item_state = "not_applicable"
        elif coverage_status == "source_blocked":
            item_state = "blocked"
        elif coverage_status == "source_partial":
            item_state = "partial"
        elif coverage_status == "observed_eligible":
            item_state = "available"
        else:
            # This includes observed_ineligible and every no-exact-D state.
            item_state = "unknown"
        item.update(
            {
                "item_state": item_state,
                "identity_status": "unresolved" if identity_unresolved else "resolved",
                "d_applicability": d_applicability,
                "public_eligibility_status": row.get("public_eligibility_status"),
                "observed_source_record_reference": row.get("observation_source_record_reference"),
                "_observation_close_value": row.get("observation_close_value"),
                "_observation_volume_value": row.get("observation_volume_value"),
                "_observation_currency": row.get("observation_currency"),
                "_observation_unit": row.get("observation_unit"),
                "_observation_quality_status": row.get("observation_quality_status"),
                "_provider": source.get("provider"),
                "_resource_id": source.get("resource_id"),
                "_source_scope": source.get("source_scope"),
                "_source_trade_date": source.get("source_trade_date"),
                "_source_available_at": source.get("source_available_at"),
                "_source_ingested_at": source.get("source_ingested_at"),
                "_order": item_order_key({
                    **item,
                    "_stable_id": item.get("_stable_id"),
                }),
            }
        )
        return item

    @staticmethod
    def _cursor_venue_order(cursor_last_key: tuple[Any, ...] | None) -> int | None:
        return int(cursor_last_key[0]) if cursor_last_key is not None else None

    def read(
        self,
        request: NeutralBatchMarketContextRequest,
        *,
        cursor_last_key: tuple[Any, ...] | None = None,
    ) -> NeutralBatchMarketContextProjection:
        """Read source, aggregates, and bounded page from one logical snapshot."""

        with self.storage.read_transaction() as conn:
            self._register_functions(conn)
            sources: dict[str, dict[str, Any]] = {}
            aggregates: dict[str, dict[str, Any]] = {}
            raw_page: list[tuple[str, dict[str, Any]]] = []
            cursor_venue_order = self._cursor_venue_order(cursor_last_key)

            for mapping in request.venue_mappings:
                coverage_request = CoverageRequest(
                    venue=mapping.venue,
                    source_trade_date=request.market_date,
                    knowledge_cutoff_at=request.knowledge_cutoff_at,
                    limit=request.limit,
                )
                source = self._source_lineage_cte(conn, mapping, coverage_request)
                aggregate = self._aggregate(
                    conn,
                    coverage_request,
                    source=source,
                )
                sources[mapping.venue] = source
                aggregates[mapping.venue] = aggregate

            remaining = request.limit + self._PAGE_LOOKAHEAD
            for mapping in request.venue_mappings:
                venue_order = request.venues.index(mapping.venue)
                if remaining <= 0:
                    break
                if cursor_venue_order is not None and venue_order < cursor_venue_order:
                    continue
                coverage_request = CoverageRequest(
                    venue=mapping.venue,
                    source_trade_date=request.market_date,
                    knowledge_cutoff_at=request.knowledge_cutoff_at,
                    limit=request.limit,
                )
                venue_cursor = None
                if cursor_last_key is not None and venue_order == cursor_venue_order:
                    venue_cursor = cursor_last_key
                rows = self._page(
                    conn,
                    coverage_request,
                    limit=remaining,
                    cursor_last_key=venue_cursor,
                )
                raw_page.extend((mapping.venue, row) for row in rows)
                remaining = request.limit + self._PAGE_LOOKAHEAD - len(raw_page)
                if len(raw_page) >= request.limit + self._PAGE_LOOKAHEAD:
                    break

            items: list[dict[str, Any]] = []
            for venue, row in raw_page:
                source = sources[venue]
                item = self._phase16_item(
                    row,
                    source,
                    bound_invalid_source=(
                        source["source_state"] == CoverageSourceState.BLOCKED.value
                        and source["source_reason"] == "source_date_in_future_or_invalid"
                    ),
                )
                items.append(item)
            items.sort(key=item_order_key)
            return NeutralBatchMarketContextProjection(
                sources=sources,
                aggregates=aggregates,
                items=tuple(items[: request.limit + self._PAGE_LOOKAHEAD]),
            )


__all__ = [
    "NeutralBatchContextProjection",
    "NeutralBatchMarketContextProjection",
    "NeutralBatchMarketContextRepository",
]
