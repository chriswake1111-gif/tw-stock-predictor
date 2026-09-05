import { X, Shield, FileText, CheckCircle2, AlertCircle } from "lucide-react";
import type { AuditReferenceSummary } from "../../api/types";

interface AuditDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  audit: AuditReferenceSummary;
  canonicalSymbol: string;
}

export function AuditDrawer({
  isOpen,
  onClose,
  audit,
  canonicalSymbol,
}: AuditDrawerProps) {
  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        display: "flex",
        justifyContent: "flex-end",
        backgroundColor: "rgba(0, 0, 0, 0.4)",
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 480,
          height: "100%",
          background: "var(--color-surface, #ffffff)",
          boxShadow: "-4px 0 24px rgba(0, 0, 0, 0.15)",
          padding: "1.75rem",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "1.5rem",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Shield size={20} color="var(--color-primary, #0284c7)" />
            <h3 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 700 }}>
              數據來源與模型審計抽屜
            </h3>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="關閉審計抽屜"
          >
            <X size={20} />
          </button>
        </div>

        {/* Target Info */}
        <div style={{ padding: "0.75rem 1rem", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: 6, fontSize: "0.9rem" }}>
          <div><strong>研究標的：</strong> {canonicalSymbol}</div>
          <div style={{ marginTop: "0.25rem" }}><strong>模型版本：</strong> {audit.model_version}</div>
        </div>

        {/* Snapshot Provenance */}
        <div>
          <h4 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.75rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
            <FileText size={16} />
            <span>底層行情快照材料（Provenance）</span>
          </h4>
          <div style={{ fontSize: "0.88rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            <div>
              <span style={{ color: "var(--color-muted, #64748b)" }}>快照識別碼：</span>
              <div style={{ fontFamily: "monospace", fontSize: "0.8rem", wordBreak: "break-all", background: "var(--color-bg-subtle, #f1f5f9)", padding: "0.3rem 0.5rem", borderRadius: 4, marginTop: "0.2rem" }}>
                {audit.source_snapshot_id || "未材料化（insufficient_data）"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--color-muted, #64748b)" }}>官方發布時間：</span>
              <div>{audit.available_at || "未知"}</div>
            </div>
            <div>
              <span style={{ color: "var(--color-muted, #64748b)" }}>本機入庫時間：</span>
              <div>{audit.ingested_at || "未知"}</div>
            </div>
          </div>
        </div>

        {/* Du Model Rules Trace */}
        <div>
          <h4 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.75rem" }}>
            依循杜金龍模型規則清單
          </h4>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
            {audit.rule_traces.map((rule) => (
              <span
                key={rule}
                style={{
                  fontSize: "0.8rem",
                  fontWeight: 600,
                  padding: "0.25rem 0.6rem",
                  borderRadius: 4,
                  background: "var(--color-primary-subtle, #e0f2fe)",
                  color: "var(--color-primary, #0369a1)",
                }}
              >
                {rule}
              </span>
            ))}
          </div>
        </div>

        {/* Boundary & Non-Regression Guarantees */}
        <div style={{ padding: "1rem", borderRadius: 8, background: "#f0fdf4", border: "1px solid #bbf7d0", fontSize: "0.85rem", color: "#166534" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 700, marginBottom: "0.3rem" }}>
            <CheckCircle2 size={16} />
            <span>不可動搖的系統安全保證</span>
          </div>
          <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.2rem", lineHeight: 1.6 }}>
            <li>搜尋與輸入過程保證<strong>零外部網路請求（Zero External Egress）</strong>。</li>
            <li>杜金龍 Forward EPS 與波浪目標價<strong>嚴禁合成假值</strong>，未核准前一律顯示資料未就緒。</li>
            <li>系統絕無連接任何券商下單 API，僅提供情境推演與決策支援。</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
