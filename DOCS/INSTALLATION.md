# Installation

一般執行環境必須套用 repository 內的相容性 constraints：

```text
python -m pip install -c constraints.txt -r requirements.txt
```

開發與測試環境使用 `requirements-dev.txt`；該檔案會套用相同的 `constraints.txt`，避免本機與 CI 解析出不同的 FastAPI、Starlette、Pydantic、httpx2 或 pytest 版本。

## Phase 10 production-data commands

官方資料匯入與 CBC publication evidence 使用方式見 `DOCS/EVIDENCE_MODEL_V2_PHASE10.md`。CBC bare timestamp 不具升格資格；evidence 檔必須保存官方來源 reference、SHA-256、verification mode 與 actor。匯入器是顯式 CLI，不會由 FastAPI GET 或瀏覽器自動啟動。

匯入 CLI exit codes：成功 `0`、partial `2`、blocked／failed `1`。因此 Task Scheduler 不會把 persistent lock 阻擋誤判成成功；partial 表示部分資料已保存但需要人工注意。

本機可由 Windows Task Scheduler 呼叫 CLI，但不得修改 legacy scheduler 來混用 Evidence V2 ingestion。SQLite 備份／驗證／還原使用：

```text
python tools/evidence_db_recovery.py backup data/cache.db backups/evidence.db
python tools/evidence_db_recovery.py validate backups/evidence.db
python tools/evidence_db_recovery.py restore backups/evidence.db restored/cache.db
```

備份與還原目的地必須是新的明確路徑；工具不會覆寫既有檔案。
