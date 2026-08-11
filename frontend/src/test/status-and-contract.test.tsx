import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceLevelBadge, MethodStrengthIndicator, OriginBadge, StatusBadge } from "../components/evidence/EvidencePrimitives";
import { sectionStatusValues } from "../api/types";
import { statusPresentation } from "../lib/status";
import { renderWithProviders } from "./render";
import { analysisFixture, snapshotFixture } from "./fixtures";

describe("Phase 9 semantic guardrails", () => {
  it("renders every authoritative status and fails unknown closed", () => {
    renderWithProviders(<div>{sectionStatusValues.map((status) => <StatusBadge key={status} status={status} />)}<StatusBadge status="surprise_state" /></div>);
    sectionStatusValues.forEach((status) => expect(screen.getByText(statusPresentation[status].label)).toBeInTheDocument());
    expect(screen.getByText("Unknown analysis state")).toBeInTheDocument();
  });

  it("keeps evidence grade, method confluence and historical origin distinct", () => {
    renderWithProviders(<div><EvidenceLevelBadge level="A" /><MethodStrengthIndicator strength="moderate" independentMethods={2} /><OriginBadge origin="historical_reconstruction" /></div>);
    expect(screen.getByText("研究證據等級 A")).toBeInTheDocument();
    expect(screen.getByText(/方法匯聚程度/)).toBeInTheDocument();
    expect(screen.getByText("歷史重建")).toBeInTheDocument();
  });

  it("browser client is GET-only and isolated from legacy v1 financial DTO", () => {
    const source = readFileSync(resolve(process.cwd(), "src/api/client.ts"), "utf8");
    expect(source).not.toContain("/api/analysis/");
    expect(source).not.toMatch(/method:\s*["'](?:POST|PUT|PATCH|DELETE)/);
    expect(source).not.toContain("X-Admin-API-Key");
    expect(source).not.toContain("Idempotency-Key");
  });

  it("models exact snapshot detail without inventing list-only analysis_status", () => {
    expect(snapshotFixture.snapshot).not.toHaveProperty("analysis_status");
    expect(snapshotFixture.snapshot.output.status).toBe("partial");
  });

  it("keeps production TGT-01 metadata at grade C project operationalization", () => {
    expect(analysisFixture.target_confluence.rules_used).toEqual([
      expect.objectContaining({
        rule_id: "TGT-01",
        evidence_level: "C",
        implementation_mode: "project_operationalization",
        project_operationalization: true,
      }),
    ]);
  });
});
