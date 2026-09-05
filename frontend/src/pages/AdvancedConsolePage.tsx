import { Link } from "react-router-dom";
import {
  Network,
  CandlestickChart,
  CalendarDays,
  GitCompare,
  CheckSquare,
  ListChecks,
  Calendar,
  Settings,
  ShieldAlert,
} from "lucide-react";

interface ConsoleLinkProps {
  to: string;
  title: string;
  description: string;
  icon: React.ElementType;
}

function ConsoleCard({ to, title, description, icon: Icon }: ConsoleLinkProps) {
  return (
    <Link
      to={to}
      className="card"
      style={{
        display: "block",
        padding: "1.25rem",
        textDecoration: "none",
        color: "inherit",
        transition: "transform 0.15s ease, box-shadow 0.15s ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-2px)";
        e.currentTarget.style.boxShadow = "0 6px 16px -2px rgba(0, 0, 0, 0.08)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "none";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.5rem" }}>
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 8,
            background: "var(--color-bg-subtle, #f1f5f9)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--color-primary, #0284c7)",
          }}
        >
          <Icon size={20} />
        </div>
        <h3 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 700 }}>{title}</h3>
      </div>
      <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--color-muted, #64748b)", lineHeight: 1.5 }}>
        {description}
      </p>
    </Link>
  );
}

export function AdvancedConsolePage() {
  return (
    <div className="advanced-console-page" style={{ maxWidth: 960, margin: "0 auto", padding: "1.5rem 1rem" }}>
      <div style={{ marginBottom: "2rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.5rem" }}>
          <Settings size={24} color="var(--color-primary, #0284c7)" />
          <h1 style={{ fontSize: "1.75rem", fontWeight: 800, margin: 0 }}>
            進階管理與審計控制台
          </h1>
        </div>
        <p style={{ color: "var(--color-muted, #64748b)", fontSize: "0.95rem", margin: 0 }}>
          供系統管理員與研究人員檢視底層材料快照、執行主檔治理、並追蹤歷史回測與驗證歷程。
        </p>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: "1.25rem",
        }}
      >
        <ConsoleCard
          to="/eod-close"
          title="官方日收盤價材料化"
          description="檢視 TWSE／TPEx 日收盤官方快照與觀測值入庫狀態。"
          icon={CandlestickChart}
        />

        <ConsoleCard
          to="/universe"
          title="標的主檔治理"
          description="檢視全市場股票主檔材料化狀態、簡稱覆蓋率與生命週期。"
          icon={Network}
        />

        <ConsoleCard
          to="/snapshots"
          title="歷史分析快照"
          description="瀏覽不可變的研究分析快照歷史紀錄與原始依賴雜湊。"
          icon={CalendarDays}
        />

        <ConsoleCard
          to="/snapshots/compare"
          title="快照差異比對"
          description="比對同一標的在不同時間切點或模型版本之推算差異。"
          icon={GitCompare}
        />

        <ConsoleCard
          to="/validation"
          title="歷史觀察與驗證"
          description="檢視歷史驗證運行紀錄、樣本外統計與數據品質 Gate。"
          icon={CheckSquare}
        />

        <ConsoleCard
          to="/research"
          title="研究待辦隊列"
          description="管理個股研究關注清單與人工決策審查待辦事項。"
          icon={ListChecks}
        />

        <ConsoleCard
          to="/research/daily"
          title="每日研究排程"
          description="檢視每日排程材料化歷程與當日結算檢核狀態。"
          icon={Calendar}
        />
      </div>

      <div
        style={{
          marginTop: "2.5rem",
          padding: "1rem 1.25rem",
          background: "var(--color-bg-subtle, #f8fafc)",
          border: "1px solid var(--color-border, #e2e8f0)",
          borderRadius: 8,
          display: "flex",
          alignItems: "center",
          gap: "0.75rem",
        }}
      >
        <ShieldAlert size={20} color="var(--color-muted, #64748b)" />
        <span style={{ fontSize: "0.85rem", color: "var(--color-muted, #64748b)" }}>
          本區所有操作均維持在本地沙盒環境，未經授權禁止向外部發送真實委託或變更安全合約。
        </span>
      </div>
    </div>
  );
}
