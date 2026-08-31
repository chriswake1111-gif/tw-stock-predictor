import { useQuery } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { evidenceApi } from "../api/client";
import { researchWorkflowApi } from "../api/researchClient";
import type {
  DailyResearchItem,
  DailyResearchResponse,
  DailyResearchSnapshotReference,
  DailyResearchStatus,
} from "../api/types";

const statusLabels: Record<DailyResearchStatus, string> = {
  available: "資料可用",
  partial: "部分可用",
  insufficient_data: "資料不足",
  unknown: "狀態未知",
  blocked: "已阻擋",
};

const reviewLabels: Record<string, string> = {
  no_snapshot: "尚無可用分析快照",
  baseline_not_set: "尚未設定每日複核基準",
  comparable_with_deltas: "相對複核基準有已保存差異",
  comparable_without_deltas: "相對複核基準目前無已保存差異",
  incomparable_contract: "比較契約不相容",
  blocked: "依賴資料已阻擋複核",
  unknown: "複核狀態未知",
  snapshot_integrity_error: "快照完整性需要處理",
};

function statusLabel(status: string) {
  return statusLabels[status as DailyResearchStatus] ?? status;
}

function reviewLabel(state: string) {
  return reviewLabels[state] ?? state;
}

function valueLabel(value: unknown, empty = "無") {
  if (value === null || value === undefined || value === "") return empty;
  return String(value);
}

function SnapshotFact({
  label,
  reference,
}: {
  label: string;
  reference: DailyResearchSnapshotReference | null;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {reference ? (
          <>
            <code>{reference.snapshot_id}</code>
            <small>{reference.created_at ?? "建立時間未知"}</small>
            <small>{reference.eligible_for_requested_d_k ? "符合本次 D/K" : "不符合本次 D/K"}</small>
          </>
        ) : "尚無"}
      </dd>
    </div>
  );
}

