import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export function EvidenceDrawer({ title, open, onClose, children }: { title: string; open: boolean; onClose: () => void; children: ReactNode }) {
  const drawerRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const drawer = drawerRef.current;
    drawer?.querySelector<HTMLElement>("button")?.focus();
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab" || !drawer) return;
      const focusable = Array.from(drawer.querySelectorAll<HTMLElement>('button, a, input, select, [tabindex]:not([tabindex="-1"])'));
      if (!focusable.length) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", handleKey);
    return () => { document.removeEventListener("keydown", handleKey); previous?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside ref={drawerRef} className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><span className="eyebrow">Evidence trace</span><h2 id="evidence-drawer-title">{title}</h2></div>
          <button className="icon-button" type="button" aria-label="關閉證據面板" onClick={onClose}><X aria-hidden="true" /></button>
        </header>
        <div className="drawer-content">{children}</div>
      </aside>
    </div>
  );
}
