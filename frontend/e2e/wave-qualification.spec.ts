import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import { researchGuidanceFixture } from '../src/test/researchGuidanceFixture';
import { waveFixture } from '../src/test/waveAssistFixture';

async function fixture(page: Page) {
  const review = researchGuidanceFixture();
  review.guidance!.wave_support = waveFixture;
  const state = { reads: 0, fail: true, writes: [] as string[], errors: [] as string[], external: [] as string[] };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('https://example.invalid/**', route => { state.external.push(route.request().url()); return route.abort(); });
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (request.method() !== 'GET') state.writes.push(path);
    let body: unknown = {};
    if (path.includes('/wave-qualification/')) {
      state.reads++;
      if (state.fail) return route.fulfill({ status: 503, body: '{}' });
      body = { contract_version: 'wave_qualification_v1', enabled: true, symbol: review.symbol,
        checked_at: '2026-09-30T00:00:00Z', knowledge_cutoff_at: '2026-09-30T00:00:00Z',
        snapshot: { snapshot_id: 'anonymous-snapshot', source: 'FinMind', parser_version: 'daily-price-v2', raw_sha256: 'a'.repeat(64), normalized_sha256: 'b'.repeat(64), observed_at: '2026-09-29T09:00:00Z', source_url: 'https://example.invalid/never-fetch' },
        requested_range: { start: '2025-09-28', end: '2026-09-29' }, actual_range: { start: '2025-10-01', end: '2026-09-29' },
        status: 'quality_warning', headline: '部分檢查已有結果；自動候選仍缺資格證據。',
        next_step: { owner: 'engineering', action: '先補齊交易日、交易狀態及價格比較證據。' }, automatic_candidates_eligible: false,
        notices: ['最近更新未完成，目前保留先前取得的快照與日期。', '來源文字：<img src="https://example.invalid/payload" onerror="alert(1)">'],
        checks: [
          { id: 'source', title: '來源與完整性', status: 'passed', reason_code: 'source_checked', reason: '快照雜湊與來源綁定已核對。', impact: '內容完整不等於行情具備自動波段資格。', owner: 'engineering', evidence: [{ kind: 'price_snapshot', reference: 'anonymous-snapshot' }], counts: {}, samples: [] },
          { id: 'prices', title: '價格基本品質', status: 'failed', reason_code: 'invalid_price_rows', reason: '含零量資料。', impact: '不能跨越不明缺口判定轉折。', owner: 'engineering', evidence: [], counts: { rows: 200, excluded: 1 }, samples: ['2026-02-01'] },
          { id: 'sessions', title: '交易日與交易狀態', status: 'unknown', reason_code: 'session_evidence_incomplete', reason: '尚缺完整期間證據。', impact: '目前無法确认完整性。', owner: 'engineering', evidence: [], counts: { unknown: 300 }, samples: [] },
          { id: 'basis', title: '價格比較基礎', status: 'unknown', reason_code: 'corporate_action_coverage_unverified', reason: '尚無完整公司行動與還原依據。', impact: '未查到事件不等於沒有事件。', owner: 'engineering', evidence: [], counts: {}, samples: [] },
          { id: 'availability', title: '資料可知時間', status: 'unknown', reason_code: 'row_publication_times_unproven', reason: '保留整份快照取得時間。', impact: '今天取得的資料不等於過去已知。', owner: 'engineering', evidence: [], counts: {}, samples: [] },
          { id: 'confirmation', title: '轉折確認資格', status: 'unknown', reason_code: 'experimental_detector_only', reason: '只進行隔離驗證。', impact: '正式自動候選仍關閉。', owner: 'engineering', evidence: [{ kind: 'project_rule', reference: 'PIVOT-EXP-01:1.0.0' }], counts: {}, samples: [] },
        ],
        session_coverage: {
          contract_version: 'wave_session_coverage_v1', mode: 'retrospective_local', status: 'partial',
          requested_range: { start: '2026-01-01', end: '2026-01-10' }, checked_at: '2026-09-30T01:30:00Z', known_at: '2026-09-30T01:00:00Z',
          historical_availability: 'not_asserted',
          counts: { interval_days: 10, market_open: 6, market_closed: 4, stock_traded: 5, suspended: 1, outside_listing: 0, intraday: 0, missing: 1, unknown: 2, conflicts: 1 },
          samples: [{ date: '2026-01-05', kind: 'missing', reason: '行情來源缺一筆' }],
          sources: [{ source_id: 'fixture-session', label: '本機測試來源', status: 'partial', start: '2026-01-01', end: '2026-01-10', fetched_at: '2026-09-30T01:00:00Z', reference: 'fixture:session', url: 'https://example.invalid/session', limitations: ['未證明公告可知時間', '<img src="https://example.invalid/inert">'] }],
          next_step: { owner: 'assistant', action: '核對交易日、停復牌公告與行情缺口。' },
          assistant_request: '請查證 2026-01-01 至 2026-01-10 的交易日與停復牌證據。',
        },
      };
    } else if (path.endsWith('/preview')) body = review;
    else if (path.includes('/summary/')) body = review.current.summary;
    else if (path.endsWith('/bootstrap')) body = { status: 'ready', canonical_symbol: review.symbol };
    else if (path.endsWith('/coverage')) body = { universe_status: 'ready', coverage_ratio: 1, total_instruments: 1, phase20_materialized_count: 1 };
    else if (path.includes('/library/')) body = { enabled: true, symbol: review.symbol, name: '匿名研究公司', held: false, favorite: false, version: 'a'.repeat(64) };
    else body = { items: [], results: [], entries: [] };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value: string) => {
      (window as typeof window & { __clipboardWrites?: string[] }).__clipboardWrites ??= [];
      (window as typeof window & { __clipboardWrites?: string[] }).__clipboardWrites!.push(value);
    } } });
  });
  return state;
}

