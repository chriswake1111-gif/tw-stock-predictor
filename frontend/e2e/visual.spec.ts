import { expect, test, type Page } from "@playwright/test";
import { analysisFixture, marketFixture, performanceFixture, snapshotFixture } from "../src/test/fixtures";

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
    const body = url.includes("dependency-status")
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
