import {
  BadgeAlert,
  Ban,
  CircleCheck,
  CircleHelp,
  CircleSlash,
  Clock3,
  DatabaseZap,
  Layers3,
  ShieldAlert,
  UserRoundCheck,
} from "lucide-react";
import type {
  CaptureMode,
  EvaluationOrigin,
  EvidenceLevel,
} from "../../api/types";
import { normalizeStatus, statusPresentation } from "../../lib/status";

const statusIcons = {
  available: CircleCheck,
  partial: Layers3,
  needs_human_input: UserRoundCheck,
  insufficient_data: DatabaseZap,
  quality_warning: ShieldAlert,
  pending: Clock3,
  unsupported: Ban,
  not_applicable: CircleSlash,
  unknown: CircleHelp,
} as const;

export function StatusBadge({
  status,
  reason,
}: {
  status: unknown;
  reason?: string | null;
}) {
  const normalized = normalizeStatus(status);
  const presentation = statusPresentation[normalized];
  const Icon = statusIcons[normalized];
  return (
    <span
      className={`status-badge status-badge--${presentation.tone}`}
      title={reason ?? presentation.description}
      data-status={normalized}
    >
      <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
      <span>{presentation.label}</span>
    </span>
  );
}

const evidenceDescriptions: Record<EvidenceLevel, string> = {
  A: "有本人公開且可重現的明確規則",
  B: "有公開方向，但參數或門檻不完整",
  C: "專案為程式化建立的 operationalization",
  U: "證據不足、衝突或不可驗證；不得進入 verified core",
};

export function EvidenceLevelBadge({ level }: { level: EvidenceLevel }) {
  return (
    <span className={`evidence-level evidence-level--${level.toLowerCase()}`}>
      <span aria-hidden="true" className="evidence-level__marker">
        {level}
      </span>
      <span>
        <strong>研究證據等級 {level}</strong>
        <small>{evidenceDescriptions[level]}</small>
      </span>
    </span>
  );
}

export function MethodStrengthIndicator({
  strength,
  independentMethods,
}: {
  strength: string | null | undefined;
  independentMethods: number;
}) {
  return (
    <span className="method-strength">
      <Layers3 aria-hidden="true" size={18} strokeWidth={1.7} />
      <span>
        <strong>方法匯聚程度：{strength ?? "未形成"}</strong>
        <small>獨立方法數 {independentMethods}；不是未來價格達標機率</small>
      </span>
    </span>
  );
}

export function OriginBadge({
  origin,
}: {
  origin: CaptureMode | EvaluationOrigin;
}) {
  const reconstruction = origin === "historical_reconstruction";
  return (
    <span className={`origin-badge${reconstruction ? " origin-badge--reconstruction" : ""}`}>
      {reconstruction ? <BadgeAlert aria-hidden="true" size={16} /> : <Clock3 aria-hidden="true" size={16} />}
      {reconstruction ? "歷史重建" : "前瞻保存快照"}
    </span>
  );
}

export function AsOfTimestamp({ value }: { value: string }) {
  return <time dateTime={value}>知識截止時間 {value}</time>;
}

export function NumeratorDenominator({
  numerator,
  denominator,
}: {
  numerator: number;
  denominator: number;
}) {
  if (denominator === 0) {
    return <strong>無可評估樣本</strong>;
  }
  return (
    <strong>
      {numerator} / {denominator}
    </strong>
  );
}
