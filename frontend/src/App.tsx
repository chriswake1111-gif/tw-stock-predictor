import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { PlaceholderPage } from "./pages/PlaceholderPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/stocks/2330.TW" replace />} />
        <Route path="/market" element={<PlaceholderPage title="市場概況" description="市場流動性與資料品質將由唯讀 v2 契約呈現。" />} />
        <Route path="/stocks/:symbol" element={<PlaceholderPage title="個股研究" description="Evidence Workspace 正在載入後端權威分析。" />} />
        <Route path="/snapshots" element={<PlaceholderPage title="歷史快照" description="依標的、模式與時間檢視不可變快照。" />} />
        <Route path="/snapshots/:snapshotId" element={<PlaceholderPage title="快照詳情" description="只呈現已保存輸出，不在瀏覽器重新計算。" />} />
        <Route path="/validation" element={<PlaceholderPage title="歷史觀察" description="描述性呈現既有評估 Run，不產生排行。" />} />
        <Route path="/validation/runs/:runId" element={<PlaceholderPage title="歷史觀察詳情" description="前瞻保存快照與歷史重建分開呈現。" />} />
        <Route path="/rules" element={<PlaceholderPage title="模型說明" description="查閱 Rule ID、研究證據等級與禁止用途。" />} />
        <Route path="*" element={<PlaceholderPage title="找不到頁面" description="請使用主要導覽返回研究工作區。" />} />
      </Routes>
    </AppShell>
  );
}
