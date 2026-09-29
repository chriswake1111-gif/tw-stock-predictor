import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { guidanceRead } from '../api/guidanceClient';
import { researchErrorMessage, researchMutation } from '../api/researchClient';
import './MyStocks.css';

type Stock = { enabled?: boolean; symbol: string; name: string; held: boolean; favorite: boolean; version: string; last_saved_at: string | null; saved_cutoff_at: string | null };
type Page = { enabled: boolean; items: Stock[]; next_cursor: string | null };
const endpoint = '/api/v2/research/library';
const date = (value: string) => new Date(value).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });

export function StockLabels({ symbol }: { symbol: string }) {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['my-stocks', symbol], queryFn: async ({ signal }) => {
    const data = await guidanceRead<Stock>(`${endpoint}/${encodeURIComponent(symbol)}`, signal);
    if (data.enabled === false) return data;
    if (data.enabled !== true || data.symbol !== symbol || typeof data.held !== 'boolean' || typeof data.favorite !== 'boolean' || !/^[a-f0-9]{64}$/.test(data.version)) throw new Error('invalid_library_response');
    return data;
  }, retry: false });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [hasPending, setHasPending] = useState(false);
  const pending = useRef<{ key: string; body: { label: 'held' | 'favorite'; value: boolean; version: string } } | null>(null);
  async function write(label?: 'held' | 'favorite') {
    if (!query.data) return;
    if (!pending.current && label) pending.current = { key: crypto.randomUUID(), body: { label, value: !query.data[label], version: query.data.version } };
    if (!pending.current) return;
    setHasPending(true);
    setBusy(true); setMessage('');
    try {
      await researchMutation(`${endpoint}/${encodeURIComponent(symbol)}`, pending.current.body, pending.current.key);
      pending.current = null;
      setHasPending(false);
      await cache.invalidateQueries({ queryKey: ['my-stocks'] });
      setMessage('標記操作已完成。');
    } catch (error) {
      setMessage(researchErrorMessage(error, '標記操作未確認，請重試相同操作。'));
      if (error instanceof Error && ['research_library_state_conflict', 'research_library_unknown_symbol', 'research_library_storage_full', 'research_library_disabled'].includes(error.message)) {
        pending.current = null;
        setHasPending(false);
        await cache.invalidateQueries({ queryKey: ['my-stocks'] });
      }
    } finally { setBusy(false); }
  }
  if (query.data?.enabled === false) return null;
  return <section className="stock-labels" aria-label="我的股票標記">
    {query.data && <div className="stock-library-actions">
      <button aria-pressed={query.data.held} disabled={busy || query.isError || hasPending} onClick={() => void write('held')}>{query.data.held ? '已標記持有' : '標記持有'}</button>
      <button aria-pressed={query.data.favorite} disabled={busy || query.isError || hasPending} onClick={() => void write('favorite')}>{query.data.favorite ? '已收藏' : '收藏股票'}</button>
      <Link to="/">回到我的股票</Link>
    </div>}
    <p className="stock-library-muted">持有與收藏各自保存；取消標記不會刪除研究。持有標記不記錄數量、成本或損益。</p>
    {query.isPending && <p role="status">正在讀取股票標記…</p>}
    {query.isError && <p role="alert">股票標記讀取失敗，無法確認最新狀態。<button onClick={() => void query.refetch()}>重新讀取標記</button></p>}
    {message && <p role="status">{message}</p>}
    {hasPending && !busy && <button onClick={() => void write()}>重試相同操作</button>}
  </section>;
}

export function MyStocks() {
  const [category, setCategory] = useState('all');
  const [pages, setPages] = useState(['']);
  const after = pages[pages.length - 1] || '';
  const query = useQuery({ queryKey: ['my-stocks', 'list', category, after], retry: false,
    queryFn: async ({ signal }) => {
      const data = await guidanceRead<Page>(`${endpoint}?category=${category}&after=${encodeURIComponent(after)}&limit=8`, signal);
      if (data.enabled === false) return data;
      if (data.enabled !== true || !Array.isArray(data.items)) throw new Error('invalid_library_response');
      return data;
    } });
  if (query.data?.enabled === false) return null;
  return <section className="stock-library" aria-label="我的股票">
    <div className="stock-library-actions"><h2>我的股票</h2><a href="#stock-search">搜尋其他股票</a></div><p>持有與收藏保存在這台電腦，可隨時開啟研究。</p>
    <div className="stock-library-actions" aria-label="股票清單分類">{([['all', '全部'], ['held', '持有'], ['favorites', '收藏'], ['researched', '已保存研究']] as const).map(([id, label]) => <button key={id} aria-pressed={category === id} onClick={() => { setCategory(id); setPages(['']); }}>{label}</button>)}</div>
    {query.isPending && <p role="status">正在讀取本機清單…</p>}
    {query.isError && <p role="alert">清單暫時無法讀取，既有紀錄仍保留。<button onClick={() => void query.refetch()}>重新讀取清單</button></p>}
    {query.data && !query.data.items.length && <p>此分類尚無股票。可在下方搜尋，進入個股頁標記持有或收藏；確認保存的研究也會出現在這裡。</p>}
    <ul className="stock-library-list">{query.data?.items.map(stock => <li key={stock.symbol}>
      <Link to={`/stocks/${encodeURIComponent(stock.symbol)}`}><strong>{stock.name}</strong><span>{stock.symbol} · 開啟研究</span></Link>
      <p>{[stock.held && '持有', stock.favorite && '收藏', stock.last_saved_at && '已保存研究'].filter(Boolean).join(' · ')}</p>
      {stock.last_saved_at ? <p className="stock-library-muted">上次保存：{date(stock.last_saved_at)}<br />研究資訊截止：{stock.saved_cutoff_at ? date(stock.saved_cutoff_at) : '未提供'}；請開啟研究查看目前資料。</p> : <p className="stock-library-muted">尚無保存研究</p>}
    </li>)}</ul>
    <div className="stock-library-actions">{pages.length > 1 && <button onClick={() => setPages(p => p.slice(0, -1))}>上一頁股票</button>}{query.data?.next_cursor && <button onClick={() => setPages(p => [...p, query.data!.next_cursor!])}>下一頁股票</button>}</div>
  </section>;
}