for (const width of [360, 768, 1024, 1440]) {
  test(`qualification is read only, recoverable and keyboard accessible ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page);
    await page.goto('/stocks/2330.TW?view=local#research-data');
    const panel = page.locator('.wave-qualification-panel').first();
    await expect(panel.getByRole('alert')).toContainText('不能視為通過').catch(error => {
      throw new Error(`${String(error)}\nPage errors: ${state.errors.join('; ')}`);
    });
    const initialReads = state.reads; // Development StrictMode replays and aborts the initial effect.
    expect(initialReads).toBeLessThanOrEqual(2);
    state.fail = false;
    await panel.getByRole('button', { name: '重試波段資料檢核' }).focus();
    await page.keyboard.press('Enter');
    await expect(panel.getByText('部分檢查已有結果；自動候選仍缺資格證據。')).toBeVisible();
    await expect(panel.getByRole('heading', { name: '交易日與停復牌：目前確認到哪裡' })).toBeVisible();
    await expect(panel.getByText('現在回頭查證，不代表歷史當時已知。')).toBeVisible();
    await expect(panel.getByText('官方有成交', { exact: true })).toBeVisible();
    await expect(panel.getByText('缺行情', { exact: true })).toBeVisible();
    await expect(panel.getByText('缺證據', { exact: true })).toBeVisible();
    await expect(panel.getByText('資料衝突', { exact: true })).toBeVisible();
    await expect(panel.getByText('各項可能重疊，不宜直接相加。')).toBeVisible();
    await expect(panel.getByText(/此按鈕只複製文字/)).toBeVisible();
    const copy = panel.getByRole('button', { name: '複製查證需求' });
    await copy.focus(); await page.keyboard.press('Enter');
    await expect(panel.getByRole('status').last()).toContainText('已複製查證需求文字');
    expect(await page.evaluate(() => (window as typeof window & { __clipboardWrites?: string[] }).__clipboardWrites)).toEqual(['請查證 2026-01-01 至 2026-01-10 的交易日與停復牌證據。']);
    const requestDetails = panel.locator('summary').filter({ hasText: '查證需求內容' });
    await expect(requestDetails.locator('..')).not.toHaveAttribute('open', '');
    await requestDetails.focus(); await page.keyboard.press('Enter');
    await expect(requestDetails).toBeFocused();
    await expect(requestDetails.locator('..')).toHaveAttribute('open', '');
    await expect(panel.getByRole('textbox', { name: '查證需求（唯讀，可手動選取複製）' })).toHaveAttribute('readonly', '');
    await expect(panel.getByText('要求檢查區間：2025-09-28 ～ 2026-09-29')).toBeVisible();
    const details = panel.locator('summary').filter({ hasText: '檢核細節' });
    await details.focus(); await page.keyboard.press('Enter');
    await expect(details).toBeFocused();
    await expect(details.locator('..')).toHaveAttribute('open', '');
    await expect(panel.getByText('價格比較基礎', { exact: true })).toBeVisible();
    await expect(panel.getByText('已確認', { exact: true })).toBeVisible();
    await expect(panel.getByText('未通過', { exact: true })).toBeVisible();
    await expect(panel.getByText('尚無證據', { exact: true })).toHaveCount(4);
    await expect(panel.locator('img,a')).toHaveCount(0);
    const coverageDetails = panel.locator('summary').filter({ hasText: '日期與來源限制' });
    await coverageDetails.focus(); await page.keyboard.press('Enter');
    await expect(panel.getByRole('rowheader', { name: '要求區間日數' })).toBeVisible();
    await expect(panel.getByText('最近一份證據取得時間：2026-09-30T01:00:00Z')).toBeVisible();
    await expect(panel.getByText('歷史當時可用性：尚未證明。')).toBeVisible();
    await expect(panel.getByText('缺少行情：行情來源缺一筆')).toBeVisible();
    await expect(panel.getByText(/部分核對/)).toBeVisible();
    await expect(panel.getByText(/未證明公告可知時間/)).toBeVisible();
    await page.addScriptTag({ content: axe.source });
    const violations = await page.evaluate(async () => (await (window as typeof window & { axe: typeof axe }).axe.run(document.querySelector('.wave-qualification-panel')!, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } })).violations.map(v => `${v.id}: ${v.nodes.map(n => n.target).join(',')}`));
    expect(violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    const retry = panel.getByRole('button', { name: '重新檢核' });
    await retry.focus();
    expect(await retry.evaluate(el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); })).toBe(true);
    await panel.screenshot({ path: info.outputPath(`wave-qualification-${width}.png`) });
    if (width === 360) {
      // Equivalent 320 CSS-pixel reflow and enhanced text-spacing acceptance.
      await page.setViewportSize({ width: 320, height: 900 });
      await page.addStyleTag({ content: '.wave-qualification-panel * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } .wave-qualification-panel p { margin-bottom: 2em !important; }' });
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      await expect(panel.getByRole('button', { name: '重新檢核' })).toBeVisible();
    }
    if (width === 1440) {
      await panel.evaluate(el => { (el as HTMLElement).style.zoom = '2'; });
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    }
    expect(state.reads).toBe(initialReads + 1);
    expect(state.writes).toEqual([]); expect(state.external).toEqual([]); expect(state.errors).toEqual([]);
  });
}
