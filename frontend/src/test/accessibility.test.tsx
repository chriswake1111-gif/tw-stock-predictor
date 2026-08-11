import axe from "axe-core";
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import App from "../App";
import { mockReadApi, renderWithProviders } from "./render";

describe("Phase 9 accessibility smoke", () => {
  afterEach(() => document.querySelectorAll("#axe-audit-root").forEach((node) => node.remove()));

  for (const route of ["/stocks/2330.TW", "/market", "/snapshots/snapshot-1", "/validation/runs/run-1"]) {
    it(`has no automated structural violations at ${route}`, async () => {
      mockReadApi();
      const { container } = renderWithProviders(<App />, route);
      await screen.findByRole("main");
      await new Promise((resolve) => setTimeout(resolve, 0));
      const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
      expect(results.violations.map((violation) => violation.id)).toEqual([]);
    });
  }
});
