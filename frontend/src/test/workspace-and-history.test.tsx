import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { StockWorkspacePage } from "../pages/StockWorkspacePage";
import { SnapshotDetailPage } from "../pages/SnapshotPages";
import { ValidationRunPage } from "../pages/ValidationPages";
import { mockReadApi, renderWithProviders } from "./render";
import { analysisFixture } from "./fixtures";
import {
  syntheticMultiClusterTargets,
  syntheticTwelveValuationCells,
} from "./syntheticFixtures";

describe("Phase 9 route behavior", () => {
  it("renders backend valuation fields without frontend aliases", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/stocks/:symbol" element={<StockWorkspacePage />} /></Routes>, "/stocks/2330.TW");
    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("20")).toHaveLength(3);
    expect(within(table).getAllByText("100")).toHaveLength(3);
    expect(within(table).getAllByText("5")).toHaveLength(3);
  });

  it("renders all 12 synthetic backend-shaped valuation scenarios without selection", async () => {
    mockReadApi({
      ...analysisFixture,
      valuation: {
        ...analysisFixture.valuation,
        target_matrix: syntheticTwelveValuationCells,
      },
    });
    renderWithProviders(<Routes><Route path="/stocks/:symbol" element={<StockWorkspacePage />} /></Routes>, "/stocks/2330.TW");
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(13);
    expect(within(table).getAllByText(/synthetic-source-/)).toHaveLength(12);
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders FB-03 as target and FB-04 as support from backend semantic roles", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/stocks/:symbol" element={<StockWorkspacePage />} /></Routes>, "/stocks/2330.TW");
    const fb03 = await screen.findByText(/等幅目標情境 · FB-03/);
    const fb04 = screen.getByText(/0.382 支撐情境 · FB-04/);
    const technicalCard = fb03.closest(".evidence-card");
    expect(technicalCard).toContainElement(fb04);
    expect(technicalCard).toHaveTextContent("100 TWD");
    expect(technicalCard).toHaveTextContent("92.36 TWD");
    expect(technicalCard).toHaveTextContent("Role: Target");
    expect(technicalCard).toHaveTextContent("Role: Support");
    expect(fb04.closest("article")).not.toHaveTextContent("Role: Target");
    expect(fb03.closest("article")).not.toHaveTextContent("Role: Support");
    expect(screen.queryByText(/勝率|成功率|預測機率|Strong Buy|強力買進|最佳模型/)).not.toBeInTheDocument();
  });

  it("keeps explicitly synthetic disjoint target cluster metrics isolated", async () => {
    mockReadApi({
      ...analysisFixture,
      target_confluence: {
        ...analysisFixture.target_confluence,
        overlap_ranges: syntheticMultiClusterTargets,
      },
    });
    renderWithProviders(<Routes><Route path="/stocks/:symbol" element={<StockWorkspacePage />} /></Routes>, "/stocks/2330.TW");
    const clusterOne = (await screen.findByText("交集區 1")).closest("article");
    const clusterTwo = screen.getByText("交集區 2").closest("article");
    expect(clusterOne).toHaveTextContent("790");
    expect(clusterOne).toHaveTextContent("805");
    expect(clusterOne).toHaveTextContent("獨立方法數 2");
    expect(clusterOne).toHaveTextContent("方法匯聚程度：moderate");
    expect(clusterOne).not.toHaveTextContent("獨立方法數 3");
    expect(clusterTwo).toHaveTextContent("920");
    expect(clusterTwo).toHaveTextContent("950");
    expect(clusterTwo).toHaveTextContent("獨立方法數 3");
    expect(clusterTwo).toHaveTextContent("方法匯聚程度：high");
    expect(clusterTwo).not.toHaveTextContent("獨立方法數 2");
    expect(screen.getAllByText(/不代表推薦順位/)).toHaveLength(2);
  });

  it("keeps historical reconstruction persistently labeled", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/snapshots/:snapshotId" element={<SnapshotDetailPage />} /></Routes>, "/snapshots/snapshot-1");
    expect(await screen.findByText("歷史重建")).toBeInTheDocument();
    expect(screen.getByText(/只呈現已保存輸出|保存的分析輸出/)).toBeInTheDocument();
    expect(screen.getAllByText("部分資料可用").length).toBeGreaterThan(0);
  });

  it("shows numerator denominator sample n horizon and origin", async () => {
    mockReadApi();
    renderWithProviders(<Routes><Route path="/validation/runs/:runId" element={<ValidationRunPage />} /></Routes>, "/validation/runs/run-1");
    await waitFor(() => expect(screen.getByText("7 / 10")).toBeInTheDocument());
    expect(screen.getByText("有效樣本 n=10")).toBeInTheDocument();
    expect(screen.getByText("20 sessions")).toBeInTheDocument();
    expect(screen.getByText("歷史重建")).toBeInTheDocument();
    expect(screen.getByText("歷史條件下目標區間觸及觀察")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
    expect(screen.getByText(/各 evaluation origin 均獨立分組呈現/)).toBeInTheDocument();
    expect(screen.getByText(/描述性歷史觀察，不是未來預測機率/)).toBeInTheDocument();
  });
});
