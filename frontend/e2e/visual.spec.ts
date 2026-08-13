import { expect, test, type Page } from "@playwright/test";
import { analysisFixture, marketFixture, performanceFixture, snapshotComparisonFixture, snapshotFixture } from "../src/test/fixtures";

const runFixture = {
  status: "available",
  evaluation_run: {
    evaluation_run_id: "run-1", evaluation_profile_revision_id: "profile-1", evaluator_version: "phase8_mvp_v1",
    evaluation_origin_policy: "separate_by_evaluation_origin", outcome_resource_manifest_id: "manifest-1",
    universe_definition: "fixed cohort", created_at: "2026-08-10T02:00:00Z", status: "completed",
    snapshot_memberships: [], results: [],
  },
};

async function mockApi(page: Page) {
  await page.route("**/api/v2/**", async (route) => {
    const url = route.request().url();
    const body = url.includes("research/queue")
      ? {
          status: "available",
          comparison_cutoff: "2026-08-13T00:00:00Z",
          items: [{
            watchlist_item: {
              watchlist_item_id: "research-watchlist-1", symbol: "2330.TW",
              membership_state: "active", created_at: "2026-08-12T00:00:00Z",
              updated_at: "2026-08-12T00:00:00Z", archived_at: null,
              workflow_contract_version: "research_review_queue_v1",
            },
            analysis_status: "available", freshness_status: "stale",
            comparison_status: "comparable", review_state: "comparable_without_deltas",
            comparison_has_deltas: false, stored_delta_count: 0,
            current_context_delta_count: 0,
            latest_snapshot_reference: { snapshot_id: "snapshot-1", symbol: "2330.TW" },
            latest_review_event_reference: {
              review_event_id: "review-1", acknowledged_snapshot_id: "snapshot-1",
              reviewed_at: "2026-08-12T00:00:00Z",
            },
            reason_codes: [],
          }],
        }
      : url.includes("analysis/snapshots/compare")
      ? snapshotComparisonFixture
      : url.includes("dependency-status")
      ? {
          status: "available",
          dependency_status: {
            snapshot_id: "snapshot-1", comparison_cutoff: "2026-08-11T02:00:00Z",
            checked_at: "2026-08-11T02:00:00Z", freshness_status: "stale",
            reasons: ["newer_eligible_forward_eps_revision"], checked_dependencies: [],
            historical_snapshot_validity: "unchanged",
          },
          cutoff_policy: { mode: "request_received_at", timezone: "UTC" },
        }
      : url.includes("market-overview")
      ? marketFixture
      : url.includes("analysis/snapshots/snapshot-1")
        ? snapshotFixture
        : url.includes("analysis/snapshots")
          ? {
              status: "available",
              snapshots: [
                { ...snapshotFixture.snapshot, analysis_status: snapshotFixture.snapshot.output.status },
                { ...snapshotFixture.snapshot, snapshot_id: "snapshot-2", created_at: "2026-08-11T02:00:00Z", analysis_status: snapshotFixture.snapshot.output.status },
              ],
              next_before: null,
              filters: { symbol: null, capture_mode: null },
            }
        : url.includes("performance/summary")
          ? performanceFixture
          : url.includes("evaluations/runs/run-1")
            ? runFixture
            : analysisFixture;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("captures deterministic Phase 9 acceptance states", async ({ page }, testInfo) => {
  await mockApi(page);
  const captures = [
    ["/stocks/2330.TW", "stock-partial", "2330.TW"],
    ["/market", "market-quality-warning", "市場概況"],
    ["/snapshots/snapshot-1", "historical-reconstruction", "保存的分析輸出"],
    ["/validation/runs/run-1", "historical-validation", "7 / 10"],
  ] as const;
  for (const [path, name, visibleText] of captures) {
    await page.goto(path);
    await expect(page.locator("main").getByText(visibleText, { exact: false }).first()).toBeVisible();
    const mobileStock = testInfo.project.name === "mobile" && name === "stock-partial";
    if (mobileStock) await page.locator(".scenario-table-wrap").evaluate((element) => element.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: !mobileStock });
  }
});

test("captures the Phase 11 read-only snapshot comparison", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/snapshots/compare");
  await page.getByLabel("Base snapshot").selectOption("snapshot-1");
  await page.getByLabel("Comparison snapshot").selectOption("snapshot-2");
  await page.getByLabel("Comparison cutoff").fill("2026-08-12T12:00:00+08:00");
  await page.getByRole("button", { name: "執行只讀比較" }).click();
  await expect(page.getByRole("heading", { name: "Stored Snapshot Facts" })).toBeVisible();
  await expect(page.getByText("approval_revoked").first()).toBeVisible();
  const noHorizontalOverflow = await page.locator("main").evaluate((element) => element.scrollWidth <= element.clientWidth);
  expect(noHorizontalOverflow).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("snapshot-comparison.png"), fullPage: true });
});

test("captures the Phase 12 research review queue without temporal claims", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/research");
  await page.getByLabel("比較截止時間（含時區）").fill("2026-08-13T00:00:00Z");
  await page.getByRole("button", { name: "載入清單" }).click();
  await expect(page.getByText("與上次複核快照相比無差異")).toBeVisible();
  await expect(page.getByText("stale")).toBeVisible();
  await expect(page.getByText(/自上次複核後/)).toHaveCount(0);
  const noHorizontalOverflow = await page.locator("main").evaluate(
    (element) => element.scrollWidth <= element.clientWidth,
  );
  expect(noHorizontalOverflow).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("research-review-queue.png"), fullPage: true });
});
