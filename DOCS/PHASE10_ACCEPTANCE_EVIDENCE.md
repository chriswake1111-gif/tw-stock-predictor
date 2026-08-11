# Phase 10 Acceptance Evidence

本文件記錄 `feature/evidence-model-v2-phase10-data-foundation` 的本機驗收。GitHub Actions Run 與 Draft PR 編號應在 push 後補入 PR description；不得在 CI 通過前宣稱階段完成。

## Implementation commits

1. `a79a0e6` — domain contracts
2. `4df2aec` — additive SQLite migration and persistence
3. `151e8d8` — official calendar／turnover／CBC candidate ingestion
4. `6936ff0` — provider health and snapshot dependency freshness reads
5. 本文件所在 commit — tests, recovery and operating documentation
6. 第一輪 remediation commit — lease recovery, publication evidence and effective-state freshness

## Local acceptance commands

```text
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_api_golden.py
python -m pytest -q -p no:cacheprovider tests/test_phase10_migration.py
python -m py_compile src/services/production_ingestion_service.py src/services/data_freshness_service.py tools/ingest_production_data.py tools/evidence_db_recovery.py

cd frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
npm.cmd run test:visual

git diff --check
git ls-files | Select-String -Pattern '(^|/)(__pycache__/|data/cache\.db$)|\.py[co]$'
```

## Local results (2026-08-11)

- Full backend regression after first-review remediation: `390 passed, 1 locally surfaced StarletteDeprecationWarning`.
- Phase 10 focused remediation set: `38 passed`.
- v1 golden: `2 passed, 1 existing StarletteDeprecationWarning`.
- Phase 10 migration: `4 passed`.
- Frontend unit: `16 passed`; lint, typecheck and build passed.
- Playwright visual: desktop + mobile, `2 passed`.
- In-app browser QA: stale badge and historical-validity wording visible at desktop and 360×800 mobile viewport; no relevant console warnings/errors; history navigation interaction passed.
- `py_compile`: passed for both services and both CLIs.
- `git diff --check`: passed.
- tracked runtime cache gate: no output.

直接使用系統 pytest temp root 時曾因 `C:\Users\User\AppData\Local\Temp\pytest-of-User` ACL 產生 setup error；改用隔離的 repository-local `--basetemp` 後，上述完整結果通過。此為環境權限問題，不列為程式測試失敗。

## Guardrail evidence

- Provider registry／resource registry：covered by domain and repository tests.
- Run／run item／timeout／HTTP／schema drift／partial outage：covered by production ingestion tests.
- Persistent lock：15-minute lease, active lock rejection, expired orphan recovery, immutable audit event and wrong-owner release tests.
- Duplicate／corrected raw revision／immutable triggers：covered by repository and ingestion tests.
- Raw metadata-only eligibility revision：additive migration preserves immutable history and dependent foreign keys.
- CBC publication provenance：bare timestamp stays candidate; accepted evidence retains source reference, SHA-256, verification mode and actor; changed evidence is append-only.
- Calendar：official explicit session meaning only; latest visible revision per trade date wins; no weekday heuristic.
- Freshness：current requires positive proof; monthly／periodic cadence without authority is unknown; provider error blocks production-backed snapshot dependencies; late old-date correction cannot replace the latest business date.
- Snapshot：GET does not create or mutate immutable snapshot.
- Recovery：backup／validate／restore round trip with integrity and row-count evidence.
- Browser：GET-only client; stale is not described as invalid; unknown fails closed; multiple reasons visible.

## Known limitations

- 官方 calendar endpoint 的已保存 coverage 以來源明示列為準；未證明的日期回傳 unknown。
- CBC 歷史 period 仍需可稽核的官方 publication evidence；bare timestamp 或 fetch time 不具升格資格。
- Provider health 是 operational context，不是投資訊號。
- 既有 FastAPI TestClient 第三方 deprecation warning 另行追蹤，不改變本階段模型語意。
