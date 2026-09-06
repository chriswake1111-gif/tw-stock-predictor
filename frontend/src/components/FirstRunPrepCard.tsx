import { useState } from "react";
import { Database, Loader2, Sparkles, CheckCircle2, AlertCircle } from "lucide-react";
import { triggerUniversePrep } from "../api/phase20Client";
import { getOperationDetails } from "../api/dataOperationsClient";

interface FirstRunPrepCardProps {
  onPreparationComplete?: () => void;
  pollIntervalMs?: number;
}

export function FirstRunPrepCard({
  onPreparationComplete,
  pollIntervalMs = 1500,
}: FirstRunPrepCardProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handlePrepare() {
    try {
      setLoading(true);
      setError(null);
      const res = await triggerUniversePrep();
      const opId = res.operation_id;

      // Poll until operation finishes
      const startTime = Date.now();
      const checkStatus = async (): Promise<boolean> => {
        try {
          if (Date.now() - startTime > 90000) {
            setLoading(false);
            setError("資料準備逾時，請稍後重試。");
            return true;
          }
          const op = await getOperationDetails(opId);
          const status = (op as { status?: string }).status;
          if (status === "succeeded" || status === "partial") {
            setLoading(false);
            setSuccess(true);
            onPreparationComplete?.();
            return true;
          } else if (status === "failed" || status === "cancelled" || status === "interrupted") {
            setLoading(false);
            setError(`股票清單準備已中斷或失敗（狀態：${status}），請檢查系統日誌。`);
            return true;
          }
        } catch {
          // ignore transient poll error
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
      setError(err instanceof Error ? err.message : "無法啟動資料作業");
    }
  }

  return (
    <div className="card first-run-card" style={{ maxWidth: 640, margin: "2rem auto", padding: "2rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1rem" }}>
        <div
          style={{
            width: 48,
            height: 48,
            borderRadius: "50%",
            background: "var(--color-primary-subtle, #e0f2fe)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--color-primary, #0284c7)",
          }}
        >
          <Database size={26} />
        </div>
        <div>
          <h2 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>首次使用引導：準備股票清單</h2>
          <p style={{ color: "var(--color-muted, #64748b)", margin: "0.25rem 0 0", fontSize: "0.9rem" }}>
            本機尚未建立臺灣上市櫃股票清單主檔。
          </p>
        </div>
      </div>

      <p style={{ lineHeight: 1.6, color: "var(--color-foreground, #1e293b)", fontSize: "0.95rem" }}>
        本系統採用「本地優先 (Local-First)」架構，所有股票代號與簡稱均儲存於本機資料庫。點擊下方按鈕將自官方開放資料下載並標準化上市櫃股票主檔，完成後即可支援全市場快速代號與中文簡稱搜尋。
      </p>

      {error && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "0.75rem",
            background: "#fee2e2",
            color: "#991b1b",
            borderRadius: 6,
            marginBottom: "1rem",
            fontSize: "0.9rem",
          }}
        >
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      )}

      {success ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "0.75rem",
            background: "#dcfce7",
            color: "#166534",
            borderRadius: 6,
            fontSize: "0.9rem",
          }}
        >
          <CheckCircle2 size={18} />
          <span>股票清單準備完成！您可以開始搜尋股票。</span>
        </div>
      ) : (
        <button
          type="button"
          className="button button--primary"
          onClick={handlePrepare}
          disabled={loading}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "0.6rem 1.4rem",
            fontSize: "1rem",
            cursor: loading ? "not-allowed" : "pointer",
          }}
        >
          {loading ? (
            <>
              <Loader2 size={18} className="spin" />
              <span>正在準備上市櫃股票主檔...</span>
            </>
          ) : (
            <>
              <Sparkles size={18} />
              <span>準備股票清單</span>
            </>
          )}
        </button>
      )}
    </div>
  );
}
