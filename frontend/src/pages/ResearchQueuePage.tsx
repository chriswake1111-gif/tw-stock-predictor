import { useQuery } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { evidenceApi } from "../api/client";
import { researchWorkflowApi } from "../api/researchClient";
import type { ResearchQueueDetail, ResearchQueueItem } from "../api/types";

const reviewLabels: Record<string, string> = {
  no_snapshot: "尚無分析快照",
  baseline_not_set: "尚未設定複核基準",
  comparable_with_deltas: "與上次複核快照相比有差異",
  comparable_without_deltas: "與上次複核快照相比無差異",
  incomparable_contract: "比較契約不相容",
  blocked: "依賴資料已阻擋",
  unknown: "資料狀態未知",
  snapshot_integrity_error: "目前無法安全比較",
};

export function ResearchQueuePage() {
  const [cutoffInput, setCutoffInput] = useState("");
  const [cutoff, setCutoff] = useState("");
  const [symbol, setSymbol] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [message, setMessage] = useState("");
  const [detail, setDetail] = useState<ResearchQueueDetail | null>(null);
  const acknowledgmentKeys = useRef<Record<string, string>>({});
  const queue = useQuery({
    queryKey: ["research-queue", cutoff, includeArchived],
    queryFn: ({ signal }) => evidenceApi.researchQueue(cutoff, includeArchived, signal),
    enabled: cutoff !== "",
  });

  async function act(action: () => Promise<unknown>, success: string) {
    setMessage("");
    try {
      await action();
      setMessage(success);
      await queue.refetch();
      return true;
    } catch (error) {
      setMessage(error instanceof Error && error.message === "csrf_refresh_required"
        ? "安全工作階段已更新，請確認內容後再次提交。"
        : "操作未完成，資料未變更。請檢查本機服務設定。");
      return false;
    }
  }

  function applyCutoff(event: FormEvent) {
    event.preventDefault();
    if (cutoffInput.trim()) setCutoff(cutoffInput.trim());
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!symbol.trim()) return;
    if (await act(() => researchWorkflowApi.addSymbol(symbol.trim()), "已更新研究觀察清單。")) {
      setSymbol("");
    }
  }

  async function acknowledge(item: ResearchQueueItem) {
    const snapshotId = item.latest_snapshot_reference?.snapshot_id;
    if (!snapshotId || !cutoff) return;
    const itemId = item.watchlist_item.watchlist_item_id;
    const key = acknowledgmentKeys.current[itemId] ?? crypto.randomUUID();
    acknowledgmentKeys.current[itemId] = key;
    if (await act(
      () => researchWorkflowApi.acknowledgeSnapshot(itemId, snapshotId, cutoff, key),
      "已將此快照設為明確複核基準。",
    )) delete acknowledgmentKeys.current[itemId];
  }

  async function loadDetail(itemId: string) {
    try {
      setDetail(await evidenceApi.researchQueueDetail(itemId, cutoff));
    } catch {
      setMessage("目前無法安全載入比較內容。");
    }
  }

  return (
    <div className="page research-queue-page">
      <header className="workspace-heading"><div>
        <span className="eyebrow">Research workflow</span><h1>研究觀察清單</h1>
        <p className="muted">以明確截止時間，比較最新快照與上次複核快照；不代表時間事件、推薦或交易訊號。</p>
      </div></header>
      <form className="research-controls" onSubmit={applyCutoff}>
        <label>比較截止時間（含時區）<input value={cutoffInput} onChange={(event) => setCutoffInput(event.target.value)} placeholder="2026-08-13T08:00:00+08:00" required /></label>
        <button type="submit">載入清單</button>
        <label className="research-checkbox"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} />顯示已封存</label>
      </form>
      <form className="research-add" onSubmit={(event) => void add(event)}>
        <label>加入研究標的<input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="2330 或 2330.TW" /></label>
        <button type="submit">加入清單</button>
      </form>
      <p className="research-live" role="status" aria-live="polite">{message}</p>
      {queue.isLoading ? <p role="status">正在載入研究清單…</p> : null}
      {queue.isError ? <p role="alert">目前無法讀取研究清單，未將此狀態視為空清單。</p> : null}
      {queue.data?.items.length === 0 ? <p className="comparison-empty">研究觀察清單目前是空的。</p> : null}
      <div className="research-list">
        {queue.data?.items.map((item) => {
          const id = item.watchlist_item.watchlist_item_id;
          return <article key={id}>
            <header><div><h2>{item.watchlist_item.symbol}</h2><code>{id}</code></div>
              <span className={`freshness-badge freshness-badge--${item.freshness_status}`}>{item.freshness_status}</span></header>
            <p className="research-state">{reviewLabels[item.review_state] ?? "目前無法安全比較"}</p>
            <dl><div><dt>保存差異</dt><dd>{item.stored_delta_count}</dd></div><div><dt>目前依賴差異</dt><dd>{item.current_context_delta_count}</dd></div>
              <div><dt>最新快照</dt><dd>{item.latest_snapshot_reference?.snapshot_id ?? "尚無"}</dd></div>
              <div><dt>上次複核</dt><dd>{item.latest_review_event_reference?.reviewed_at ?? "尚未設定"}</dd></div></dl>
            <div className="research-actions">
              {item.latest_snapshot_reference ? <button type="button" onClick={() => void acknowledge(item)}>設為已複核基準</button> : null}
              <button type="button" onClick={() => void loadDetail(id)}>查看比較內容</button>
              {item.watchlist_item.membership_state === "active"
                ? <button type="button" onClick={() => void act(() => researchWorkflowApi.archiveItem(id), "已封存研究標的。")}>封存</button>
                : <button type="button" onClick={() => void act(() => researchWorkflowApi.unarchiveItem(id), "已恢復研究標的。")}>恢復</button>}
            </div>
          </article>;
        })}
      </div>
      {detail ? <section className="evidence-card research-detail" aria-live="polite"><header><h2>比較內容</h2><button type="button" onClick={() => setDetail(null)}>關閉</button></header>
        <p>{reviewLabels[detail.review_state]}</p><pre>{JSON.stringify(detail.comparison, null, 2)}</pre></section> : null}
    </div>
  );
}
