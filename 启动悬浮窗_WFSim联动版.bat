@echo off
chcp 65001 >nul
cd /d "%~dp0"
set RIVEN_WFSIM_EDITION=1
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py --overlay --wfsim-edition
) else (
    start "" pythonw main.py --overlay --wfsim-edition
)
