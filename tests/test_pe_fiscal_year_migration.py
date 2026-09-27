"""Upgrade genuine pre-year evidence without guessing or rewriting history."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace
import sqlite3

import pytest

from src.domain.valuation import ApprovalResourceType, ApprovalStatus, PEScenario, PEScope, ValuationApproval
from src.engine.forward_pe_valuation import ForwardPEValuationEngine
from src.repositories import migration_runner
from src.repositories.forward_eps_repository import ForwardEPSRepository, _fingerprint
from src.services.daily_research_journal_service import DailyResearchJournalService
from src.services.evidence_backup_service import EvidenceBackupService
from tests.test_forward_pe_valuation import add_eps


def test_legacy_upgrade_retry_revision_and_backup_restore(tmp_path, monkeypatch):
    db = str(tmp_path / "legacy.db")
    with monkeypatch.context() as old:
        cutoff = migration_runner.ADDITIONAL_MIGRATION_IDS.index("20260926_26_pe_fiscal_year")
        old.setattr(migration_runner, "ADDITIONAL_MIGRATION_IDS", migration_runner.ADDITIONAL_MIGRATION_IDS[:cutoff])
        old.setattr(migration_runner, "ADDITIONAL_MIGRATION_FILES", migration_runner.ADDITIONAL_MIGRATION_FILES[:cutoff])
        migration_runner.apply_valuation_migration(db)
    repo = ForwardEPSRepository(db, auto_migrate=False)
    add_eps(repo, series="source", source="Source", eps=10)
    legacy = PEScenario(logical_series_id="old-pe", revision_number=1, label="legacy", pe_value=20,
                        rationale="original evidence", evidence_level="U", scope=PEScope.SYMBOL,
                        symbol="2330.TW", available_at="2026-08-01T00:00:00Z", approval_status=ApprovalStatus.DRAFT)
    payload = legacy.canonical_payload()
    fingerprint = _fingerprint(payload)
    pe_id = f"pe_{fingerprint[:24]}"
    approval = ValuationApproval(approval_id="legacy-approval", resource_type=ApprovalResourceType.PE_SCENARIO,
                                 resource_id=pe_id, decision=ApprovalStatus.APPROVED, rule_id="VAL-04",
                                 evidence_level="B", project_operationalization=False, approved_by="reviewer",
                                 rationale="original decision", available_at="2026-08-01T00:02:00Z")
    decision = approval.canonical_payload()
    approval_hash = _fingerprint(decision)
    event_id = f"approval_{approval_hash[:24]}"
    # Insert exact old-format immutable rows before migration, including retry bindings.
    with closing(sqlite3.connect(db)) as conn, conn:
        for table, row in (
            ("pe_scenarios", {**payload, "id": pe_id, "payload_fingerprint": fingerprint,
                              "idempotency_key": "legacy-pe-key", "ingested_at": "2026-08-01T00:01:00.000000Z"}),
            ("valuation_approvals", {**decision, "approval_event_id": event_id, "payload_fingerprint": approval_hash,
                                     "ingested_at": "2026-08-01T00:03:00.000000Z"}),
        ):
            conn.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        repo._bind_idempotency(conn, "legacy-pe-key", fingerprint, "pe_scenario", pe_id)
        repo._bind_idempotency(conn, "legacy-approval-key", approval_hash, "approval", event_id)
    journal = DailyResearchJournalService(db)
    journal.summary_service = SimpleNamespace(get_summary=lambda *a, **k: {
        "canonical_symbol": "2330.TW", "valuation_context": {"status": "available", "target_matrix": [{"target_price": 200}]}})
    saved = journal.save("2330.TW", "2026-08-02T00:00:00Z", "original note", "legacy-journal")
    backup = str(tmp_path / "before-upgrade.db")
    EvidenceBackupService.backup(db, backup)

    migration_runner.apply_valuation_migration(db)
    migration_runner.apply_valuation_migration(db)  # repeated startup is harmless
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT fiscal_year,payload_fingerprint FROM pe_scenarios").fetchone() == (None, fingerprint)
        assert conn.execute("SELECT payload_fingerprint FROM valuation_approvals WHERE approval_id='legacy-approval'").fetchone()[0] == approval_hash
    assert journal.history("2330.TW")["entries"] == [saved]
    assert repo.add_pe_scenario(legacy, "legacy-pe-key")["created"] is False
    assert repo.add_approval(approval, "legacy-approval-key")["created"] is False
    with pytest.raises(ValueError, match="pe_fiscal_year_required"):
        repo.add_pe_scenario(replace(legacy, logical_series_id="new-unbound"), "new-unbound-key")
    with pytest.raises(ValueError, match="create_revision"):
        repo.add_approval(replace(approval, approval_id="new-approval"), "new-approval-key")
    engine = ForwardPEValuationEngine(repo)
    old_cutoff = "2026-08-02T00:00:00Z"
    result = engine.evaluate("2330.TW", old_cutoff)
    assert result["target_matrix"] == [] and result["reason"] == "pe_fiscal_year_required"
    assert result["year_pairing"]["unbound_pe_ids"] == [pe_id]
    revision = repo.add_pe_scenario(replace(legacy, revision_number=2, revision_of=pe_id, fiscal_year=2027,
                                            available_at="2026-08-03T00:00:00Z"), "bound-pe-key",
                                    ingested_at="2026-08-03T00:01:00Z")
    assert engine.evaluate("2330.TW", "2026-08-04T00:00:00Z")["target_matrix"] == []
    repo.add_approval(replace(approval, approval_id="bound-approval", resource_id=revision["id"],
                             available_at="2026-08-03T00:02:00Z"), "bound-approval-key",
                      ingested_at="2026-08-03T00:03:00Z")
    assert engine.evaluate("2330.TW", "2026-08-04T00:00:00Z")["target_matrix"][0]["target_price"] == 200
    assert engine.evaluate("2330.TW", old_cutoff)["target_matrix"] == []
    repo.add_approval(replace(approval, approval_id="legacy-revoke", decision=ApprovalStatus.REVOKED,
                             available_at="2026-08-05T00:00:00Z"), "legacy-revoke-key",
                      ingested_at="2026-08-05T00:01:00Z")  # old evidence can still be revoked

    after = str(tmp_path / "after-upgrade.db")
    restored = str(tmp_path / "restored-new.db")
    EvidenceBackupService.backup(db, after)
    EvidenceBackupService.restore(after, restored)
    assert DailyResearchJournalService(restored).history("2330.TW")["entries"] == [saved]
    assert ForwardPEValuationEngine(ForwardEPSRepository(restored)).evaluate("2330.TW", "2026-08-04T00:00:00Z")["target_matrix"][0]["pe_fiscal_year"] == 2027
    rollback = str(tmp_path / "restored-old.db")
    EvidenceBackupService.restore(backup, rollback)
    with closing(sqlite3.connect(rollback)) as conn:
        assert "fiscal_year" not in [r[1] for r in conn.execute("PRAGMA table_info(pe_scenarios)")]
    assert DailyResearchJournalService(rollback).history("2330.TW")["entries"] == [saved]
