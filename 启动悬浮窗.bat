@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo ============================================================
echo   裂罅透镜 — Warframe 紫卡与开核桃视觉助手
echo   F8: 开启/暂停监控(本地) ^| F9: 拉取WM词条 ^| ESC: 隐藏 ^| 右键托盘设置/退出
echo ============================================================
".venv\Scripts\python.exe" -u main.py
echo.
pause