export function DailyResearchPage() {
  const [marketDateInput, setMarketDateInput] = useState("");
  const [knowledgeCutoffInput, setKnowledgeCutoffInput] = useState("");
  const [request, setRequest] = useState<{ marketDate: string; knowledgeCutoffAt: string } | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [symbol, setSymbol] = useState("");
  const [message, setMessage] = useState("");
  const [detail, setDetail] = useState<DailyResearchResponse | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const baselineKeys = useRef<Record<string, string>>({});
  const refreshKeys = useRef<Record<string, string>>({});

  const queue = useQuery({
    queryKey: ["daily-research-context", request?.marketDate, request?.knowledgeCutoffAt, cursor],
    queryFn: ({ signal }) => evidenceApi.dailyResearchContext(
      request!.marketDate,
      request!.knowledgeCutoffAt,
      25,
      cursor,
      signal,
    ),
    enabled: request !== null,
  });

  async function act(
    actionKey: string,
    operation: () => Promise<unknown>,
    success: string,
  ) {
    setPendingAction(actionKey);
    setMessage("");
    try {
      await operation();
      setMessage(success);
      setDetail(null);
      await queue.refetch();
      return true;
    } catch (error) {
      const detailMessage = error instanceof Error ? error.message : "";
      setMessage(detailMessage === "csrf_refresh_required"
        ? "安全工作階段已更新，請確認內容後再次提交。"
        : detailMessage === "csrf_refresh_failed"
          ? "安全工作階段更新失敗，資料未變更。請檢查本機服務後重試。"
          : "操作未完成，資料未變更。請檢查本機服務設定。",
      );
      return false;
    } finally {
      setPendingAction(null);
    }
  }

  function applyRequest(event: FormEvent) {
    event.preventDefault();
    const marketDate = marketDateInput.trim();
    const knowledgeCutoffAt = knowledgeCutoffInput.trim();
    if (!marketDate || !knowledgeCutoffAt) {
      setMessage("請同時輸入市場日期 D 與含時區的知識截止 K。未自動補入最新值。");
      return;
    }
    setMessage("");
    setDetail(null);
    setCursor(null);
    setRequest({ marketDate, knowledgeCutoffAt });
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const candidate = symbol.trim();
    if (!candidate) return;
    if (await act("add", () => researchWorkflowApi.addSymbol(candidate), "已更新研究觀察清單。")) {
      setSymbol("");
    }
  }

  async function selectBaseline(item: DailyResearchItem) {
    if (!request || !item.latest_snapshot_reference || !item.baseline_selection_eligible) return;
    const itemId = item.watchlist_reference.watchlist_item_id;
    const snapshotId = item.latest_snapshot_reference.snapshot_id;
    const key = baselineKeys.current[itemId] ?? crypto.randomUUID();
    baselineKeys.current[itemId] = key;
    if (await act(
      `baseline:${itemId}`,
      () => researchWorkflowApi.selectDailyBaseline(itemId, snapshotId, request.knowledgeCutoffAt, key),
      "已將此快照設為每日複核基準。",
    )) delete baselineKeys.current[itemId];
  }

  async function refreshSnapshot(item: DailyResearchItem) {
    if (!request || !item.permitted_actions.refresh_snapshot) return;
    const itemId = item.watchlist_reference.watchlist_item_id;
    const key = refreshKeys.current[itemId] ?? crypto.randomUUID();
    refreshKeys.current[itemId] = key;
    if (await act(
      `refresh:${itemId}`,
      () => researchWorkflowApi.refreshDailySnapshot(itemId, {
        market_date: request.marketDate,
        loaded_knowledge_cutoff_at: request.knowledgeCutoffAt,
        expected_snapshot_id: item.latest_snapshot_reference?.snapshot_id ?? null,
        advance_knowledge_cutoff: true,
      }, key),
      "已完成明確要求的快照更新。",
    )) delete refreshKeys.current[itemId];
  }

  async function loadDetail(itemId: string) {
    if (!request) return;
    setPendingAction(`detail:${itemId}`);
    try {
      setDetail(await evidenceApi.dailyResearchContextDetail(
        itemId,
        request.marketDate,
        request.knowledgeCutoffAt,
      ));
    } catch {
      setMessage("目前無法安全載入此標的的每日比較內容。");
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div className="page daily-research-page">
      <header className="workspace-heading">
        <div>
          <span className="eyebrow">Phase 17 · Daily research review</span>
          <h1>每日研究脈絡</h1>
          <p className="muted">
            以明確的市場日期 D 與知識截止 K，查看研究觀察清單、資料狀態、複核差異與可追溯來源；不輸出排名、推薦或交易訊號。
          </p>
        </div>
      </header>

      <form className="research-controls daily-context-form" onSubmit={applyRequest}>
        <label>
          市場日期 D（YYYY-MM-DD）
          <input
            value={marketDateInput}
            onChange={(event) => setMarketDateInput(event.target.value)}
            placeholder="2026-08-31"
            required
          />
        </label>
        <label>
          知識截止 K（含時區）
          <input
            value={knowledgeCutoffInput}
            onChange={(event) => setKnowledgeCutoffInput(event.target.value)}
            placeholder="2026-08-31T08:00:00+08:00"
            required
          />
        </label>
        <button type="submit">載入每日脈絡</button>
      </form>
      <p className="daily-context-note">D/K 必須由使用者明確提供；系統不會用最新時間、回退值或隱性 fallback 代替。</p>

      <form className="research-add" onSubmit={(event) => void add(event)}>
        <label>
          加入研究標的
          <input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="2330 或 2330.TW" />
        </label>
        <button type="submit" disabled={pendingAction === "add"}>加入清單</button>
      </form>

      <p className="research-live" role="status" aria-live="polite">{message}</p>
      {queue.isLoading ? <p role="status">正在載入每日研究脈絡…</p> : null}
      {queue.isError ? <p role="alert">目前無法讀取每日研究脈絡，未將此狀態視為空清單。</p> : null}

      {queue.data ? (
        <section className="evidence-card daily-preflight" aria-live="polite">
          <header>
            <div>
              <h2>每日前置狀態</h2>
              <p className="muted">D {queue.data.request.market_date} · K {queue.data.request.knowledge_cutoff_at}</p>
            </div>
            <span className={`freshness-badge daily-status--${queue.data.preflight.status}`}>
              {statusLabel(queue.data.preflight.status)}
            </span>
          </header>
          <dl className="daily-summary-grid">
            <div><dt>頁面狀態</dt><dd>{statusLabel(queue.data.status)}</dd></div>
            <div><dt>活動清單總數</dt><dd>{queue.data.preflight.active_queue_total_count}</dd></div>
            <div><dt>本頁筆數</dt><dd>{queue.data.preflight.page_item_count}</dd></div>
            <div><dt>需複核筆數</dt><dd>{queue.data.preflight.page_review_needed_count}</dd></div>
            <div><dt>阻擋筆數</dt><dd>{queue.data.preflight.page_review_blocked_count}</dd></div>
            <div><dt>活動人口校驗摘要</dt><dd><code>{queue.data.preflight.active_population_checksum}</code></dd></div>
          </dl>
          {queue.data.preflight.reasons.length > 0 ? (
            <p className="daily-reasons">前置原因：{queue.data.preflight.reasons.join("、")}</p>
          ) : null}
          <p className="daily-context-note">完整 Phase 16 aggregate 仍標示為未證明完整；頁面狀態只代表本頁項目範圍。</p>
        </section>
      ) : null}

      {queue.data?.items.length === 0 ? <p className="comparison-empty">指定 D/K 下的活動研究清單目前是空的。</p> : null}
      <div className="research-list daily-research-list">
        {queue.data?.items.map((item) => {
          const itemId = item.watchlist_reference.watchlist_item_id;
          const snapshot = item.latest_snapshot_reference;
          const isBaselinePending = pendingAction === `baseline:${itemId}`;
          const isRefreshPending = pendingAction === `refresh:${itemId}`;
          return (
            <article key={itemId}>
              <header>
                <div>
                  <h2>{item.watchlist_reference.symbol}</h2>
                  <p className="muted"><code>{item.canonical_symbol}</code> · {item.venue} · {itemId}</p>
                </div>
                <span className={`freshness-badge daily-status--${item.status}`}>{statusLabel(item.status)}</span>
              </header>
              <p className="research-state">{reviewLabel(item.review_state)}</p>
              <div className="daily-flags" aria-label="複核狀態">
                <span>需複核：{item.review_needed ? "是" : "否"}</span>
                <span>已阻擋：{item.review_blocked ? "是" : "否"}</span>
                <span>受限：{item.review_limited ? "是" : "否"}</span>
                <span>新鮮度：{valueLabel(item.freshness_status)}</span>
              </div>
              <dl className="daily-item-grid">
                <SnapshotFact label="D/K 最新快照" reference={snapshot} />
                <SnapshotFact label="工作流最新快照" reference={item.workflow_latest_snapshot_reference} />
                <div><dt>保存差異</dt><dd>{item.stored_delta_summary.count}</dd></div>
                <div><dt>目前脈絡差異</dt><dd>{item.current_context_delta_summary.count}</dd></div>
                <div><dt>Phase 16 狀態</dt><dd>{valueLabel(item.phase16_context.aggregate_status, "未知")}</dd></div>
                <div><dt>來源狀態</dt><dd>{valueLabel(item.provenance.status)}</dd></div>
              </dl>
              {item.reason_codes.length > 0 ? (
                <p className="daily-reasons">原因代碼：{item.reason_codes.join("、")}</p>
              ) : null}
              <div className="research-actions">
                {item.permitted_actions.acknowledge && snapshot && item.baseline_selection_eligible ? (
                  <button type="button" disabled={isBaselinePending} onClick={() => void selectBaseline(item)}>
                    {isBaselinePending ? "設定中…" : "設為每日複核基準"}
                  </button>
                ) : null}
                {item.permitted_actions.refresh_snapshot ? (
                  <button type="button" disabled={isRefreshPending} onClick={() => void refreshSnapshot(item)}>
                    {isRefreshPending ? "更新中…" : "明確更新快照"}
                  </button>
                ) : null}
                <button
                  type="button"
                  disabled={pendingAction === `detail:${itemId}`}
                  onClick={() => void loadDetail(itemId)}
                >
                  {pendingAction === `detail:${itemId}` ? "載入中…" : "查看每日比較"}
                </button>
                {item.permitted_actions.archive ? (
                  <button type="button" onClick={() => void act(itemId, () => researchWorkflowApi.archiveItem(itemId), "已封存研究標的。")}>封存</button>
                ) : null}
                {item.permitted_actions.restore ? (
                  <button type="button" onClick={() => void act(itemId, () => researchWorkflowApi.unarchiveItem(itemId), "已恢復研究標的。")}>恢復</button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>

      {queue.data?.next_cursor ? (
        <div className="daily-pagination">
          <button type="button" onClick={() => setCursor(queue.data?.next_cursor ?? null)}>載入下一頁</button>
        </div>
      ) : null}

      {detail?.item ? (
        <section className="evidence-card research-detail" aria-live="polite">
          <header>
            <div><h2>每日比較內容</h2><p className="muted">{detail.item.canonical_symbol} · {detail.item.venue}</p></div>
            <button type="button" onClick={() => setDetail(null)}>關閉</button>
          </header>
          <p className="research-state">{reviewLabel(detail.item.review_state)}</p>
          <p className="daily-reasons">原因代碼：{detail.item.reason_codes.length ? detail.item.reason_codes.join("、") : "無"}</p>
          <pre>{JSON.stringify(detail.item.comparison ?? null, null, 2)}</pre>
        </section>
      ) : null}
    </div>
  );
}
