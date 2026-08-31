import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { StockWorkspacePage } from "./pages/StockWorkspacePage";
import { MarketOverviewPage } from "./pages/MarketOverviewPage";
import { RuleLibraryPage } from "./pages/RuleLibraryPage";
import { SnapshotDetailPage, SnapshotHistoryPage } from "./pages/SnapshotPages";
import { SnapshotComparisonPage } from "./pages/SnapshotComparisonPage";
import { ValidationHistoryPage, ValidationRunPage } from "./pages/ValidationPages";
import { ResearchQueuePage } from "./pages/ResearchQueuePage";
import { DailyResearchPage } from "./pages/DailyResearchPage";
import { UniversePage } from "./pages/UniversePage";
import { EodCloseContextPage } from "./pages/EodCloseContextPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/stocks/2330.TW" replace />} />
        <Route path="/market" element={<MarketOverviewPage />} />
        <Route path="/stocks/:symbol" element={<StockWorkspacePage />} />
        <Route path="/snapshots" element={<SnapshotHistoryPage />} />
        <Route path="/snapshots/compare" element={<SnapshotComparisonPage />} />
        <Route path="/snapshots/:snapshotId" element={<SnapshotDetailPage />} />
        <Route path="/validation" element={<ValidationHistoryPage />} />
        <Route path="/validation/runs/:runId" element={<ValidationRunPage />} />
        <Route path="/rules" element={<RuleLibraryPage />} />
        <Route path="/research" element={<ResearchQueuePage />} />
        <Route path="/research/daily" element={<DailyResearchPage />} />
        <Route path="/universe" element={<UniversePage />} />
        <Route path="/eod-close" element={<EodCloseContextPage />} />
        <Route path="*" element={<PlaceholderPage title="找不到頁面" description="請使用主要導覽返回研究工作區。" />} />
      </Routes>
    </AppShell>
  );
}
