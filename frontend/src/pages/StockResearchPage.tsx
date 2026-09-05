import { useState, useEffect, useCallback } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Calendar,
  Clock,
  Loader2,
  RefreshCw,
  AlertTriangle,
} from "lucide-react";
import { bootstrapSymbol, getResearchSummary } from "../api/phase20Client";
import { getOperationDetails } from "../api/dataOperationsClient";
import type { ResearchSummaryResponse } from "../api/types";
import { ResearchSummaryCard } from "../components/ResearchSummaryCard";
import { HumanDecisionQueue } from "../components/HumanDecisionQueue";
import { AuditDrawer } from "../components/evidence/AuditDrawer";

export function StockResearchPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const asOf = searchParams.get("as_of") || undefined;
  const canonicalSymbol = (symbol || "2330.TW").toUpperCase();

  const [summary, setSummary] = useState<ResearchSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootstrapStatus, setBootstrapStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false);
  const [historicalInput, setHistoricalInput] = useState(asOf || "");
  const [showTimeMachine, setShowTimeMachine] = useState(Boolean(asOf));

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // In current mode (no as_of), trigger bootstrap readiness check
      if (!asOf) {
        setBootstrapStatus("正在檢查資料就緒狀態...");
        const bRes = await bootstrapSymbol(canonicalSymbol);
        if (bRes.status === "preparing" || bRes.status === "waiting_for_data_operation") {
          setBootstrapStatus("正在材料化最新已結算行情...");
          if (bRes.operation_id) {
            // Poll for completion
            const startTime = Date.now();
            await new Promise<void>((resolve, reject) => {
              const timer = setInterval(async () => {
                if (Date.now() - startTime > 90000) {
                  clearInterval(timer);
                  reject(new Error("資料準備逾時（90秒），請稍後重試。"));
                  return;
                }
                try {
                  const op = await getOperationDetails(bRes.operation_id!);
                  const opStatus = (op as { status?: string }).status;
                  if (opStatus === "completed") {
                    clearInterval(timer);
                    resolve();
                  } else if (opStatus === "failed") {
                    clearInterval(timer);
                    reject(new Error("行情材料化失敗。"));
                  }
                } catch {
                  // transient polling error
                }
              }, 1500);
            });
          }
        }
      }

      setBootstrapStatus("正在載入研究資料...");
      const sum = await getResearchSummary(canonicalSymbol, asOf);
      setSummary(sum);
      setLoading(false);
      setBootstrapStatus(null);
    } catch (err) {
      setLoading(false);
      setBootstrapStatus(null);
      setError(err instanceof Error ? err.message : "載入個股研究資料失敗");
    }
  }, [canonicalSymbol, asOf]);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      if (active) {
        void loadData();
      }
    }, 0);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [loadData]);

  function handleHistoricalSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (historicalInput.trim()) {
      setSearchParams({ as_of: historicalInput.trim() });
    } else {
      setSearchParams({});
    }
  }

  function handleResetToCurrent() {
    setHistoricalInput("");
    setShowTimeMachine(false);
    setSearchParams({});
  }

  return (
    <div className="stock-research-page" style={{ maxWidth: 1024, margin: "0 auto", padding: "1.5rem 1rem" }}>
      {/* Top action bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1.25rem",
        }}
      >
        <button
          type="button"
          className="button button--secondary"
          onClick={() => navigate("/")}
          style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.88rem" }}
        >
          <ArrowLeft size={16} />
          <span>返回標的搜尋</span>
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {asOf ? (
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.4rem",
                padding: "0.3rem 0.75rem",
                borderRadius: 6,
                background: "#fef3c7",
                color: "#92400e",
                fontSize: "0.85rem",
                fontWeight: 600,
              }}
            >
              <Clock size={15} />
              <span>歷史切點模式：{asOf}</span>
              <button
                type="button"
                onClick={handleResetToCurrent}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: "#92400e",
                  textDecoration: "underline",
                  marginLeft: "0.4rem",
                  fontSize: "0.82rem",
                }}
              >
                回到最新
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="button button--secondary"
              onClick={() => setShowTimeMachine((prev) => !prev)}
              style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}
            >
              <Calendar size={15} />
              <span>{showTimeMachine ? "關閉歷史切點" : "切換至歷史切點研究"}</span>
            </button>
          )}

          <button
            type="button"
            className="button button--secondary"
            onClick={loadData}
            disabled={loading}
            style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}
            title="重新檢查最新結算數據"
          >
            <RefreshCw size={15} className={loading ? "spin" : ""} />
            <span>重新整理</span>
          </button>
        </div>
      </div>

      {/* Historical Time Machine Bar */}
      {showTimeMachine && !asOf && (
        <form
          onSubmit={handleHistoricalSubmit}
          className="card"
          style={{
            padding: "0.75rem 1.25rem",
            marginBottom: "1.25rem",
            background: "var(--color-bg-subtle, #f8fafc)",
            display: "flex",
            alignItems: "center",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}
        >
          <Clock size={18} color="var(--color-primary, #0284c7)" />
          <span style={{ fontSize: "0.9rem", fontWeight: 600 }}>歷史切點 (ISO-8601)：</span>
          <input
            type="text"
            value={historicalInput}
            onChange={(e) => setHistoricalInput(e.target.value)}
            placeholder="例如 2026-09-04T16:00:00Z"
            style={{
              padding: "0.4rem 0.75rem",
              borderRadius: 6,
              border: "1px solid var(--color-border, #cbd5e1)",
              fontSize: "0.9rem",
              minWidth: 260,
            }}
          />
          <button type="submit" className="button button--primary" style={{ padding: "0.4rem 0.9rem", fontSize: "0.85rem" }}>
            進入歷史模式
          </button>
        </form>
      )}

      {/* Loading state */}
      {loading && (
        <div
          className="card"
          style={{
            padding: "3rem",
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "1rem",
          }}
        >
          <Loader2 size={36} className="spin" color="var(--color-primary, #0284c7)" />
          <div style={{ fontSize: "1.1rem", fontWeight: 600 }}>
            {bootstrapStatus || "正在載入個股研究工作區..."}
          </div>
          <div style={{ color: "var(--color-muted, #64748b)", fontSize: "0.9rem" }}>
            依杜金龍理論模型規範，正在對齊最新官方結算日行情材料。
          </div>
        </div>
      )}

      {/* Error state */}
      {!loading && error && (
        <div
          className="card"
          style={{
            padding: "2rem",
            border: "1px solid #fecaca",
            background: "#fef2f2",
            color: "#991b1b",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.75rem" }}>
            <AlertTriangle size={24} />
            <h3 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700 }}>載入失敗</h3>
          </div>
          <p style={{ margin: "0 0 1rem", fontSize: "0.95rem" }}>{error}</p>
          <button
            type="button"
            className="button button--primary"
            onClick={loadData}
            style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
          >
            <RefreshCw size={15} />
            <span>重試同步</span>
          </button>
        </div>
      )}

      {/* Loaded summary */}
      {!loading && summary && (
        <>
          <ResearchSummaryCard
            summary={summary}
            onOpenAuditDrawer={() => setAuditDrawerOpen(true)}
          />

          <HumanDecisionQueue
            items={summary.human_decision_queue}
            canonicalSymbol={summary.canonical_symbol}
            onActionClick={(item) => {
              if (item.rule_id === "VAL-02") {
                navigate(`/rules?rule=VAL-02`);
              } else if (item.rule_id.includes("FB")) {
                navigate(`/rules?rule=FB-03`);
              }
            }}
          />

          <AuditDrawer
            isOpen={auditDrawerOpen}
            onClose={() => setAuditDrawerOpen(false)}
            audit={summary.audit_reference}
            canonicalSymbol={summary.canonical_symbol}
          />
        </>
      )}
    </div>
  );
}
