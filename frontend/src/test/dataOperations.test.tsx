import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DataOperationsModal } from "../components/DataOperationsModal";
import { renderWithProviders } from "./render";

describe("DataOperationsModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders modal when open and displays status", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/v2/data-operations/status")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            readiness: "ready",
            is_syncing: false,
            active_operation: null,
            market_context_summary: {
              calendar_status: "available",
              latest_eod_date: "2026-08-27",
              m1b_latest_period: "2026-07",
            },
          }),
        } as Response;
      }
      return { ok: false, status: 404 } as Response;
    });

    const handleClose = vi.fn();
    renderWithProviders(<DataOperationsModal isOpen={true} onClose={handleClose} />);

    expect(screen.getByText("本機市場資料維護與同步")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("市場資料完整")).toBeInTheDocument();
      expect(screen.getByText("最新 EOD 收盤日期：2026-08-27")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(handleClose).toHaveBeenCalled();
  });
});
