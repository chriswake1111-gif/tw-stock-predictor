import React, { useState, useEffect } from "react";
import type { DataOperationsStatusResponse } from "../api/types";
import {
  getDataOperationsStatus,
  triggerSync,
  cancelOperation,
} from "../api/dataOperationsClient";

interface DataOperationsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const DataOperationsModal: React.FC<DataOperationsModalProps> = ({
  isOpen,
  onClose,
}) => {
  const [statusData, setStatusData] = useState<DataOperationsStatusResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setLoading(true);
      const data = await getDataOperationsStatus();
      setStatusData(data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    let active = true;
    getDataOperationsStatus()
      .then((data) => {
        if (active) {
          setStatusData(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      active = false;
    };
  }, [isOpen]);

  if (!isOpen) {
    return null;
  }

  const handleSync = async () => {
    try {
      setLoading(true);
      setError(null);
      await triggerSync();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    try {
      setLoading(true);
      setError(null);
      await cancelOperation();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setLoading(false);
    }
  };

  const readinessLabels: Record<string, string> = {
    not_initialized: "尚未初始化 (需同步市場資料)",
    partial: "部分資料已就緒",
    ready: "市場資料完整",
    stale: "資料需更新",
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="data-ops-title"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        style={{
          backgroundColor: "#fff",
          borderRadius: "8px",
          padding: "24px",
          maxWidth: "500px",
          width: "90%",
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 id="data-ops-title" style={{ margin: 0, fontSize: "1.25rem" }}>
            本機市場資料維護與同步
          </h2>
          <button
            onClick={onClose}
            aria-label="關閉視窗"
            style={{ border: "none", background: "none", cursor: "pointer", fontSize: "1.2rem" }}
          >
            ×
          </button>
        </div>

        {error && (
          <div style={{ color: "#d32f2f", marginTop: "12px", fontSize: "0.9rem" }}>
            錯誤：{error}
          </div>
        )}

        <div style={{ marginTop: "16px", fontSize: "0.95rem" }}>
          <div>
            <strong>資料就緒狀態：</strong>{" "}
            {statusData ? readinessLabels[statusData.readiness] || statusData.readiness : "載入中..."}
          </div>
          {statusData?.market_context_summary && (
            <div style={{ marginTop: "8px", color: "#555" }}>
              <div>最新 EOD 收盤日期：{statusData.market_context_summary.latest_eod_date || "無資料"}</div>
              <div>M1B 貨幣總計資料期別：{statusData.market_context_summary.m1b_latest_period || "無資料"}</div>
            </div>
          )}

          {statusData?.is_syncing && statusData.active_operation && (
            <div
              style={{
                marginTop: "16px",
                padding: "12px",
                backgroundColor: "#e3f2fd",
                borderRadius: "4px",
              }}
            >
              <div><strong>目前同步進度：</strong></div>
              <div>階段：{statusData.active_operation.current_stage}</div>
              <div>狀態：{statusData.active_operation.status}</div>
            </div>
          )}
        </div>

        <div
          style={{
            marginTop: "24px",
            display: "flex",
            justifyContent: "flex-end",
            gap: "12px",
          }}
        >
          {statusData?.is_syncing ? (
            <button
              onClick={handleCancel}
              disabled={loading}
              style={{
                padding: "8px 16px",
                backgroundColor: "#d32f2f",
                color: "#fff",
                border: "none",
                borderRadius: "4px",
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              取消同步
            </button>
          ) : (
            <button
              onClick={handleSync}
              disabled={loading}
              style={{
                padding: "8px 16px",
                backgroundColor: "#1976d2",
                color: "#fff",
                border: "none",
                borderRadius: "4px",
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              開始同步市場資料
            </button>
          )}
          <button
            onClick={onClose}
            style={{
              padding: "8px 16px",
              backgroundColor: "#e0e0e0",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            關閉
          </button>
        </div>
      </div>
    </div>
  );
};
