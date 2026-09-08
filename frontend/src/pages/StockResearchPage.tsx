import { useState, useEffect, useCallback, useRef } from "react";
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

  const requestRef = useRef<AbortController | null>(null);
  const [updateNotice, setUpdateNotice] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    const { signal } = controller;
    const timeout = setTimeout(() => controller.abort(), 180000);
    const isCurrent = () => requestRef.current === controller;
    const checkCurrent = () => {
      if (!isCurrent() || signal.aborted) throw new Error("資料準備逾時，請重試更新。");
    };
    const pause = () => new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(new Error("資料準備已停止。"));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, 1500);
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
    const operationReason = (op: Record<string, unknown>) => {
      const items = (op.items || []) as { error_detail?: string }[];
      const reason = String(op.error_detail || items.find(item => item.error_detail)?.error_detail || "");
      if (/calendar|authorized trading session/i.test(reason)) {
        return "缺少官方交易日證據或證據衝突，行情尚未完成更新。請稍後重試；持續失敗時可至進階與審計查看資料作業。";
      }
      return reason ? `資料更新未完成：${reason}` : "部分資料尚未就緒，請至進階與審計查看資料作業。";
    };
    try {
      setLoading(true);
      setSummary(null);
      setError(null);
      setUpdateNotice(null);
      if (!asOf) {
        // Preserve readable local data when a user-requested update fails.
        const local = await getResearchSummary(canonicalSymbol, undefined, signal).catch(() => null);
        checkCurrent();
        if (local) setSummary(local);
        setBootstrapStatus("正在向官方來源檢查並更新行情...");
        const seenOperations = new Set<string>();
        while (true) {
          checkCurrent();
          const result = await bootstrapSymbol(canonicalSymbol, true, signal);
          checkCurrent();
          if (result.status === "ready") break;
          if (!result.operation_id || !["preparing", "waiting_for_data_operation"].includes(result.status)) {
            throw new Error("資料準備未能啟動，請至進階與審計查看資料作業。");
          }
          if (seenOperations.has(result.operation_id)) {
            throw new Error("既有資料作業已結束但標的尚未就緒，請重試更新。");
          }
          seenOperations.add(result.operation_id);
          setBootstrapStatus(result.status === "preparing"
            ? "正在取得並驗證官方行情資料..." : "正在等待既有資料作業完成...");
          const pollDeadline = Date.now() + 90000;
          let op: Record<string, unknown>;
          while (true) {
            checkCurrent();
            if (Date.now() >= pollDeadline) throw new Error("資料準備逾時，請至進階與審計查看作業狀態。");
            op = await getOperationDetails(result.operation_id, signal);
            checkCurrent();
            if (["succeeded", "partial", "failed", "cancelled", "interrupted"].includes(String(op.status))) break;
            await pause();
          }
          if (["failed", "cancelled", "interrupted"].includes(String(op.status))) {
            throw new Error(operationReason(op));
          }
          if (op.status === "partial") {
            setUpdateNotice(operationReason(op));
            break; // Partial is terminal: never automatically create another operation.
          }
          if (result.status === "preparing") {
            setUpdateNotice("已完成官方來源檢查；下列日期為本機已取得的行情日期，來源可能尚未發布下一交易日資料。");
            break;
          }
          // Only a successful unrelated operation permits a target-specific follow-up.
        }
      }
      setBootstrapStatus("正在載入研究資料...");
      const sum = await getResearchSummary(canonicalSymbol, asOf, signal);
      checkCurrent();
      setSummary(sum);
    } catch (err) {
      if (isCurrent()) setError(signal.aborted ? "資料準備逾時，請重試更新。"
        : err instanceof Error ? err.message : "載入個股研究資料失敗");
    } finally {
      clearTimeout(timeout);
      if (isCurrent()) {
        setLoading(false);
        setBootstrapStatus(null);
      }
    }
  }, [canonicalSymbol, asOf]);

  useEffect(() => {
    const timer = setTimeout(() => { void loadData(); }, 0);
    return () => {
      clearTimeout(timer);
      const request = requestRef.current;
      requestRef.current = null;
      request?.abort();
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
            onClick={() => { void loadData(); }}
            disabled={loading}
            style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}
            title={asOf ? "重新載入歷史研究" : "連線官方來源更新研究資料"}
          >
            <RefreshCw size={15} className={loading ? "spin" : ""} />
            <span>{asOf ? "重新載入" : "更新資料"}</span>
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
          <span style={{ fontSize: "0.9rem", fontWeight: 600 }}>指定歷史時間點：</span>
          <input
            type="text"
            value={historicalInput}
            onChange={(e) => setHistoricalInput(e.target.value)}
            placeholder="請輸入日期時間（例如 2026-09-04 16:00）"
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
            正在檢查來源與交易日證據；若資料不足，將顯示原因。
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
            onClick={() => { void loadData(); }}
            style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
          >
            <RefreshCw size={15} />
            <span>重試同步</span>
          </button>
        </div>
      )}

      {!loading && updateNotice && <p role="status">{updateNotice}</p>}
      {!loading && error && summary && <p role="status">更新未完成，以下保留本機資料；請留意行情日期。</p>}
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
