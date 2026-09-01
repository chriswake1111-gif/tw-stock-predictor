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

## Phase 18 Windows 內部封裝

Phase 18 的 Windows 產品化只提供本機研究與虛擬模擬能力。Launcher 綁定
`127.0.0.1` loopback、動態埠與每位使用者的單一 instance；不包含券商連線、
真實委託、自動交易、雲端同步或遙測。SQLite、設定副本、備份、復原 staging、
診斷 log 均位於 `%LOCALAPPDATA%\tw-stock-predictor`，不寫入安裝目錄。

建置需要 Python 相容環境、Node.js、PyInstaller 與 Inno Setup 6。PyInstaller 產生
的是兩個完整的 `onedir` bundle，不是單一 exe；安裝程式會遞迴複製整個 bundle：

```text
dist/windows-productization/executables/tw-stock-predictor/
  tw-stock-predictor.exe + bundled runtime files
dist/windows-productization/executables/tw-stock-predictor-server/
  tw-stock-predictor-server.exe + bundled runtime files
```

```text
python -m pip install -c constraints.txt -r requirements-dev.txt
python -m pip install -r packaging/windows/requirements-build.txt
python -B tools/build_windows_package.py
python -B tools/validate_windows_package.py dist/windows-productization --distribution-manifest dist/windows-productization/distribution-manifest.json
```

若只需產生 PyInstaller onedir payload 而不產生 installer，可使用
`--skip-installer`。封裝前會先建立 immutable resource payload 與內部
`package-manifest.json`；最終 installer 完成後才建立包含 installer SHA-256 的
`distribution-manifest.json`。驗證器是離線唯讀 gate，不會啟動服務或連線外部來源。

GitHub Actions 的 `windows-packaging.yml` 會在 Windows runner 上固定 Python、Node.js
與 Inno Setup 版本，建立並驗證 onedir layout，安裝到乾淨的暫存目錄後執行 loopback
readiness、`/research/daily`、single-instance、graceful Stop、process-tree cleanup
與 uninstall user-data preservation smoke。完整 build 與 smoke 摘要寫入 Actions job
summary；不會上傳使用者資料或 runtime database。

第一次啟動會先檢查 resource manifest，再判定 canonical database 為
`fresh`、`known_v2_upgradeable`、`legacy` 或 `corrupt_unknown`：

- `fresh` 只在 loopback server bind 前套用既有 Phase 1–14 migrations。
- `known_v2_upgradeable` 會先建立帶 migration／provenance metadata 的備份，再升級。
- `legacy` 會保留原始 database 與 SHA-256，再以獨立 staging 建立 V2 canonical。
- `corrupt_unknown` 或任何 manifest、hash、啟動 handshake 失敗都會 fail closed。

使用 `tw-stock-predictor.exe --stop` 只會停止通過 local descriptor、build identity、
parent process 與 loopback origin 驗證的本機 server；不會對任意 PID 或遠端 URL 發送控制。
