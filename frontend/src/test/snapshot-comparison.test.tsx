import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SnapshotComparisonPage } from "../pages/SnapshotComparisonPage";
import { snapshotComparisonFixture } from "./fixtures";
import { mockReadApi, renderWithProviders } from "./render";

async function submitComparison(comparisonOverride?: unknown) {
  const fetchSpy = mockReadApi(undefined, undefined, comparisonOverride);
  const user = userEvent.setup();
  renderWithProviders(<SnapshotComparisonPage />, "/snapshots/compare");
  await screen.findAllByRole("option", { name: /snapshot-1/ });
  const base = screen.getByLabelText<HTMLSelectElement>("Base snapshot");
  const comparison = screen.getByLabelText<HTMLSelectElement>("Comparison snapshot");
  const cutoff = screen.getByLabelText<HTMLInputElement>("Comparison cutoff");
  await user.selectOptions(base, "snapshot-1");
  await user.selectOptions(comparison, "snapshot-2");
  await user.type(cutoff, "2026-08-12T12:00:00+08:00");
  await user.click(screen.getByRole("button", { name: "執行只讀比較" }));
  return fetchSpy;
}

describe("Phase 11 snapshot comparison", () => {
  it("renders server-provided stored and current deltas without recomputing them", async () => {
    const fetchSpy = await submitComparison();
    const stored = await screen.findByRole("heading", { name: "Stored Snapshot Facts" });
    const storedSection = stored.closest("section")!;
    expect(within(storedSection).getByText("resource_revision_changed")).toBeInTheDocument();
    expect(within(storedSection).getByText(/eps-r1/)).toBeInTheDocument();
    const current = screen.getByRole("heading", { name: "Current Context Changes" }).closest("section")!;
    expect(within(current).getByText("approval_revoked")).toBeInTheDocument();
    expect(screen.getByText(/系統不使用瀏覽器本地時區推測/)).toBeInTheDocument();
    expect(fetchSpy.mock.calls.every(([, init]) => !init || init.method === "GET")).toBe(true);
    expect(screen.queryByText(/買進|賣出|自動交易|保證獲利/)).not.toBeInTheDocument();
  });

  it("shows explicit no-change state from empty server delta arrays", async () => {
    await submitComparison({
      ...snapshotComparisonFixture,
      stored_deltas: [],
      current_context_deltas: [],
    });
    expect((await screen.findAllByText("沒有已登錄的語意變化。")).length).toBe(2);
  });

  it("renders incomparable reasons and does not create comparison sections", async () => {
    await submitComparison({
      ...snapshotComparisonFixture,
      status: "incomparable_contract",
      compatibility: { compatible: false, reasons: ["different_capture_mode"] },
      stored_deltas: [],
      base_current_context: null,
      comparison_current_context: null,
      current_context_deltas: [],
      reasons: ["different_capture_mode"],
    });
    expect(await screen.findByText("快照契約不可直接比較")).toBeInTheDocument();
    expect(screen.getByText("different_capture_mode")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Stored Snapshot Facts" })).not.toBeInTheDocument();
  });
});
