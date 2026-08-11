import type { ForwardPeTargetCell, TargetConfluenceCluster } from "../api/types";

// Synthetic UI robustness fixtures only. These values are not production
// Evidence Model contracts and must not be used for backend/API parity tests.
const syntheticEpsScenarios = ["low", "base", "high"] as const;

export const syntheticTwelveValuationCells: ForwardPeTargetCell[] = Array.from(
  { length: 12 },
  (_, index) => ({
    status: "available",
    observation_id: `synthetic-feps-${Math.floor(index / 3) + 1}`,
    pe_scenario_id: `synthetic-pe-${index + 1}`,
    fiscal_year: 2027,
    source_name: `synthetic-source-${Math.floor(index / 3) + 1}`,
    eps_scenario: syntheticEpsScenarios[index % 3]!,
    eps_value: 5 + Math.floor(index / 3),
    pe_value: 15 + index,
    target_price: (5 + Math.floor(index / 3)) * (15 + index),
  }),
);

export const syntheticMultiClusterTargets: TargetConfluenceCluster[] = [
  {
    cluster_id: "synthetic-cluster-moderate",
    price_low: "790",
    price_high: "805",
    price_unit: "TWD_per_share",
    candidate_count: 2,
    support_count: 2,
    independent_method_count: 2,
    evidence_strength: "moderate",
    target_method_families: ["SYNTHETIC-METHOD-A", "SYNTHETIC-METHOD-B"],
    candidate_ids: ["synthetic-a-1", "synthetic-a-2"],
    shared_dependencies: [],
  },
  {
    cluster_id: "synthetic-cluster-high",
    price_low: "920",
    price_high: "950",
    price_unit: "TWD_per_share",
    candidate_count: 3,
    support_count: 3,
    independent_method_count: 3,
    evidence_strength: "high",
    target_method_families: [
      "SYNTHETIC-METHOD-A",
      "SYNTHETIC-METHOD-B",
      "SYNTHETIC-METHOD-C",
    ],
    candidate_ids: ["synthetic-b-1", "synthetic-b-2", "synthetic-b-3"],
    shared_dependencies: [],
  },
];
