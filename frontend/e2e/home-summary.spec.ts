import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import { researchGuidanceFixture } from '../src/test/researchGuidanceFixture';

async function fixture(page: Page) {
  const review = researchGuidanceFixture();
  const symbol = review.symbol;
  review.previous = { entry_id: 'baseline-1', created_at: '2026-09-01T00:00:00Z', note: '匿名已確認筆記', summary: review.current.summary };
  review.comparison = { status: 'available', assumptions_changed: false, facts: [{ field: 'official_close', before: { value: 100, date: '2026-09-01' }, after: { value: 105, date: '2026-09-02' }, delta: 5, status: 'comparable' }] };
  const state = { target: 'research-changes', reads: 0, writes: [] as string[], errors: [] as string[], failed: true };
  page.on('pageerror', e => state.errors.push(e.message));
  const stock = { enabled: true, symbol, name: '匿名研究公司', favorite: true, held: true, version: 'a'.repeat(64), last_saved_at: '2026-09-01T00:00:00Z', saved_cutoff_at: '2026-09-01T00:00:00Z' };
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (request.method() !== 'GET') state.writes.push(path);
    let body: unknown = {};
    if (path.endsWith('/library')) body = { enabled: true, summary_enabled: true, items: [stock, { ...stock, symbol: '3491.TWO', name: '匿名缺項公司' }], next_cursor: null };
    else if (path.includes('/library/') && path.endsWith('/summary')) {
      const key = path.split('/').at(-2);
      if (key === '3491.TWO' && state.failed) return route.fulfill({ status: 503, body: '{}' });
      if (key === symbol) state.reads++;
      body = { contract_version: 'research_home_summary_v1', symbol: key, enabled: true, local_only: true, prepared_at: '2026-09-30T00:00:00Z', selected_year: 2026,
        baseline: { entry_id: `baseline-${state.reads}`, created_at: '2026-09-01T00:00:00Z', knowledge_cutoff_at: '2026-09-01T00:00:00Z' },
        status: 'changed', headline: `官方收盤價數字與資料日期有變化。（本機讀取 ${state.reads}）`,
        dates: [{ field: 'official_close', label: '官方收盤價', previous: '2026-09-01', current: '2026-09-02' }],
        limitations: [{ id: 'prices:stale', text: '部分資料已過期，不能视為最新。', owner: 'program' }, { id: 'pe', text: '估值倍數仍待助理查證', owner: 'assistant' }],
        next_step: { target: state.target, label: state.target === 'research-changes' ? '閱讀前後比較' : state.target === 'research-candidates' ? '閱讀候選' : '閱讀資料狀態', owner: 'user' } };
    } else if (path.includes('/library/')) body = stock;
    else if (path.endsWith('/preview')) body = review;
    else if (path.includes('/summary/')) body = review.current.summary;
    else if (path.endsWith('/bootstrap')) body = { status: 'ready', canonical_symbol: symbol };
    else if (path.endsWith('/coverage')) body = { universe_status: 'ready', coverage_ratio: 1, total_instruments: 2, phase20_materialized_count: 2 };
    else if (path.includes('/journal')) body = { server_time: '2026-09-30T00:00:00Z', entries: [], items: [], next_symbol: null };
    else if (path.includes('/assumptions/')) body = { items: [] };
    else body = { items: [], results: [] };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  return state;
}

for (const width of [360, 768, 1024, 1440]) {
  test(`local home summary navigation and keyboard ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page);
    await page.goto('/');
    const library = page.getByRole('region', { name: '我的股票', exact: true });
    await expect(library.getByText(/本機讀取 \d+/)).toBeVisible();
    await expect(library.getByText(/行情資料日：2026-09-01 → 2026-09-02/)).toBeVisible();
    await expect(library.getByText(/比較基準：/)).toContainText('2026');
    await expect(library.getByRole('alert')).toContainText('暫時無法比較');
    await page.addScriptTag({ content: axe.source });
    const violations = await page.evaluate(async () => (await (window as typeof window & { axe: typeof axe }).axe.run(document.querySelector('.stock-library')!, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } })).violations.map(v => v.id));
    expect(violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.screenshot({ path: info.outputPath(`home-summary-${width}.png`), fullPage: true });
    for (const [target, label, title] of [
      ['research-changes', '閱讀前後比較', '與前次研究相比'],
      ['research-candidates', '閱讀候選', '先看依據，再決定是否採用'],
      ['research-data', '閱讀資料狀態', '每個判斷，都能回到依據'],
    ]) {
      state.target = target!;
      if (target !== 'research-changes') {
        const prior = state.reads;
        await page.goBack();
        await expect.poll(() => state.reads).toBeGreaterThan(prior);
      }
      const link = library.getByRole('link', { name: label!, exact: true });
      await link.focus(); await page.keyboard.press('Enter');
      await expect(page.getByRole('heading', { name: title!, exact: true })).toBeFocused();
      await expect(page.getByRole('heading', { name: title!, exact: true })).toBeInViewport();
      expect(state.writes).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      if (target === 'research-changes') await expect(page.getByText('匿名已確認筆記')).toBeVisible();
    }
    await page.goBack();
    await expect(library.getByRole('alert')).toContainText('暫時無法比較');
    state.failed = false;
    await library.getByRole('button', { name: '重試本機摘要' }).click();
    await expect(library.getByRole('alert')).toHaveCount(0);
    expect(state.writes).toEqual([]);
    expect(state.errors).toEqual([]);
    await page.goto('/research/daily');
    await expect(page.getByRole('heading', { name: '每日研究', exact: true })).toBeVisible();
  });
}
