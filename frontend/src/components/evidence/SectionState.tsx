import { DatabaseZap } from "lucide-react";
import { StatusBadge } from "./EvidencePrimitives";

export function SectionState({ status, reason }: { status: unknown; reason?: string | null }) {
  return <div className="section-state"><DatabaseZap aria-hidden="true" size={24} strokeWidth={1.6} /><StatusBadge status={status} reason={reason} />{reason ? <p>{reason}</p> : null}</div>;
}
