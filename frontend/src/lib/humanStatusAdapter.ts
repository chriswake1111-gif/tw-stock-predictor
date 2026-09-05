/**
 * Human Status Adapter for Phase 20 Stock Research Workspace.
 * Translates low-level engineering status codes and numeric facts
 * into unambiguous, professional Traditional Chinese status descriptions and pill styles.
 */

export interface StatusPillConfig {
  label: string;
  className: string;
  description?: string;
}

export function formatPrice(value: number | null | undefined, currency: string = "TWD"): string {
  if (value === null || value === undefined || isNaN(value)) {
    return "尚無報價";
  }
  const unit = currency === "TWD" ? "元" : currency;
  return `${value.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`;
}

export function formatTurnover(turnoverTwd: number | null | undefined): string {
  if (turnoverTwd === null || turnoverTwd === undefined || isNaN(turnoverTwd)) {
    return "尚無成交額資料";
  }
  const yi = turnoverTwd / 1e8;
  return `${yi.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 億元`;
}

export function formatRatioPercent(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || isNaN(ratio)) {
    return "尚無比率資料";
  }
  return `${(ratio * 100).toFixed(2)}%`;
}

export function getCloseStatusPill(
  closeStatus: string,
  closeReason?: string | null
): StatusPillConfig {
  if (closeStatus === "available") {
    return {
      label: "官方結算價",
      className: "status-pill status-pill--success",
      description: "來自臺灣證券交易所／櫃買中心正式結算之收盤價",
    };
  }
  if (closeReason === "symbol_observation_not_yet_materialized_for_settled_session") {
    return {
      label: "標的當日行情尚未入庫",
      className: "status-pill status-pill--warning",
      description: "當日全市場快照已結算，但該個股行情尚未完成材料化或未開盤",
    };
  }
  return {
    label: "行情未就緒",
    className: "status-pill status-pill--warning",
    description: "尚未取得有效官方日收盤價",
  };
}

export function getValuationStatusPill(
  status: string,
  reasonCode?: string | null
): StatusPillConfig {
  if (status === "available") {
    return {
      label: "估值模型已推算",
      className: "status-pill status-pill--success",
      description: "已依核准之 Forward EPS 及本益比推算目標價區間",
    };
  }
  if (status === "needs_human_judgment") {
    return {
      label: "需人工核准 Forward EPS",
      className: "status-pill status-pill--info",
      description: "杜金龍估值模型嚴禁自動合成預估 EPS，須由研究員核准正式假設",
    };
  }
  return {
    label: "資料不足",
    className: "status-pill status-pill--muted",
    description: reasonCode || "無法進行估值推算",
  };
}

export function getTechnicalStatusPill(
  status: string,
  reasonCode?: string | null
): StatusPillConfig {
  if (status === "available") {
    return {
      label: "波浪錨點已核准",
      className: "status-pill status-pill--success",
      description: "已依核准之起算點與轉折錨點推算費氏分割目標",
    };
  }
  if (status === "needs_human_judgment") {
    return {
      label: "待指定波浪轉折錨點",
      className: "status-pill status-pill--info",
      description: "波浪理論黃金分割推算需要先確認關鍵高低點，嚴禁無錨點自動生成",
    };
  }
  return {
    label: "資料不足",
    className: "status-pill status-pill--muted",
    description: reasonCode || "尚未指定技術波段起算點",
  };
}

export function getScreeningStatusPill(status: string): StatusPillConfig {
  if (status === "available") {
    return {
      label: "已載入",
      className: "status-pill status-pill--success",
    };
  }
  return {
    label: "尚無可用資料",
    className: "status-pill status-pill--muted",
    description: "本機金融資料庫尚未材料化該指標，嚴禁連線外部抓取假值",
  };
}
