"""Application service for Forward EPS ingestion and v2 valuation."""

from __future__ import annotations

from src.domain.valuation import ForwardEPSObservation, PEScenario
from src.engine.forward_pe_valuation import ForwardPEValuationEngine
from src.repositories.forward_eps_repository import ForwardEPSRepository


class ForwardEPSService:
    def __init__(self, db_path: str = "data/cache.db"):
        self.repository = ForwardEPSRepository(db_path)
        self.engine = ForwardPEValuationEngine(self.repository)

    def ingest_forward_eps(
        self, observation: ForwardEPSObservation, idempotency_key: str
    ) -> dict:
        return self.repository.add_forward_eps(observation, idempotency_key)

    def ingest_pe_scenario(self, scenario: PEScenario, idempotency_key: str) -> dict:
        return self.repository.add_pe_scenario(scenario, idempotency_key)

    def analyze(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        industry: str | None = None,
        market: str | None = None,
    ) -> dict:
        return self.engine.evaluate(
            symbol,
            knowledge_cutoff_at,
            industry=industry,
            market=market,
        )
