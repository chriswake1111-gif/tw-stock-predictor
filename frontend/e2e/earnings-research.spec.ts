import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import { earningsReview } from '../src/test/earningsResearchFixture';

async function setup(page: Page, partial: boolean) {
  const review = earningsReview(partial);
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (request.method() === 'POST') writes.push({ path, body: request.postDataJSON() });
    let body: unknown = {};
    if (path.endsWith('/csrf-token')) body = { csrf_token: 'synthetic-csrf' };
    else if (path.endsWith('/preview')) body = review;
    else if (path.includes('/journal/') && request.method() === 'POST') body = { entry_id: 'synthetic-saved' };
    else if (path.includes('/summary/')) body = review.current.summary;
    else if (path.endsWith('/bootstrap')) body = { status: 'ready', canonical_symbol: review.symbol };
    else if (path.endsWith('/coverage')) body = { universe_status: 'ready', coverage_ratio: 1, total_instruments: 1, phase20_materialized_count: 1 };
    else if (path.includes('/assumptions/')) body = { items: [], status: 'preview_only' };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  return { review, writes, errors };
}

for (const width of [360, 768, 1024, 1440]) {
  for (const partial of [false, true]) {
    test(`four-quarter reading and partial save ${width} ${partial ? 'missing' : 'available'}`, async ({ page }, info) => {
      await page.setViewportSize({ width, height: 900 });
      const { review, writes, errors } = await setup(page, partial);
      await page.goto('/stocks/2330.TW');
      await expect(page).toHaveURL(/stocks\/2330.TW/);
      await expect(page).toHaveTitle('台股證據決策工作區');
      await expect(page.getByRole('heading', { name: '資料是否足夠' })).toBeVisible();
      await page.getByRole('button', { name: '完整證據', exact: true }).focus();
      await page.keyboard.press('Enter');
      const section = page.getByRole('region', { name: '最近四季基本每股盈餘合計' });
      await expect(section).toBeVisible();
      if (partial) await expect(section.getByText(/來源內容已有變動/)).toBeVisible();
      else await expect(section.getByText('2.75 元／股')).toBeVisible();
      await section.getByText('查看來源版本、核對範圍與限制').focus();
      await page.keyboard.press('Enter');
      await expect(section.getByRole('link', { name: '2026 年第 2 季完整財報' })).toBeVisible();
      await expect(section.getByText(/synthetic-earnings/)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await section.scrollIntoViewIfNeeded();
      await section.screenshot({ path: info.outputPath(`earnings-panel-${width}-${partial}.png`) });
      await page.screenshot({ path: info.outputPath(`earnings-${width}-${partial}.png`), fullPage: true });
      await page.addScriptTag({ content: axe.source });
      const violations = await page.evaluate(async () => (await window.axe.run('.guided-research', { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa'] })).violations.map(v => v.id));
      expect(violations).toEqual([]);
      expect(writes.filter(w => w.path.includes('/journal/') || w.path.includes('/assumptions/'))).toEqual([]);
      await page.getByRole('button', { name: '快速摘要', exact: true }).click();
      await page.getByRole('textbox', { name: '你的觀察（可留白）' }).fill('先保存已取得的資料與缺項。');
      await page.keyboard.press('Tab');
      await expect(page.getByRole('button', { name: '預覽將保存的內容' })).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('heading', { name: '請確認這份部分研究' })).toBeVisible();
      await expect(page.getByRole('region', { name: '最近四季基本每股盈餘合計' })).toBeVisible();
      await page.getByRole('button', { name: '確認保存這份部分研究' }).focus();
      await page.keyboard.press('Enter');
      await expect(page.getByText('已保存部分研究、完整筆記與查證版本。')).toBeVisible();
      expect(writes.filter(w => w.path.includes('/journal/'))).toHaveLength(1);
      expect(writes.find(w => w.path.includes('/journal/'))!.body.expected_content_fingerprint).toBe(review.content_fingerprint);
      expect(writes.filter(w => w.path.includes('/assumptions/'))).toEqual([]);
      expect(errors).toEqual([]);
    });
  }
}
