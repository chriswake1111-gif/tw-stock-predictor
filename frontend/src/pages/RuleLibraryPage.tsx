import { useQuery } from "@tanstack/react-query";
import { BookOpen, ShieldAlert } from "lucide-react";
import { evidenceApi } from "../api/client";
import { EvidenceLevelBadge } from "../components/evidence/EvidencePrimitives";
import { SectionState } from "../components/evidence/SectionState";

export function RuleLibraryPage() {
  const query = useQuery({ queryKey: ["model-rules"], queryFn: ({ signal }) => evidenceApi.rules(signal) });
  if (query.isLoading) return <div className="page loading-state">讀取規則註冊表…</div>;
  if (query.isError || !query.data) return <div className="page"><SectionState status="insufficient_data" reason="rule_registry_unavailable" /></div>;
  return <div className="page"><header className="workspace-heading"><div><span className="eyebrow">Model governance</span><h1>模型說明</h1><p className="muted">核心資格以後端 Rule Registry 為準，不因舊程式存在而升格。</p></div><div className="model-disclosure"><BookOpen aria-hidden="true" /><span>Model {query.data.model_version}</span><small>official_affiliation: false</small></div></header><div className="rule-library">{query.data.rules.map((rule) => <article key={rule.rule_id}><header><div><span className="eyebrow">{rule.implementation_mode}</span><h2>{rule.rule_id} · {rule.title}</h2></div><EvidenceLevelBadge level={rule.evidence_level} /></header><dl className="compact-dl"><div><dt>版本</dt><dd>{rule.version}</dd></div><div><dt>人工核准</dt><dd>{rule.human_approval_required ? "需要" : "不需要"}</dd></div><div><dt>允許輸出</dt><dd>{rule.allowed_outputs?.join("、") || "未列示"}</dd></div></dl>{rule.forbidden_uses?.length ? <div className="forbidden-box"><ShieldAlert aria-hidden="true" /><div><strong>禁止用途</strong><p>{rule.forbidden_uses.join("、")}</p></div></div> : null}</article>)}</div></div>;
}
