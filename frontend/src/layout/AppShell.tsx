import { useState, type FormEvent, type ReactNode } from "react";
import {
  BarChart3,
  BookOpen,
  CalendarDays,
  FileSearch,
  Menu,
  Search,
} from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";

const navigation = [
  { to: "/market", label: "市場概況", icon: BarChart3 },
  { to: "/stocks/2330.TW", label: "個股研究", icon: FileSearch },
  { to: "/snapshots", label: "歷史快照", icon: CalendarDays },
  { to: "/validation", label: "歷史觀察", icon: BarChart3 },
  { to: "/rules", label: "模型說明", icon: BookOpen },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [symbol, setSymbol] = useState("2330.TW");
  const [mobileOpen, setMobileOpen] = useState(false);

  function submit(event: FormEvent) {
    event.preventDefault();
    const candidate = symbol.trim().toUpperCase();
    if (candidate) {
      navigate(`/stocks/${encodeURIComponent(candidate)}`);
      setMobileOpen(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button
          className="icon-button menu-button"
          type="button"
          aria-label="切換主要導覽"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((open) => !open)}
        >
          <Menu aria-hidden="true" />
        </button>
        <div className="product-name">台股證據決策工作區</div>
        <form className="symbol-search" role="search" onSubmit={submit}>
          <Search aria-hidden="true" size={19} />
          <label className="sr-only" htmlFor="global-symbol-search">
            輸入台灣股票代號
          </label>
          <input
            id="global-symbol-search"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            placeholder="輸入股票代號"
            autoComplete="off"
          />
        </form>
        <p className="research-boundary">研究與決策支援｜非自動投資建議</p>
      </header>

      <aside className={`sidebar${mobileOpen ? " sidebar--open" : ""}`}>
        <nav aria-label="主要導覽">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `nav-link${isActive ? " nav-link--active" : ""}`
              }
            >
              <Icon aria-hidden="true" size={21} strokeWidth={1.7} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="workspace" id="main-content">
        {children}
      </main>

      <nav className="mobile-nav" aria-label="行動版主要導覽">
        {navigation.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `mobile-nav__link${isActive ? " mobile-nav__link--active" : ""}`
            }
          >
            <Icon aria-hidden="true" size={20} strokeWidth={1.8} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
