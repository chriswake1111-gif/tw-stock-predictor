import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import { researchGuidanceFixture } from '../src/test/researchGuidanceFixture';
import { waveFixture } from '../src/test/waveAssistFixture';

async function fixture(page: Page) {
  const review = researchGuidanceFixture(); review.guidance!.wave_support = waveFixture;
  const candidate = { candidate_id: 'wc_anonymous', title: '匿名固定波段候選',
    anchors: [
      { role: 'origin', market_date: '2026-01-08', price: 89, raw_price: 90, adjustment_factor: 89/90, confirmed_at: '2026-01-13' },
      { role: 'swing_end', market_date: '2026-01-22', price: 139, raw_price: 140, adjustment_factor: 139/140, confirmed_at: '2026-01-27' },
    ], confirmed_at: '2026-01-27', known_at: '2026-09-30T18:00:00Z',
    source_summary: '<img src="https://example.invalid/payload" onerror="alert(1)"> 純文字來源', limitations: ['不供歷史回測，不自動核准。'] };
  const value = { contract_version: 'wave_candidates_v1', symbol: review.symbol, enabled: true, status: 'available',
    headline: '固定測試候選可供閱讀比較', checked_at: '2026-10-01T01:00:00Z',
    requested_range: { start: '2025-09-29', end: '2026-09-30' }, actual_range: { start: '2025-09-30', end: '2026-09-30' },
    package_ref: 'wp_anonymous', known_at: candidate.known_at, basis: { label: '現金除息比例換算', anchor_date: '2026-09-30' },
    limitations: candidate.limitations, checks: [{ id: 'scope', title: '完整期間', status: 'passed', reason: '匿名固定測試資料已核對。' }],
    candidates: [candidate] };
  const values = { rule_id: 'FB-04', anchors: candidate.anchors.map(({ role, market_date, price }) => ({ role, market_date, price })),
    source: '固定匿名官方形狀測試來源', rationale: '僅固定測試，不是實際金融資料。' };
  const writes: string[] = [], external: string[] = [], problems: string[] = [];
  page.on('pageerror', error => problems.push(error.message));
  page.on('console', message => { if (message.type() === 'error') problems.push(message.text()); });
  await page.route('https://example.invalid/**', route => { external.push(route.request().url()); return route.abort(); });
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (req.method() === 'POST') writes.push(path);
    let body: unknown = { items: [], entries: [], results: [] };
    if (path.endsWith('/csrf-token')) body = { csrf_token: 'fixture' };
    else if (path.includes('/wave-candidates/')) body = path.endsWith('/wc_anonymous') ? { kind: 'anchor', values, candidate_id: candidate.candidate_id, approval_required: true } : value;
    else if (path.includes('/journal/') && path.endsWith('/preview')) body = review;
    else if (path.includes('/summary/')) body = review.current.summary;
    else if (path.includes('/assumptions/') && path.endsWith('/preview')) body = { status: 'preview_only', inputs: values, approval_required: true };
    else if (path.includes('/assumptions/') && path.endsWith('/draft')) body = { status: 'draft', record: { id: 'anonymous-draft' } };
    else if (path.endsWith('/coverage')) body = { universe_status: 'ready', coverage_ratio: 1, total_instruments: 1, phase20_materialized_count: 1 };
    else if (path.includes('/library/')) body = { enabled: true, symbol: review.symbol, name: '匿名公司', held: false, favorite: false, version: 'a'.repeat(64) };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  return { writes, external, problems };
}

for (const width of [360, 768, 1024, 1440]) {
  test(`automatic candidates retain explicit selection and keyboard reading ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page);
    await page.goto('/stocks/2330.TW?view=local#research-candidates');
    const panel = page.locator('.automatic-wave-candidates');
    await expect(panel.getByRole('heading', { name: '匿名固定波段候選' })).toBeVisible();
    await expect(panel.getByText(/確認交易日 2026-01-27/)).toHaveCount(2);
    expect(state.writes).toEqual([]);
    expect(state.external).toEqual([]);
    await panel.getByRole('button', { name: '預覽這份錨點假設' }).focus();
    await page.keyboard.press('Enter');
    await expect(panel.getByRole('heading', { name: '具體假設預覽' })).toBeVisible();
    await expect(panel.getByText('固定匿名官方形狀測試來源', { exact: true })).toBeVisible();
    expect(state.writes.filter(p => p.endsWith('/draft'))).toEqual([]);
    const draft = panel.getByRole('button', { name: '建立待核准草稿' });
    await draft.focus(); await page.keyboard.press('Enter');
    await expect(panel.getByRole('status')).toContainText('尚未核准或保存研究');
    expect(state.writes.filter(p => p.endsWith('/draft'))).toHaveLength(1);
    expect(state.writes.some(p => p.endsWith('/approve') || (p.includes('/journal/') && !p.endsWith('/preview')))).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.addScriptTag({ content: axe.source });
    const issues = await page.evaluate(async () => (await window.axe.run('.automatic-wave-candidates', { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa'] })).violations.map(v => v.id));
    expect(issues).toEqual([]);
    expect(state.problems).toEqual([]);
    await page.screenshot({ path: info.outputPath(`wave-${width}.png`), fullPage: true });
  });
}
