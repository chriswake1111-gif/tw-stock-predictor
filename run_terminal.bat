@echo off
title 台股市場研究與決策支援工具 (FastAPI + TradingView)
chcp 65001 > nul
cd /d "%~dp0"

echo ======================================================================
echo  📈 正在啟動 [台股市場研究與決策支援工具]...
echo  ⚠ 僅供研究與虛擬模擬，不連接券商或送出真實委託
echo ======================================================================
echo  👉 金融終端主頁: http://localhost:8000
echo  👉 REST API 文檔: http://localhost:8000/docs
echo  👉 每日 14:30 盤後推播排程已同步啟動中
echo ======================================================================
echo.

python start.py --mode fastapi --port 8000

pause
