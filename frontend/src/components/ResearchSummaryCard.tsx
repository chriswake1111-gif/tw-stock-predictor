import { Calendar, DollarSign, Activity, HelpCircle } from "lucide-react";
import type { ResearchSummaryResponse } from "../api/types";
import {
  formatPrice,
  formatTurnover,
  formatRatioPercent,
  getCloseStatusPill,
  getValuationStatusPill,
  getTechnicalStatusPill,
} from "../lib/humanStatusAdapter";

interface ResearchSummaryCardProps {
  summary: ResearchSummaryResponse;
  onOpenAuditDrawer?: () => void;
}

export function ResearchSummaryCard({
  summary,
  onOpenAuditDrawer,
}: ResearchSummaryCardProps) {
  const m = summary?.market_context || {
    settled_trade_date: null,
    official_close: null,
    close_status: "insufficient_data",
    close_reason: null,
    currency: "TWD",
    unit: "TWD_per_share",
    is_market_closed: false,
    market_status_label: "尚未有結算行情",
    market_turnover_total: null,
    market_turnover_status: "insufficient_data",
    cbc_m1b_ratio: null,
    cbc_status: "insufficient_data",
  };
  const closePill = getCloseStatusPill(m.close_status, m.close_reason);
  const valPill = getValuationStatusPill(
    summary?.valuation_context?.status || "needs_human_judgment",
    summary?.valuation_context?.reason_code
  );
  const techPill = getTechnicalStatusPill(
    summary?.technical_context?.status || "needs_human_judgment",
    summary?.technical_context?.reason_code
  );

  return (
    <div className="card research-summary-card" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
      {/* Header with Title & Badges */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1.25rem",
          borderBottom: "1px solid var(--color-border, #e2e8f0)",
          paddingBottom: "1rem",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
            <h1 style={{ fontSize: "1.75rem", fontWeight: 800, margin: 0 }}>
              {summary.official_code} {summary.short_name || summary.company_name}
            </h1>
            <span
              style={{
                fontSize: "0.85rem",
                fontWeight: 600,
                padding: "0.2rem 0.6rem",
                borderRadius: 4,
                background: "var(--color-bg-subtle, #f1f5f9)",
                color: "var(--color-muted, #475569)",
              }}
            >
              {summary.venue}
            </span>
            <span
              style={{
                fontSize: "0.85rem",
                fontWeight: 600,
                padding: "0.2rem 0.6rem",
                borderRadius: 4,
                background: m.is_market_closed ? "#fef3c7" : "#e0f2fe",
                color: m.is_market_closed ? "#92400e" : "#0369a1",
              }}
            >
              {m.market_status_label}
            </span>
          </div>
          {summary.company_name && summary.short_name && summary.company_name !== summary.short_name && (
            <div style={{ fontSize: "0.9rem", color: "var(--color-muted, #64748b)", marginTop: "0.25rem" }}>
              {summary.company_name}
            </div>
          )}
        </div>

        {onOpenAuditDrawer && (
          <button
            type="button"
            className="button button--secondary"
            onClick={onOpenAuditDrawer}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.4rem",
              fontSize: "0.85rem",
              padding: "0.35rem 0.75rem",
            }}
          >
            <HelpCircle size={15} />
            <span>資料審計抽屜</span>
          </button>
        )}
      </div>

      {/* Grid of Key Facts */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: "1.25rem",
          marginBottom: "1.25rem",
        }}
      >
        {/* Official Close */}
        <div style={{ padding: "0.75rem 1rem", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--color-muted, #64748b)", fontSize: "0.85rem", marginBottom: "0.25rem" }}>
            <DollarSign size={15} />
            <span>官方收盤價</span>
          </div>
          <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--color-foreground, #0f172a)" }}>
            {formatPrice(m.official_close, m.currency)}
          </div>
          <div style={{ marginTop: "0.3rem" }}>
            <span
              style={{
                fontSize: "0.75rem",
                fontWeight: 600,
                padding: "0.15rem 0.45rem",
                borderRadius: 4,
                background: m.close_status === "available" ? "#dcfce7" : "#fee2e2",
                color: m.close_status === "available" ? "#166534" : "#991b1b",
              }}
              title={closePill.description}
            >
              {closePill.label}
            </span>
          </div>
        </div>

        {/* Settled Trade Date */}
        <div style={{ padding: "0.75rem 1rem", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--color-muted, #64748b)", fontSize: "0.85rem", marginBottom: "0.25rem" }}>
            <Calendar size={15} />
            <span>最新結算交易日</span>
          </div>
          <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "var(--color-foreground, #0f172a)" }}>
            {m.settled_trade_date || "尚未結算"}
          </div>
          <div style={{ fontSize: "0.8rem", color: "var(--color-muted, #64748b)", marginTop: "0.3rem" }}>
            每日收盤 14:30 正式結算
          </div>
        </div>

        {/* Market Turnover */}
        <div style={{ padding: "0.75rem 1rem", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--color-muted, #64748b)", fontSize: "0.85rem", marginBottom: "0.25rem" }}>
            <Activity size={15} />
            <span>雙市場成交總金額</span>
          </div>
          <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "var(--color-foreground, #0f172a)" }}>
            {formatTurnover(m.market_turnover_total)}
          </div>
          <div style={{ fontSize: "0.8rem", color: "var(--color-muted, #64748b)", marginTop: "0.3rem" }}>
            TWSE＋TPEx 官方總和
          </div>
        </div>

        {/* CBC M1B Status */}
        <div style={{ padding: "0.75rem 1rem", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--color-muted, #64748b)", fontSize: "0.85rem", marginBottom: "0.25rem" }}>
            <Activity size={15} />
            <span>成交金額 / M1B 比率</span>
          </div>
          <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "var(--color-foreground, #0f172a)" }}>
            {m.cbc_status === "available" ? formatRatioPercent(m.cbc_m1b_ratio) : "尚無最新貨幣資料"}
          </div>
          <div style={{ fontSize: "0.8rem", color: "var(--color-muted, #64748b)", marginTop: "0.3rem" }}>
            {m.cbc_status === "available" ? "央行貨幣供給對比" : "補充指標（不影響個股研究）"}
          </div>
        </div>
      </div>

      {/* Status Indicators Row */}
      <div
        style={{
          display: "flex",
          gap: "1rem",
          flexWrap: "wrap",
          padding: "0.75rem 1rem",
          background: "var(--color-bg-subtle, #f8fafc)",
          borderRadius: 8,
          fontSize: "0.85rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span style={{ color: "var(--color-muted, #64748b)" }}>估值模型：</span>
          <span
            style={{
              padding: "0.15rem 0.5rem",
              borderRadius: 4,
              background: summary.valuation_context.status === "available" ? "#dcfce7" : "#e0e7ff",
              color: summary.valuation_context.status === "available" ? "#166534" : "#3730a3",
              fontWeight: 600,
            }}
            title={valPill.description}
          >
            {valPill.label}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span style={{ color: "var(--color-muted, #64748b)" }}>波浪與費氏：</span>
          <span
            style={{
              padding: "0.15rem 0.5rem",
              borderRadius: 4,
              background: summary.technical_context.status === "available" ? "#dcfce7" : "#e0e7ff",
              color: summary.technical_context.status === "available" ? "#166534" : "#3730a3",
              fontWeight: 600,
            }}
            title={techPill.description}
          >
            {techPill.label}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span style={{ color: "var(--color-muted, #64748b)" }}>本益比 (PE)：</span>
          <span style={{ color: "var(--color-muted, #94a3b8)" }}>尚無可用資料</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span style={{ color: "var(--color-muted, #64748b)" }}>淨值比 (PB)：</span>
          <span style={{ color: "var(--color-muted, #94a3b8)" }}>尚無可用資料</span>
        </div>
      </div>
    </div>
  );
}
