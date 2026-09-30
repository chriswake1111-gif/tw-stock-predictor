import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MyStocks, StockLabels } from '../components/MyStocks';
import { guidanceRead } from '../api/guidanceClient';
import { researchMutation } from '../api/researchClient';
import { renderWithProviders } from './render';

vi.mock('../api/guidanceClient', async original => ({ ...await original<typeof import('../api/guidanceClient')>(), guidanceRead: vi.fn() }));
vi.mock('../api/researchClient', async original => ({ ...await original<typeof import('../api/researchClient')>(), researchMutation: vi.fn() }));
const stock = { enabled: true, symbol: '3491.TWO', name: '匿名波段公司', held: true, favorite: true, version: 'a'.repeat(64), last_saved_at: '2026-09-01T00:00:00Z', saved_cutoff_at: '2026-08-31T00:00:00Z' };
beforeEach(() => vi.resetAllMocks());

it('reads and filters saved stocks without writing or relying on localStorage', async () => {
  vi.mocked(guidanceRead).mockResolvedValue({ enabled: true, items: [stock], next_cursor: null });
  renderWithProviders(<MyStocks />);
  expect(await screen.findByRole('link', { name: /匿名波段公司/ })).toHaveAttribute('href', '/stocks/3491.TWO?view=local');
  expect(screen.getByText(/研究資訊截止/)).toHaveTextContent('2026');
  fireEvent.click(screen.getByRole('button', { name: '已保存研究' }));
  await waitFor(() => expect(guidanceRead).toHaveBeenLastCalledWith(expect.stringContaining('category=researched'), expect.anything()));
  expect(researchMutation).not.toHaveBeenCalled();
});

it('explains empty, failed and disabled states', async () => {
  vi.mocked(guidanceRead).mockResolvedValue({ enabled: true, items: [], next_cursor: null });
  const view = renderWithProviders(<MyStocks />);
  expect(await screen.findByText(/此分類尚無股票/)).toBeInTheDocument();
  view.unmount();
  vi.mocked(guidanceRead).mockRejectedValue(new Error('offline'));
  const failed = renderWithProviders(<MyStocks />);
  expect(await screen.findByRole('alert')).toHaveTextContent('既有紀錄仍保留');
  failed.unmount();
  vi.mocked(guidanceRead).mockResolvedValue({ enabled: false, items: [], next_cursor: null });
  renderWithProviders(<MyStocks />);
  await waitFor(() => expect(screen.queryByRole('heading', { name: '我的股票' })).not.toBeInTheDocument());
});

it('retries a lost response with identical value, version and request key then reads back', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(stock);
  vi.mocked(researchMutation).mockRejectedValueOnce(new Error('回應中斷')).mockImplementationOnce(async () => {
    vi.mocked(guidanceRead).mockResolvedValue({ ...stock, held: false, version: 'b'.repeat(64) });
    return {};
  });
  renderWithProviders(<StockLabels symbol={stock.symbol} />);
  fireEvent.click(await screen.findByRole('button', { name: '已標記持有' }));
  fireEvent.click(await screen.findByRole('button', { name: '重試相同操作' }));
  expect(await screen.findByRole('button', { name: '標記持有' })).toHaveAttribute('aria-pressed', 'false');
  expect(researchMutation).toHaveBeenCalledTimes(2);
  expect(vi.mocked(researchMutation).mock.calls[0]).toEqual(vi.mocked(researchMutation).mock.calls[1]);
  expect(vi.mocked(researchMutation).mock.calls[0]?.[1]).toEqual({ label: 'held', value: false, version: stock.version });
  expect(screen.getByRole('button', { name: '已收藏' })).toHaveAttribute('aria-pressed', 'true');
});

it('shows conflict and reads the changed state without sending another command', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(stock);
  vi.mocked(researchMutation).mockImplementationOnce(async () => {
    vi.mocked(guidanceRead).mockResolvedValue({ ...stock, favorite: false, version: 'b'.repeat(64) });
    throw new Error('research_library_state_conflict');
  });
  renderWithProviders(<StockLabels symbol={stock.symbol} />);
  fireEvent.click(await screen.findByRole('button', { name: '已收藏' }));
  expect(await screen.findByRole('button', { name: '收藏股票' })).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent('其他頁面變更');
  expect(researchMutation).toHaveBeenCalledTimes(1);
});
