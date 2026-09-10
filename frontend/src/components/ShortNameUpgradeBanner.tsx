import { useEffect, useRef, useState } from "react";
import { Info, Loader2, Sparkles, CheckCircle2 } from "lucide-react";
import { triggerUniversePrep } from "../api/phase20Client";
import { getOperationDetails } from "../api/dataOperationsClient";
import type { UniverseCoverage } from "../api/types";

interface ShortNameUpgradeBannerProps {
  coverage: UniverseCoverage;
  onUpgradeComplete?: () => void;
  pollIntervalMs?: number;
}

export function ShortNameUpgradeBanner({
  coverage,
  onUpgradeComplete,
  pollIntervalMs = 1500,
}: ShortNameUpgradeBannerProps) {
  const [loading, setLoading] = useState(false);
  const [completedStatus, setCompletedStatus] = useState<"succeeded" | "partial" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollWaitResolveRef = useRef<(() => void) | null>(null);
  const activeControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
      pollWaitResolveRef.current?.();
      pollWaitResolveRef.current = null;
    };
  }, []);

  if (coverage.universe_status === "ready" || coverage.universe_status === "not_initialized") {
    return null;
  }

  async function handleUpgrade() {
    const controller = new AbortController();
    activeControllerRef.current = controller;
    const deadline = setTimeout(() => controller.abort(), 90000);
    try {
      setLoading(true);
      setError(null);
      setCompletedStatus(null);
      const res = await triggerUniversePrep(controller.signal);
      const opId = res.operation_id;

      const startTime = Date.now();
      while (mountedRef.current && Date.now() - startTime <= 90000) {
        let status: string | undefined;
        try {
          const op = await getOperationDetails(opId, controller.signal);
          status = (op as { status?: string }).status;
        } catch (err) {
          if (mountedRef.current) {
            setLoading(false);
            setError(err instanceof Error ? `無法取得更新狀態：${err.message}。請重試。` : "無法取得更新狀態，請重試。");
          }
          return;
        }
        if (["succeeded", "partial"].includes(status || "")) {
          if (mountedRef.current) {
            setLoading(false);
            setCompletedStatus(status as "succeeded" | "partial");
            onUpgradeComplete?.();
          }
          return;
        }
        if (["failed", "cancelled", "interrupted"].includes(status || "")) {
          if (mountedRef.current) {
            setLoading(false);
            setError(`股票簡稱更新已中斷或失敗（狀態：${status}）。請重試。`);
          }
          return;
        }
        await new Promise<void>(resolve => {
          pollWaitResolveRef.current = resolve;
          pollTimerRef.current = setTimeout(() => {
            pollTimerRef.current = null;
            pollWaitResolveRef.current = null;
            resolve();
          }, pollIntervalMs);
        });
      }
      if (mountedRef.current) {
        setLoading(false);
        setError("更新逾時，請稍後重試。");
      }
    } catch (err) {
      if (mountedRef.current) {
        setLoading(false);
        setError(err instanceof Error ? `${err.message}。請重試。` : "無法啟動更新，請重試。");
      }
    } finally {
      clearTimeout(deadline);
      if (activeControllerRef.current === controller) activeControllerRef.current = null;
    }
  }

  const isPartial = coverage.universe_status === "short_names_partial";
  const pct = Math.round(coverage.coverage_ratio * 100);

  return (
    <div
      className="card short-name-upgrade-banner"
      style={{
        maxWidth: 720,
        margin: "1rem auto",
        padding: "1rem 1.25rem",
        background: "var(--color-bg-subtle, #f8fafc)",
        border: "1px solid var(--color-border, #e2e8f0)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: "1rem",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: "0.75rem", flex: 1, minWidth: 260 }}>
        <Info size={20} color="var(--color-primary, #0284c7)" style={{ marginTop: 2, flexShrink: 0 }} />
        <div>
          <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
            {isPartial
              ? `股票簡稱已準備 ${pct}%（${coverage.phase20_materialized_count}/${coverage.total_instruments} 檔）`
              : "可升級股票簡稱以支援中文快速搜尋"}
          </div>
          <div style={{ fontSize: "0.85rem", color: "var(--color-muted, #64748b)", marginTop: "0.2rem" }}>
            升級後可直接以「台積電」、「聯發科」等常用簡稱搜尋股票，無需每次手動輸入完整公司全名。
          </div>
          {error && <div style={{ color: "#b91c1c", fontSize: "0.85rem", marginTop: "0.25rem" }}>{error}</div>}
        </div>
      </div>

      <div>
        {completedStatus === "succeeded" ? (
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "#166534", fontSize: "0.9rem" }}>
            <CheckCircle2 size={16} />
            <span>更新完成</span>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: "0.65rem" }}>
            {completedStatus === "partial" && <span style={{ color: "#166534", fontSize: "0.85rem" }}>部分完成，已更新可用簡稱</span>}
            <button
              type="button"
              className="button button--secondary"
              onClick={handleUpgrade}
              disabled={loading}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.4rem",
                padding: "0.4rem 0.9rem",
                fontSize: "0.88rem",
              }}
            >
              {loading ? (
                <>
                  <Loader2 size={15} className="spin" />
                  <span>更新中...</span>
                </>
              ) : (
                <>
                  <Sparkles size={15} />
                  <span>更新股票簡稱清單</span>
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
