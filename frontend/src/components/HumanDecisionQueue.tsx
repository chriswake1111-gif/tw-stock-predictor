import { AlertTriangle, CheckCircle, ArrowRight, ShieldCheck } from "lucide-react";
import type { HumanDecisionItem } from "../api/types";

interface HumanDecisionQueueProps {
  items: HumanDecisionItem[];
  canonicalSymbol: string;
  onActionClick?: (item: HumanDecisionItem) => void;
}

export function HumanDecisionQueue({
  items,
  canonicalSymbol,
  onActionClick,
}: HumanDecisionQueueProps) {
  if (!items || items.length === 0) {
    return (
      <div
        className="card"
        style={{
          padding: "1.25rem",
          background: "var(--color-bg-subtle, #f8fafc)",
          border: "1px dashed var(--color-border, #cbd5e1)",
          display: "flex",
          alignItems: "center",
          gap: "0.75rem",
        }}
      >
        <CheckCircle size={20} color="#166534" />
        <span style={{ fontSize: "0.95rem", color: "#166534", fontWeight: 600 }}>
          本標的無待辦人工決策事項，所有模型依核准參數執行。
        </span>
      </div>
    );
  }

  return (
    <div className="card human-decision-queue" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.5rem" }}>
        <ShieldCheck size={22} color="var(--color-primary, #0284c7)" />
        <h2 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>
          待人工審查與決策隊列（{items.length} 項）
        </h2>
      </div>

      <p style={{ color: "var(--color-muted, #64748b)", fontSize: "0.9rem", margin: "0 0 1.25rem", lineHeight: 1.5 }}>
        依杜金龍理論模型規範，系統嚴禁自動合成未經證實的預估值或虛構轉折錨點。
        客觀市場事實（收盤價、成交量）已如實呈現，以下事項需由研究員進行實質審查並輸入核准。
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        {items.map((item) => (
          <div
            key={item.item_id}
            style={{
              padding: "1rem 1.25rem",
              borderRadius: 8,
              border: "1px solid #fed7aa",
              background: "#fffbeb",
              display: "flex",
              flexDirection: "column",
              gap: "0.5rem",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    padding: "0.15rem 0.5rem",
                    borderRadius: 4,
                    background: "#ea580c",
                    color: "#ffffff",
                    letterSpacing: "0.05em",
                  }}
                >
                  {item.rule_id} [{item.evidence_level} 級]
                </span>
                <span style={{ fontWeight: 700, fontSize: "1rem", color: "#7c2d12" }}>
                  {item.title}
                </span>
              </div>

              <button
                type="button"
                className="button button--secondary"
                onClick={() => onActionClick?.(item)}
                style={{
                  fontSize: "0.85rem",
                  padding: "0.3rem 0.75rem",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "0.3rem",
                  borderColor: "#f97316",
                  color: "#9a3412",
                }}
              >
                <span>{item.suggested_action}</span>
                <ArrowRight size={14} />
              </button>
            </div>

            <p style={{ margin: 0, fontSize: "0.88rem", color: "#451a03", lineHeight: 1.5 }}>
              {item.description}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
