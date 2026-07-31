@echo off
title 杜金龍台股量化預測 - Streamlit 儀表板
chcp 65001 > nul
cd /d "%~dp0"

echo ======================================================================
echo  📈 正在啟動 [杜金龍台股量化預測 Streamlit 儀表板]...
echo ======================================================================
echo  👉 Streamlit 網址: http://localhost:8501
echo ======================================================================
echo.

python start.py --mode streamlit --port 8501

pause
