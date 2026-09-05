import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Search, ArrowRight, TrendingUp, Sparkles, Building2 } from "lucide-react";
import { searchUniverse, getUniverseCoverage } from "../api/phase20Client";
import type { UniverseCoverage, UniverseSearchResultItem } from "../api/types";
import { FirstRunPrepCard } from "../components/FirstRunPrepCard";
import { ShortNameUpgradeBanner } from "../components/ShortNameUpgradeBanner";

const QUICK_STOCKS = [
  { code: "2330.TW", name: "台積電", desc: "半導體權值龍頭" },
  { code: "2454.TW", name: "聯發科", desc: "IC設計龍頭" },
  { code: "2317.TW", name: "鴻海", desc: "電子代工龍頭" },
  { code: "2603.TW", name: "長榮", desc: "航運主流標的" },
];

export function SearchHomePage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UniverseSearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [coverage, setCoverage] = useState<UniverseCoverage | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load coverage on mount to determine first run / upgrade banner
  useEffect(() => {
    let active = true;
    getUniverseCoverage()
      .then((cov) => {
        if (active) setCoverage(cov);
      })
      .catch(() => {
        // ignore if offline or starting
      });
    return () => {
      active = false;
    };
  }, []);

  function refreshCoverage() {
    getUniverseCoverage().then(setCoverage).catch(() => {});
  }

  // Live search debounced
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    const timeoutId = setTimeout(() => {
      searchUniverse(trimmed, 10)
        .then((res) => {
          setResults(res.results);
          if (res.coverage) {
            setCoverage(res.coverage);
          }
          setLoading(false);
        })
        .catch(() => {
          setResults([]);
          setLoading(false);
        });
    }, 150);

    return () => clearTimeout(timeoutId);
  }, [query]);

  function handleSelect(canonicalSymbol: string) {
    navigate(`/stocks/${encodeURIComponent(canonicalSymbol)}`);
  }

  return (
    <div className="search-home-page" style={{ maxWidth: 840, margin: "0 auto", padding: "2rem 1rem" }}>
      {coverage && coverage.universe_status === "not_initialized" ? (
        <FirstRunPrepCard onPreparationComplete={refreshCoverage} />
      ) : (
        <>
          {coverage && (
            <ShortNameUpgradeBanner coverage={coverage} onUpgradeComplete={refreshCoverage} />
          )}

          <div style={{ textAlign: "center", marginTop: "2rem", marginBottom: "2.5rem" }}>
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.4rem",
                padding: "0.25rem 0.75rem",
                borderRadius: 9999,
                background: "var(--color-primary-subtle, #e0f2fe)",
                color: "var(--color-primary, #0369a1)",
                fontSize: "0.85rem",
                fontWeight: 600,
                marginBottom: "1rem",
              }}
            >
              <Sparkles size={14} />
              <span>本地優先 杜金龍理論研究工作區</span>
            </div>
            <h1 style={{ fontSize: "2.2rem", fontWeight: 800, margin: 0, letterSpacing: "-0.02em" }}>
              搜尋標的以開啟研究
            </h1>
            <p style={{ color: "var(--color-muted, #64748b)", marginTop: "0.75rem", fontSize: "1.05rem" }}>
              支援股票代號（例如 <code>2330</code>）與中文簡稱（例如 <code>台積電</code>）即時本地檢索
            </p>
          </div>

          <div style={{ position: "relative", marginBottom: "2rem" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                background: "var(--color-surface, #ffffff)",
                border: "2px solid var(--color-primary, #0284c7)",
                borderRadius: 12,
                padding: "0.75rem 1.25rem",
                boxShadow: "0 4px 20px -2px rgba(0, 0, 0, 0.08)",
              }}
            >
              <Search size={24} color="var(--color-primary, #0284c7)" style={{ marginRight: "0.75rem" }} />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="請輸入股票代號（如 2330）或中文簡稱（如 台積電）..."
                autoFocus
                style={{
                  width: "100%",
                  border: "none",
                  outline: "none",
                  fontSize: "1.15rem",
                  background: "transparent",
                  color: "var(--color-foreground, #1e293b)",
                }}
              />
              {loading && (
                <div style={{ color: "var(--color-muted, #94a3b8)", fontSize: "0.85rem", whiteSpace: "nowrap" }}>
                  搜尋中...
                </div>
              )}
            </div>

            {/* Results Dropdown */}
            {results.length > 0 && (
              <div
                className="card"
                style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  right: 0,
                  zIndex: 20,
                  marginTop: "0.5rem",
                  maxHeight: 400,
                  overflowY: "auto",
                  padding: "0.5rem 0",
                  boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.15)",
                }}
              >
                {results.map((item) => (
                  <div
                    key={item.canonical_symbol}
                    onClick={() => handleSelect(item.canonical_symbol)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "0.75rem 1.25rem",
                      cursor: "pointer",
                      borderBottom: "1px solid var(--color-border, #f1f5f9)",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-bg-subtle, #f8fafc)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
                      <span
                        style={{
                          display: "inline-block",
                          fontWeight: 700,
                          fontSize: "1.1rem",
                          minWidth: 60,
                          color: "var(--color-primary, #0284c7)",
                        }}
                      >
                        {item.official_code}
                      </span>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: "1rem" }}>
                          {item.short_name || item.display_name}
                        </div>
                        {item.short_name && item.display_name !== item.short_name && (
                          <div style={{ fontSize: "0.82rem", color: "var(--color-muted, #64748b)" }}>
                            {item.display_name}
                          </div>
                        )}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span
                        style={{
                          fontSize: "0.78rem",
                          padding: "0.2rem 0.5rem",
                          borderRadius: 4,
                          background: "var(--color-bg-subtle, #f1f5f9)",
                          color: "var(--color-muted, #475569)",
                        }}
                      >
                        {item.venue}
                      </span>
                      <ArrowRight size={16} color="var(--color-muted, #94a3b8)" />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {query.trim() && !loading && results.length === 0 && (
              <div
                className="card"
                style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  right: 0,
                  zIndex: 20,
                  marginTop: "0.5rem",
                  padding: "1.5rem",
                  textAlign: "center",
                  color: "var(--color-muted, #64748b)",
                }}
              >
                查無符合「{query}」的上市櫃標的。請檢查輸入代號或名稱。
              </div>
            )}
          </div>

          {/* Quick Launch Section */}
          <div style={{ marginTop: "3rem" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                color: "var(--color-muted, #64748b)",
                fontSize: "0.9rem",
                fontWeight: 600,
                marginBottom: "1rem",
              }}
            >
              <TrendingUp size={16} />
              <span>快速開啟常備研究標的</span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                gap: "1rem",
              }}
            >
              {QUICK_STOCKS.map((st) => (
                <div
                  key={st.code}
                  onClick={() => handleSelect(st.code)}
                  className="card"
                  style={{
                    padding: "1rem",
                    cursor: "pointer",
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
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontWeight: 700, fontSize: "1.1rem", color: "var(--color-primary, #0284c7)" }}>
                      {st.code.split(".")[0]}
                    </span>
                    <Building2 size={16} color="var(--color-muted, #94a3b8)" />
                  </div>
                  <div style={{ fontWeight: 600, marginTop: "0.3rem" }}>{st.name}</div>
                  <div style={{ fontSize: "0.8rem", color: "var(--color-muted, #64748b)", marginTop: "0.2rem" }}>
                    {st.desc}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
