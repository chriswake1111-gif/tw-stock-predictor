import type { SectionStatus } from "../api/types";
import { sectionStatusValues } from "../api/types";

export type RenderableStatus = SectionStatus | "unknown";

export interface StatusPresentation {
  label: string;
  description: string;
  tone: "information" | "attention" | "neutral";
}

const knownStatuses = new Set<string>(sectionStatusValues);

export function normalizeStatus(value: unknown): RenderableStatus {
  return typeof value === "string" && knownStatuses.has(value)
    ? (value as SectionStatus)
    : "unknown";
}

export const statusPresentation: Record<RenderableStatus, StatusPresentation> = {
  available: {
    label: "資料可用",
    description: "此區段具備後端確認的可用資料。",
    tone: "information",
  },
  partial: {
    label: "部分資料可用",
    description: "部分區段可獨立檢視，其餘仍不完整。",
    tone: "attention",
  },
  needs_human_input: {
    label: "待人工核准／選擇",
    description: "需要人工核准或明確選擇後才能使用。",
    tone: "attention",
  },
  insufficient_data: {
    label: "資料不足",
    description: "目前資料不足，後端未產生計算結果。",
    tone: "neutral",
  },
  quality_warning: {
    label: "資料品質警示",
    description: "資料存在，但品質不符合完整驗證條件。",
    tone: "attention",
  },
  pending: {
    label: "觀察中",
    description: "觀察期間尚未結束，不視為未達成。",
    tone: "neutral",
  },
  unsupported: {
    label: "目前未支援",
    description: "此能力目前沒有受支援的模型契約。",
    tone: "neutral",
  },
  not_applicable: {
    label: "此情境不適用",
    description: "此資料狀態不適用於目前情境。",
    tone: "neutral",
  },
  unknown: {
    label: "Unknown analysis state",
    description: "後端回傳未知狀態；介面已採 fail-closed 顯示。",
    tone: "attention",
  },
};
