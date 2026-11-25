@echo off
chcp 65001 >nul
:: 自动请求管理员权限（解决 WeGame/星际战甲 以管理员运行时按键与置顶被 UIPI 拦截的问题）
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在请求管理员权限以兼容《星际战甲》游戏内全局快捷键...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
set PYTHONUTF8=1
echo ============================================================
echo   裂罅透镜 — Warframe 紫卡与开核桃视觉助手 [管理员模式]
echo   F8: 开启/暂停监控(本地) ^| F9: 拉取WM词条 ^| ESC: 隐藏 ^| 右键托盘设置/退出
echo ============================================================
".venv\Scripts\python.exe" -u main.py
echo.
pause
