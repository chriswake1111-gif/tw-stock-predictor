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
from src.services.forward_eps_service import ForwardEPSService
from src.services.technical_scenario_service import TechnicalScenarioService


class CurrentResearchService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        repository: CurrentResearchRepository | None = None,
    ):
        self.db_path = getattr(repository, "db_path", os.getenv("DATABASE_PATH", db_path))
        self.repository = repository or CurrentResearchRepository(self.db_path)

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
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
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

            # Use the existing governed engines on this same read snapshot.
            # Approved inputs alone never imply that a result was calculated.
            valuation = ForwardEPSService(self.db_path, auto_migrate=False).analyze_preloaded(
                conn, canonical_symbol, cutoff
            )
            technical = TechnicalScenarioService(self.db_path, auto_migrate=False).analyze_preloaded(
                conn, canonical_symbol, cutoff
            )
            if technical.get("reason") == "manual_anchor_required":
                technical = TechnicalScenarioService(self.db_path, auto_migrate=False).analyze_preloaded(
                    conn, official_code, cutoff
                )
            valuation_ctx = ValuationContextSummary(
                status=valuation["status"],
                reason_code=valuation.get("reason"),
                target_matrix=valuation.get("target_matrix", []),
            )
            technical_ctx = TechnicalContextSummary(
                status=technical["status"],
                reason_code=technical.get("reason"),
                targets=technical if technical.get("scenarios") else None,
            )
            if valuation_ctx.status in {"insufficient_data", "needs_human_input"}:
                valuation_ctx.status = "needs_human_judgment"
                missing_pe = valuation.get("reason") == "approved_symbol_pe_missing_at_knowledge_cutoff"
                decision_queue.append(HumanDecisionItem(
                    item_id="val_04_pe" if missing_pe else "val_02_forward_eps",
                    title="補齊估值輸入與核准",
                    rule_id="VAL-04" if missing_pe else "VAL-02",
                    evidence_level="A", description="估值需要有效的預估 EPS 與本益比情境核准。",
                    suggested_action="請檢查估值資料與核准狀態；行情仍可直接查閱。",
                ))
            if technical_ctx.status == "needs_human_input":
                technical_ctx.status = "needs_human_judgment"
            if technical_ctx.status != "available":
                decision_queue.append(HumanDecisionItem(
                    item_id="fb_wave_anchor", title="檢查波浪錨點", rule_id="FB-03/FB-04", evidence_level="A",
                    description="目前沒有可計算的有效核准錨點。", suggested_action="請確認錨點與核准狀態。",
                ))
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
