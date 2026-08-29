"""Read-only Phase 15 EOD coverage service."""

from __future__ import annotations

from typing import Any

from src.domain.eod_coverage import (
    CoverageCursor,
    CoverageRequest,
    decode_cursor,
    eod_coverage_visibility_status_v1,
    safe_coverage_response,
)
from src.repositories.eod_coverage_repository import EodCoverageRepository


class EodCoverageService:
    """Compose the neutral historical coverage DTO from one repository read."""

    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        repository: EodCoverageRepository | None = None,
    ) -> None:
        self.repository = repository or EodCoverageRepository(db_path)

    @staticmethod
    def _public_source(request: CoverageRequest, source: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": "TWSE" if request.venue == "TWSE" else "TPEx",
            "resource_id": request.resource_id,
            "source_scope": request.source_scope,
            "source_record_reference": source.get("source_record_reference"),
            "source_trade_date": source.get("source_trade_date"),
            "available_at": source.get("source_available_at"),
            "ingested_at": source.get("source_ingested_at"),
            "source_status": source.get("source_status"),
            "coverage_state": source.get("source_coverage_state"),
            "partial_proof_present": bool(source.get("source_proof_present", False)),
            "reason_codes": [source.get("source_reason")] if source.get("source_reason") else [],
        }

    @staticmethod
    def _cursor_for_item(request: CoverageRequest, item: dict[str, Any]) -> str:
        cursor = CoverageCursor(
            context=request.cursor_context(),
            last_key=(
                str(item.get("venue") or ""),
                str(item.get("official_code") or ""),
                item.get("identity_epoch"),
                str(item.get("item_kind") or ""),
                str(item.get("_stable_id") or ""),
            ),
        )
        return cursor.encode()

    def as_of(
        self,
        *,
        venue: str,
        source_trade_date: str,
        knowledge_cutoff_at: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        request = CoverageRequest(
            venue=venue,
            source_trade_date=source_trade_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            limit=limit,
            cursor=cursor,
        )
        decoded = decode_cursor(cursor, request=request) if cursor else None
        projection = self.repository.read(
            request,
            cursor_last_key=decoded.last_key if decoded else None,
        )
        aggregate = projection.aggregate
        status = eod_coverage_visibility_status_v1(
            source_state=str(projection.source.get("source_state") or "unknown"),
            denominator_candidate_count=int(aggregate.get("denominator_candidate_count", 0)),
            denominator_expected_count=int(aggregate.get("denominator_expected_count", 0)),
            denominator_excluded_count=int(aggregate.get("denominator_excluded_count", 0)),
            denominator_unresolved_count=int(aggregate.get("denominator_unresolved_count", 0)),
            denominator_blocked=(
                str(projection.source.get("source_state")) == "blocked"
            ),
        )
        raw_items = list(projection.items)
        has_more = len(raw_items) > request.limit
        page_items = raw_items[:request.limit]
        next_cursor = (
            self._cursor_for_item(request, page_items[-1])
            if has_more and page_items
            else None
        )
        return safe_coverage_response(
            request=request,
            status=status,
            source=self._public_source(request, projection.source),
            aggregate=aggregate,
            items=page_items,
            next_cursor=next_cursor,
        )


__all__ = ["EodCoverageService"]
