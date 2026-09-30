import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { guidanceRead } from '../api/guidanceClient';
import { readHomeSummary, type HomeSummary } from '../api/homeSummaryClient';
import { MyStocks } from '../components/MyStocks';
import { StockHomeSummary } from '../components/StockHomeSummary';
import { renderWithProviders } from './render';

vi.mock('../api/guidanceClient', async original => ({ ...await original<typeof import('../api/guidanceClient')>(), guidanceRead: vi.fn() }));
const symbol = '2330.TW';
export const homeFixture = (stock = symbol): HomeSummary => ({ contract_version: 'research_home_summary_v1', enabled: true,
  symbol: stock, local_only: true, prepared_at: '2026-09-30T00:00:00Z', selected_year: 2026,
  baseline: { entry_id: 'one', created_at: '2026-09-01T00:00:00Z', knowledge_cutoff_at: '2026-09-01T00:00:00Z' },
  status: 'changed', headline: '官方收盤價同一資料日的數字有修訂。', dates: [{ field: 'official_close', label: '官方收盤價', previous: '2026-09-01', current: '2026-09-01' }],
  limitations: [{ id: 'prices:stale', text: '部分資料已過期', owner: 'program' }],
  next_step: { target: 'research-changes', label: '閱讀前後比較', owner: 'user' } });
beforeEach(() => vi.resetAllMocks());

it('keeps at most two active reads even during cancellation and drops queued old cards', async () => {
  const pending: (() => void)[] = [];
  vi.mocked(guidanceRead).mockImplementation(path => new Promise(resolve => pending.push(() => resolve(homeFixture(path.split('/').at(-2)!)))));
  const controllers = Array.from({ length: 5 }, () => new AbortController());
  const reads = controllers.map((c, i) => readHomeSummary(`${2330 + i}.TW`, c.signal).catch(e => e));
  expect(guidanceRead).toHaveBeenCalledTimes(2);
  controllers[2]!.abort(); controllers[0]!.abort();
  expect(guidanceRead).toHaveBeenCalledTimes(2); // active abort settles before a replacement starts
  pending.shift()!(); await reads[0]; await Promise.resolve(); await Promise.resolve();
  expect(guidanceRead).toHaveBeenCalledTimes(3);
  expect(vi.mocked(guidanceRead).mock.calls[2]![0]).toContain('2333.TW');
  pending.shift()!(); await reads[1]; await Promise.resolve(); await Promise.resolve();
  pending.shift()!(); pending.shift()!();
  await Promise.all(reads);
  expect(guidanceRead).toHaveBeenCalledTimes(4);
});

it('fails closed on wrong stock, supports independent retry and safe text rendering', async () => {
  vi.mocked(guidanceRead).mockResolvedValueOnce(homeFixture('9999.TW')).mockResolvedValueOnce({ ...homeFixture(), headline: '<img src=x onerror=alert(1)>' });
  renderWithProviders(<StockHomeSummary symbol={symbol} pageKey="all:" />);
  expect(await screen.findByRole('alert')).toHaveTextContent('暫時無法比較');
  fireEvent.click(screen.getByRole('button', { name: '重試本機摘要' }));
  expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
  expect(document.querySelector('img')).toBeNull();
  expect(screen.getByRole('link', { name: '閱讀前後比較' })).toHaveAttribute('href', '/stocks/2330.TW?view=local&research_year=2026#research-changes');
});

it('shows list before summaries, loads only page of eight, refreshes and cancels on category change', async () => {
  const pending: (() => void)[] = [];
  const stock = (index: number) => ({ symbol: `${2330 + index}.TW`, name: `匿名公司${index}`, held: false, favorite: true, last_saved_at: null, saved_cutoff_at: null, version: 'a'.repeat(64) });
  vi.mocked(guidanceRead).mockImplementation((path, signal) => {
    if (!path.endsWith('/summary')) return Promise.resolve({ enabled: true, summary_enabled: true, items: path.includes('category=held') ? [] : Array.from({ length: 8 }, (_, i) => stock(i)), next_cursor: '2337.TW' });
    return new Promise((resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true });
      pending.push(() => resolve(homeFixture(path.split('/').at(-2)!)));
    });
  });
  renderWithProviders(<MyStocks />);
  expect(await screen.findByRole('link', { name: /匿名公司7/ })).toBeInTheDocument();
  expect(screen.getAllByText('正在讀取本機摘要…')).toHaveLength(8);
  expect(vi.mocked(guidanceRead).mock.calls.filter(([p]) => p.endsWith('/summary'))).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: '持有' }));
  await screen.findByText(/此分類尚無股票/);
  await act(async () => { pending.splice(0).forEach(resolve => resolve()); });
  expect(screen.queryByText(/數字有修訂/)).not.toBeInTheDocument();
  expect(vi.mocked(guidanceRead).mock.calls.filter(([p]) => p.endsWith('/summary'))).toHaveLength(2);
  vi.mocked(guidanceRead).mockImplementation(async path => path.endsWith('/summary') ? homeFixture(path.split('/').at(-2)!) : { enabled: true, summary_enabled: true, items: [stock(0)], next_cursor: null });
  fireEvent.click(screen.getByRole('button', { name: '全部' }));
  await screen.findByText(homeFixture().headline);
  const count = vi.mocked(guidanceRead).mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: '重新讀取本機摘要' }));
  await waitFor(() => expect(guidanceRead).toHaveBeenCalledTimes(count + 1));
});
