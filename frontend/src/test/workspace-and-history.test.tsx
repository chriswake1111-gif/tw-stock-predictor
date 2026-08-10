import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { StockWorkspacePage } from "../pages/StockWorkspacePage";
import { SnapshotDetailPage } from "../pages/SnapshotPages";
import { ValidationRunPage } from "../pages/ValidationPages";
import { mockReadApi, renderWithProviders } from "./render";

describe("Phase 9 route behavior", () => {
  it("renders FB-04 only inside technical support context", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/stocks/:symbol" element={<StockWorkspacePage />} /></Routes>, "/stocks/2330.TW");
    const fb04 = await screen.findByText("FB-04");
    expect(fb04.closest(".evidence-card")).toHaveTextContent("人工錨點技術情境");
    expect(fb04.closest(".evidence-card")).not.toHaveTextContent("目標區間匯聚");
    expect(screen.queryByText(/勝率|成功率|預測機率|Strong Buy|強力買進|最佳模型/)).not.toBeInTheDocument();
  });

  it("keeps historical reconstruction persistently labeled", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/snapshots/:snapshotId" element={<SnapshotDetailPage />} /></Routes>, "/snapshots/snapshot-1");
    expect(await screen.findByText("歷史重建")).toBeInTheDocument();
    expect(screen.getByText(/只呈現已保存輸出|保存的分析輸出/)).toBeInTheDocument();
  });

  it("shows numerator denominator sample n horizon and origin", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/validation/runs/:runId" element={<ValidationRunPage />} /></Routes>, "/validation/runs/run-1");
    await waitFor(() => expect(screen.getByText("7 / 10")).toBeInTheDocument());
    expect(screen.getByText("有效樣本 n=10")).toBeInTheDocument();
    expect(screen.getByText("20 sessions")).toBeInTheDocument();
    expect(screen.getByText("歷史重建")).toBeInTheDocument();
    expect(screen.getByText("歷史條件下目標區間觸及觀察")).toBeInTheDocument();
  });
});
