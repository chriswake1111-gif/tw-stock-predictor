import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import { researchGuidanceFixture, evidenceFixture } from '../src/test/researchGuidanceFixture';

async function installFixture(page: Page, candidate = false) {
  const review = researchGuidanceFixture();
  if (candidate) review.guidance!.candidates = [evidenceFixture];
  const writes: { path: string; body: Record<string, unknown>; key?: string }[] = [];
  const problems: string[] = [];
  page.on('pageerror', error => problems.push(error.message));
  page.on('console', message => { if (message.type() === 'error') problems.push(message.text()); });
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (request.method() === 'POST') writes.push({ path, body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
    let body: unknown = {};
    if (path.endsWith('/csrf-token')) body = { csrf_token: 'synthetic-csrf' };
    else if (path.includes('/journal/') && path.endsWith('/preview')) body = review;
    else if (path.includes('/journal/') && request.method() === 'POST') body = { entry_id: 'synthetic-saved' };
    else if (path.includes('/summary/')) body = review.current.summary;
    else if (path.endsWith('/bootstrap')) body = { status: 'ready', canonical_symbol: review.symbol };
    else if (path.endsWith('/coverage')) body = { universe_status: 'ready', coverage_ratio: 1, total_instruments: 1, phase20_materialized_count: 1 };
    else if (path.endsWith('/search')) body = { results: [{ canonical_symbol: review.symbol, official_code: '2330', short_name: '研究示例公司', display_name: '研究示例公司', venue: 'TWSE', security_type: '股票', has_short_name: true }], total_matches: 1 };
    else if (path.endsWith('/candidate')) body = { kind: 'pe', candidate_id: evidenceFixture.record_id, values: { label: evidenceFixture.title, fiscal_year: 2026, pe_value: 20, rationale: '第三頁；獲利未實現' } };
    else if (path.includes('/assumptions/') && path.endsWith('/draft')) {
      review.assumptions = [{ id: 'draft-1', kind: 'pe', fiscal_year: 2026, pe_value: 20, rationale: '第三頁；獲利未實現', revision_number: 1, approval: null, superseded: false }];
      body = { record: { id: 'draft-1' } };
    } else if (path.endsWith('/approve')) {
      review.assumptions[0]!.approval = { decision: 'approved', approval_id: 'approved-1' };
      review.guidance!.approved_assumptions = review.assumptions;
      body = { status: 'approved' };
    } else if (path.includes('/assumptions/')) body = { items: review.assumptions, status: 'preview_only' };
    else if (path.includes('/evidence/')) body = { items: [], next_cursor: null };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  return { writes, problems };
}

for (const width of [360, 768, 1024, 1440]) {
  test(`guided partial save, keyboard and reflow ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const { writes, problems } = await installFixture(page);
    await page.goto('/');
    await page.getByRole('textbox', { name: '搜尋股票代號或中文名稱' }).fill('研究示例公司');
    await page.getByRole('button', { name: /2330.*研究示例公司/ }).click();
    await expect(page.getByRole('heading', { name: '資料是否足夠' })).toBeVisible();
    await expect(page.getByText('下一步由：研究助理查證')).toBeVisible();
    await expect(page.getByRole('heading', { name: '目前研究發現' })).toBeVisible();
    expect(writes.filter(w => w.path.includes('/journal/') || w.path.includes('/assumptions/'))).toEqual([]);
    await page.screenshot({ path: info.outputPath(`summary-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: '候選與選擇' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText(/目前沒有適用且可直接審閱/)).toBeVisible();
    await page.getByRole('button', { name: '稍後處理，先看資料' }).click();
    await page.getByRole('button', { name: '返回快速摘要' }).click();
    await page.getByRole('textbox', { name: '你的觀察（可留白）' }).fill('只保存已查得資料，保留缺項。');
    // Reach the preview by normal Tab order, then save with the keyboard.
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: '預覽將保存的內容' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: '請確認這份部分研究' })).toBeVisible();
    await page.screenshot({ path: info.outputPath(`save-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: '確認保存這份部分研究' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('已保存部分研究、完整筆記與查證版本。')).toBeVisible();
    expect(writes.filter(w => w.path.includes('/journal/'))).toHaveLength(1);
    expect(writes.find(w => w.path.includes('/journal/'))?.body).toMatchObject({ include_research_context: true, research_year: 2026 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.addScriptTag({ content: axe.source });
    const issues = await page.evaluate(async () => (await window.axe.run('.guided-research', { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa'] })).violations.map(v => ({ id: v.id, impact: v.impact })));
    expect(issues).toEqual([]);
    expect(problems).toEqual([]);
  });
}

test('candidate preview to explicit approval, without automatic research save', async ({ page }, info) => {
  const { writes, problems } = await installFixture(page, true);
  await page.goto('/stocks/2330.TW#local-assumptions');
  await page.getByRole('button', { name: '預覽這份假設' }).click();
  await expect(page.getByRole('heading', { name: '預覽具體假設' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('candidate.png'), fullPage: true });
  expect(writes.filter(w => w.path.endsWith('/draft') || w.path.endsWith('/approve'))).toEqual([]);
  await page.getByRole('button', { name: '建立待核准草稿' }).click();
  await page.getByRole('button', { name: '確認核准此草稿' }).click();
  await expect(page.getByText('已有核准紀錄（1）')).toBeVisible();
  expect(writes.filter(w => w.path.endsWith('/approve'))).toHaveLength(1);
  expect(writes.filter(w => w.path.includes('/journal/'))).toEqual([]);
  expect(problems).toEqual([]);
});
