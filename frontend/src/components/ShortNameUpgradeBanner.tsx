import { useState } from "react";
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
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (coverage.universe_status === "ready" || coverage.universe_status === "not_initialized") {
    return null;
  }

  async function handleUpgrade() {
    try {
      setLoading(true);
      setError(null);
      const res = await triggerUniversePrep();
      const opId = res.operation_id;

      const startTime = Date.now();
      const checkStatus = async (): Promise<boolean> => {
        try {
          if (Date.now() - startTime > 90000) {
            setLoading(false);
            setError("更新逾時，請稍後重試。");
            return true;
          }
          const op = await getOperationDetails(opId);
          const status = (op as { status?: string }).status;
          if (status === "succeeded" || status === "partial") {
            setLoading(false);
            setSuccess(true);
            onUpgradeComplete?.();
            return true;
          } else if (status === "failed" || status === "cancelled" || status === "interrupted") {
            setLoading(false);
            setError(`股票簡稱更新已中斷或失敗（狀態：${status}）。`);
            return true;
          }
        } catch {
          // ignore
        }
        return false;
      };

      const isDone = await checkStatus();
      if (!isDone) {
        const interval = setInterval(async () => {
          const finished = await checkStatus();
          if (finished) {
            clearInterval(interval);
          }
        }, pollIntervalMs);
      }
    } catch (err) {
      setLoading(false);
      setError(err instanceof Error ? err.message : "無法啟動更新");
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
        {success ? (
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "#166534", fontSize: "0.9rem" }}>
            <CheckCircle2 size={16} />
            <span>更新完成</span>
          </div>
        ) : (
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
        )}
      </div>
    </div>
  );
}
