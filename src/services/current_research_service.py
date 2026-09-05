"""Current research service orchestrating latest-settled context retrieval and research summary."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from src.domain.research_summary import (
    AuditReferenceSummary,
    HumanDecisionItem,
    MarketContextSummary,
    ResearchSummaryResponse,
    ScreeningContextSummary,
    TechnicalContextSummary,
    ValuationContextSummary,
)
from src.domain.universe import parse_canonical_symbol, validate_knowledge_cutoff_at
from src.domain.valuation import utc_now_timestamp
from src.repositories.current_research_repository import CurrentResearchRepository


class CurrentResearchService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        repository: CurrentResearchRepository | None = None,
    ):
        self.repository = repository or CurrentResearchRepository(
            os.getenv("DATABASE_PATH", db_path)
        )

    def get_context(
        self, canonical_symbol: str, *, knowledge_cutoff_at: str | None = None
    ) -> dict[str, Any]:
        return self.repository.resolve_latest_settled_context(
            canonical_symbol=canonical_symbol,
            cutoff=knowledge_cutoff_at,
        )

    def get_summary(
        self, canonical_symbol: str, *, knowledge_cutoff_at: str | None = None
    ) -> dict[str, Any] | None:
        """Compose Research Summary Response adhering strictly to Phase 20 Section 1.5 mapping matrix."""
        cutoff = (
            validate_knowledge_cutoff_at(knowledge_cutoff_at)
            if knowledge_cutoff_at
            else validate_knowledge_cutoff_at(utc_now_timestamp())
        )
        venue, official_code = parse_canonical_symbol(canonical_symbol)
        venue_str = venue.value

        conn = sqlite3.connect(self.repository.db_path)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

            company_name = None
            short_name = None

            if "universe_instruments" in tables:
                u_row = conn.execute(
                    """
                    SELECT ui.display_name AS instrument_display_name,
                           uir.display_name AS revision_display_name,
                           uir.short_name
                    FROM universe_instruments ui
                    LEFT JOIN universe_instrument_revisions uir
                      ON uir.instrument_id = ui.instrument_id
                     AND uir.available_at <= ? AND uir.ingested_at <= ?
                    WHERE ui.venue = ? AND ui.official_code = ?
                    ORDER BY uir.revision_number DESC, uir.ingested_at DESC
                    LIMIT 1
                    """,
                    (cutoff, cutoff, venue_str, official_code),
                ).fetchone()

                if u_row is None:
                    return None

                company_name = (
                    u_row["revision_display_name"]
                    or u_row["instrument_display_name"]
                    or None
                )
                short_name = u_row["short_name"] or None

            settled = self.repository.resolve_latest_settled_context(
                canonical_symbol, cutoff=cutoff, conn=conn
            )
            off_close = settled["official_close"]
            m_ctx = settled["market_context"]

            cbc_m1b_ratio = None
            cbc_status = "insufficient_data"
            if "cbc_m1b_monthly" in tables and m_ctx.get("market_turnover_total"):
                m1b_row = conn.execute(
                    """
                    SELECT value_twd
                    FROM cbc_m1b_monthly
                    WHERE status = 'available'
                      AND available_at <= ? AND ingested_at <= ?
                    ORDER BY available_at DESC, period DESC, revision DESC, ingested_at DESC
                    LIMIT 1
                    """,
                    (cutoff, cutoff),
                ).fetchone()
                if m1b_row and m1b_row["value_twd"] is not None:
                    try:
                        m1b_val = float(m1b_row["value_twd"])
                        if m1b_val > 0:
                            cbc_m1b_ratio = round(
                                m_ctx["market_turnover_total"] / m1b_val, 6
                            )
                            cbc_status = "available"
                    except (ValueError, TypeError):
                        pass

            market_turnover_status = (
                "available"
                if m_ctx.get("market_turnover_total") is not None
                else "insufficient_data"
            )

            decision_queue: list[HumanDecisionItem] = []

            has_approved_forward_eps = False
            if "forward_eps_observations" in tables and "valuation_approvals" in tables:
                f_row = conn.execute(
                    """
                    SELECT 1
                    FROM forward_eps_observations f
                    JOIN valuation_approvals a ON a.resource_id = f.id
                    WHERE f.symbol = ?
                      AND f.status = 'active'
                      AND a.decision = 'approved'
                      AND a.rule_id = 'VAL-02'
                      AND a.available_at <= ? AND a.ingested_at <= ?
                    LIMIT 1
                    """,
                    (canonical_symbol, cutoff, cutoff),
                ).fetchone()
                if f_row:
                    has_approved_forward_eps = True

            if has_approved_forward_eps:
                valuation_ctx = ValuationContextSummary(
                    status="available",
                    reason_code=None,
                    target_matrix=[],
                )
            else:
                valuation_ctx = ValuationContextSummary(
                    status="needs_human_judgment",
                    reason_code="forward_eps_missing_at_knowledge_cutoff",
                    target_matrix=[],
                )
                decision_queue.append(
                    HumanDecisionItem(
                        item_id="val_02_forward_eps",
                        title="核准預估 EPS（Forward EPS）",
                        rule_id="VAL-02",
                        evidence_level="A",
                        description="依杜金龍估值模型規範，預估 EPS 屬核心假設，系統嚴禁自動合成假值，必須由研究員輸入並核准。",
                        suggested_action="請至估值決策面板輸入經核准的 Forward EPS 以推算目標價區間。",
                        status="pending",
                    )
                )

            has_approved_anchors = False
            if (
                "manual_anchor_set_revisions" in tables
                and "technical_anchor_approvals" in tables
            ):
                t_row = conn.execute(
                    """
                    SELECT 1
                    FROM manual_anchor_set_revisions r
                    JOIN technical_anchor_approvals a ON a.anchor_revision_id = r.id
                    WHERE r.symbol = ?
                      AND r.status = 'active'
                      AND a.decision = 'approved'
                      AND a.rule_id IN ('FB-03', 'FB-04')
                      AND a.available_at <= ?
                    LIMIT 1
                    """,
                    (canonical_symbol, cutoff),
                ).fetchone()
                if t_row:
                    has_approved_anchors = True

            if has_approved_anchors:
                technical_ctx = TechnicalContextSummary(
                    status="available",
                    reason_code=None,
                    targets=None,
                )
            else:
                technical_ctx = TechnicalContextSummary(
                    status="needs_human_judgment",
                    reason_code="manual_anchor_required",
                    targets=None,
                )
                decision_queue.append(
                    HumanDecisionItem(
                        item_id="fb_wave_anchor",
                        title="指定波浪理論關鍵轉折錨點",
                        rule_id="FB-03/FB-04",
                        evidence_level="A",
                        description="波浪黃金分割（0.382／等幅）推算需要先確認關鍵高低點錨點，禁止無錨點直接合成目標價。",
                        suggested_action="請指定經核准之波浪起算點與轉折錨點以計算目標價。",
                        status="pending",
                    )
                )

            screening_ctx = ScreeningContextSummary()

            snapshot_id = off_close.get("snapshot_id")
            snap_avail = None
            snap_ingest = None
            if snapshot_id and "eod_close_source_snapshots" in tables:
                s_row = conn.execute(
                    "SELECT available_at, ingested_at FROM eod_close_source_snapshots WHERE source_snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()
                if s_row:
                    snap_avail = s_row["available_at"]
                    snap_ingest = s_row["ingested_at"]

            audit_ref = AuditReferenceSummary(
                source_snapshot_id=snapshot_id,
                available_at=snap_avail,
                ingested_at=snap_ingest,
                model_version="2.0.0",
                rule_traces=[
                    "VAL-01",
                    "VAL-02",
                    "FB-03",
                    "FB-04",
                    "ENT-02",
                    "SEL-01",
                ],
            )

            market_summary = MarketContextSummary(
                settled_trade_date=settled["settled_trade_date"],
                official_close=off_close["value"],
                close_status=off_close["status"],
                close_reason=off_close.get("reason"),
                currency=off_close.get("currency") or "TWD",
                unit=off_close.get("unit") or "TWD_per_share",
                is_market_closed=m_ctx["is_market_closed"],
                market_status_label=m_ctx["market_status_label"],
                market_turnover_total=m_ctx["market_turnover_total"],
                market_turnover_status=market_turnover_status,
                cbc_m1b_ratio=cbc_m1b_ratio,
                cbc_status=cbc_status,
            )

            resp = ResearchSummaryResponse(
                canonical_symbol=canonical_symbol,
                official_code=official_code,
                venue=venue_str,
                company_name=company_name,
                short_name=short_name,
                market_context=market_summary,
                valuation_context=valuation_ctx,
                technical_context=technical_ctx,
                screening_context=screening_ctx,
                human_decision_queue=decision_queue,
                audit_reference=audit_ref,
                knowledge_cutoff_at=cutoff,
            )
            return resp.model_dump()
        finally:
            conn.close()


__all__ = ["CurrentResearchService"]
