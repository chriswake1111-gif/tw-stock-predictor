import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import { researchGuidanceFixture } from '../src/test/researchGuidanceFixture';
import { anchorFixture, waveFixture } from '../src/test/waveAssistFixture';

async function fixture(page: Page) {
  const review = researchGuidanceFixture();
  review.guidance!.wave_support = waveFixture;
  review.guidance!.candidates = [anchorFixture];
  const stock = { enabled: true, symbol: review.symbol, name: '匿名研究公司', held: false, favorite: true, version: 'a'.repeat(64), last_saved_at: '2026-08-30T00:00:00Z', saved_cutoff_at: '2026-08-29T00:00:00Z' };
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (request.method() === 'POST') writes.push({ path, body: request.postDataJSON() });
    let body: unknown = {};
    if (path.endsWith('/csrf-token')) body = { csrf_token: 'synthetic-library-wave' };
    else if (path.endsWith('/library')) {
      const category = url.searchParams.get('category');
      body = { enabled: true, items: category === 'held' && !stock.held ? [] : category === 'favorites' && !stock.favorite ? [] : [stock], next_cursor: null };
    } else if (path.includes('/library/')) {
      if (request.method() === 'POST') {
        const command = request.postDataJSON();
        if (command.label === 'held') stock.held = command.value;
        if (command.label === 'favorite') stock.favorite = command.value;
        stock.version = stock.version === 'a'.repeat(64) ? 'b'.repeat(64) : 'c'.repeat(64);
      }
      body = stock;
    } else if (path.includes('/journal/') && path.endsWith('/preview')) body = review;
    else if (path.includes('/journal/') && request.method() === 'POST') body = { entry_id: 'synthetic-wave-saved' };
    else if (path.includes('/summary/')) body = review.current.summary;
    else if (path.endsWith('/bootstrap')) body = { status: 'ready', canonical_symbol: review.symbol };
    else if (path.endsWith('/coverage')) body = { universe_status: 'ready', coverage_ratio: 1, total_instruments: 1, phase20_materialized_count: 1 };
    else if (path.endsWith('/candidate')) body = { kind: 'anchor', candidate_id: anchorFixture.record_id, values: { rule_id: anchorFixture.rule_id, anchors: anchorFixture.anchors, source: '人工來源第三頁', rationale: '未還原價格，人工假設及其限制。' } };
    else if (path.includes('/assumptions/') && path.endsWith('/draft')) {
      review.assumptions = [{ id: 'anchor-draft', kind: 'anchor', anchors: anchorFixture.anchors, source: '人工來源第三頁', source_note: '未還原價格，人工假設及其限制。', revision_number: 1, approval: null, superseded: false }];
      body = { record: { id: 'anchor-draft' } };
    } else if (path.endsWith('/approve')) {
      review.assumptions[0]!.approval = { decision: 'approved', approval_id: 'anchor-approved' };
      review.guidance!.approved_assumptions = review.assumptions;
      body = { status: 'approved' };
    } else if (path.includes('/assumptions/')) body = { items: review.assumptions, status: 'preview_only' };
    else if (path.includes('/evidence/')) body = { items: [], next_cursor: null };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  return { writes, errors };
}

for (const width of [360, 768, 1024, 1440]) {
  test(`library to wave candidate, keyboard and partial save ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const { writes, errors } = await fixture(page);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '我的股票' })).toBeVisible();
    await page.getByRole('button', { name: '持有', exact: true }).click();
    await expect(page.getByText(/此分類尚無股票/)).toBeVisible();
    await page.getByRole('button', { name: '已保存研究', exact: true }).click();
    await page.addScriptTag({ content: axe.source });
    const libraryAccessibility = await page.evaluate(async () => {
      const results = await (window as typeof window & { axe: typeof axe }).axe.run(document.querySelector('.stock-library')!, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } });
      return results.violations.map(v => v.id);
    });
    expect(libraryAccessibility).toEqual([]);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: info.outputPath(`library-${width}.png`), fullPage: true });
    const link = page.getByRole('link', { name: /匿名研究公司/ });
    await link.focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: '資料是否足夠' })).toBeVisible();
    expect(writes.filter(w => w.path.includes('/library/') || w.path.includes('/assumptions/'))).toEqual([]);
    await page.getByRole('button', { name: '標記持有', exact: true }).click();
    await expect(page.getByRole('button', { name: '已標記持有', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: '已收藏', exact: true }).click();
    await expect(page.getByRole('button', { name: '收藏股票', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await page.reload();
    await expect(page.getByRole('button', { name: '已標記持有', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: '候選與選擇' }).focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('region', { name: '波段資料資格' })).toBeVisible();
    await page.getByText('為什麼還不能自動選出波段', { exact: true }).click();
    await expect(page.getByText(/核准只接受人工假設/)).toBeVisible();
    await page.getByRole('checkbox', { name: /比較：匿名來源波段候選/ }).focus(); await page.keyboard.press('Space');
    await expect(page.getByRole('table')).toContainText('100 元');
    await page.getByRole('button', { name: '預覽這份假設' }).click();
    await expect(page.getByRole('heading', { name: '預覽具體假設' })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: info.outputPath(`wave-preview-${width}.png`), fullPage: true });
    expect(writes.filter(w => w.path.endsWith('/draft') || w.path.endsWith('/approve'))).toEqual([]);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
    await page.addScriptTag({ content: axe.source });
    const accessibility = await page.evaluate(async () => {
      const results = await (window as typeof window & { axe: typeof axe }).axe.run(document.querySelector('.guided-research')!, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } });
      return results.violations.map(v => `${v.id}: ${v.nodes.map(n => n.target).join(',')}`);
    });
    expect(accessibility).toEqual([]);
    await page.getByRole('button', { name: '建立待核准草稿' }).click();
    await page.getByRole('button', { name: '確認核准此草稿' }).click();
    await expect(page.getByRole('button', { name: '確認核准此草稿' })).toHaveCount(0);
    await expect(page.getByText('已有核准紀錄（1）', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '快速摘要' }).click();
    await page.getByRole('textbox', { name: '你的觀察（可留白）' }).fill('匿名測試：保存仍缺完整波段資料的部分研究。');
    await page.getByRole('button', { name: '預覽將保存的內容' }).click();
    await page.getByRole('button', { name: '確認保存這份部分研究' }).click();
    expect(writes.filter(w => w.path.endsWith('/draft'))).toHaveLength(1);
    expect(writes.filter(w => w.path.endsWith('/approve'))).toHaveLength(1);
    expect(writes.filter(w => w.path.includes('/library/'))).toHaveLength(2);
    expect(writes.filter(w => w.path.includes('/journal/'))[0]?.body).toMatchObject({ include_research_context: true });
    expect(errors).toEqual([]);
  });
}
