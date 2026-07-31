@echo off
title 杜金龍台股量化預測 - 機構級金融終端 (FastAPI + TradingView)
chcp 65001 > nul
cd /d "%~dp0"

echo ======================================================================
echo  📈 正在啟動 [杜金龍台股量化預測機構級金融終端]...
echo ======================================================================
echo  👉 金融終端主頁: http://localhost:8000
echo  👉 REST API 文檔: http://localhost:8000/docs
echo  👉 每日 14:30 盤後推播排程已同步啟動中
echo ======================================================================
echo.

python start.py --mode fastapi --port 8000

pause
