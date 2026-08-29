"""Read-only Phase 16 service for the neutral batch market context."""

from __future__ import annotations

from typing import Any

from src.domain.neutral_batch_market_context import (
    NeutralBatchMarketContextRequest,
    cursor_for_item,
    decode_neutral_batch_cursor,
    neutral_batch_market_context_assembly_v1,
    neutral_batch_market_context_status_v1,
    safe_neutral_batch_response,
)
from src.repositories.neutral_batch_market_context_repository import (
    NeutralBatchMarketContextProjection,
    NeutralBatchMarketContextRepository,
)


class NeutralBatchMarketContextService:
    """Compose one stable public DTO from one repository snapshot."""

    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        repository: NeutralBatchMarketContextRepository | None = None,
    ) -> None:
        self.repository = repository or NeutralBatchMarketContextRepository(db_path)

    @staticmethod
    def _aggregate(aggregates: list[dict[str, Any]]) -> dict[str, Any]:
        result = {
            "denominator_candidate_count": 0,
            "denominator_expected_count": 0,
            "denominator_excluded_count": 0,
            "denominator_unresolved_count": 0,
            "source_observation_orphan_count": 0,
            "item_status_counts": {},
        }
        for aggregate in aggregates:
            for key in (
                "denominator_candidate_count",
                "denominator_expected_count",
                "denominator_excluded_count",
                "denominator_unresolved_count",
                "source_observation_orphan_count",
            ):
                result[key] += int(aggregate.get(key, 0))
            for status, count in aggregate.get("item_status_counts", {}).items():
                result["item_status_counts"][status] = (
                    result["item_status_counts"].get(status, 0) + int(count)
                )
        result["item_status_counts"] = dict(sorted(result["item_status_counts"].items()))
        return result

    @staticmethod
    def _source_state_for_status(source: dict[str, Any]) -> str:
        return str(source.get("source_state") or "unknown")

    @staticmethod
    def _public_source(source: dict[str, Any]) -> dict[str, Any]:
        return dict(source)

    @staticmethod
    def _cursor_for_item(
        request: NeutralBatchMarketContextRequest,
        item: dict[str, Any],
    ) -> str:
        return cursor_for_item(request, item)

    @staticmethod
    def _safe_response(
        *,
        request: NeutralBatchMarketContextRequest,
        status: dict[str, Any],
        per_venue: list[dict[str, Any]],
        aggregate: dict[str, Any],
        items: list[dict[str, Any]],
        next_cursor: str | None,
    ) -> dict[str, Any]:
        return safe_neutral_batch_response(
            request=request,
            status=status,
            per_venue=per_venue,
            aggregate=aggregate,
            items=items,
            next_cursor=next_cursor,
        )

    def as_of(
        self,
        *,
        market_date: str,
        knowledge_cutoff_at: str,
        venue_scope: str = "TWSE_TPEX",
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        request = NeutralBatchMarketContextRequest(
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            venue_scope=venue_scope,
            limit=limit,
            cursor=cursor,
        )
        decoded = (
            decode_neutral_batch_cursor(cursor, request=request) if cursor else None
        )
        projection = self.repository.read(
            request,
            cursor_last_key=decoded.last_key if decoded else None,
        )
        return self._compose(request, projection)

    def _compose(
        self,
        request: NeutralBatchMarketContextRequest,
        projection: NeutralBatchMarketContextProjection,
    ) -> dict[str, Any]:
        per_venue = self._compose_per_venue(request, projection)
        aggregate_status, aggregate = self._compose_global(per_venue)
        raw_items = list(projection.items)
        has_more = len(raw_items) > request.limit
        page_items = raw_items[: request.limit]
        next_cursor = (
            self._cursor_for_item(request, page_items[-1])
            if has_more and page_items
            else None
        )
        return self._safe_response(
            request=request,
            status=aggregate_status,
            per_venue=per_venue,
            aggregate=aggregate,
            items=page_items,
            next_cursor=next_cursor,
        )

    def _compose_per_venue(
        self,
        request: NeutralBatchMarketContextRequest,
        projection: NeutralBatchMarketContextProjection,
    ) -> list[dict[str, Any]]:
        per_venue: list[dict[str, Any]] = []
        for venue in request.venues:
            source = self._public_source(dict(projection.sources[venue]))
            aggregate = dict(projection.aggregates[venue])
            assembly = neutral_batch_market_context_assembly_v1(
                source_state=self._source_state_for_status(source),
                denominator_projection_state=aggregate.get(
                    "denominator_projection_state"
                ),
                aggregate=aggregate,
                partial_proof_present=bool(source.get("source_proof_present", False)),
            )
            aggregate["aggregate_assertion_state"] = assembly[
                "aggregate_assertion_state"
            ]
            per_venue.append(
                {
                    "venue": venue,
                    "assembly_status": assembly["status"],
                    "source": source,
                    "aggregate": aggregate,
                }
            )

        return per_venue

    def _compose_global(
        self,
        per_venue: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        aggregate = self._aggregate([venue["aggregate"] for venue in per_venue])
        aggregate_status = neutral_batch_market_context_status_v1(
            per_venue=per_venue,
            aggregate=aggregate,
        )
        aggregate["aggregate_assertion_state"] = aggregate_status[
            "aggregate_assertion_state"
        ]
        return aggregate_status, aggregate


__all__ = ["NeutralBatchMarketContextService"]
