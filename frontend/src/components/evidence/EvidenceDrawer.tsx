import { X } from "lucide-react";
import type { ReactNode } from "react";

export function EvidenceDrawer({ title, open, onClose, children }: { title: string; open: boolean; onClose: () => void; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><span className="eyebrow">Evidence trace</span><h2 id="evidence-drawer-title">{title}</h2></div>
          <button className="icon-button" type="button" aria-label="關閉證據面板" onClick={onClose}><X aria-hidden="true" /></button>
        </header>
        <div className="drawer-content">{children}</div>
      </aside>
    </div>
  );
}
