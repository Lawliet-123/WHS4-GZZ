@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0auto_paint.py"
    if not errorlevel 1 exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0auto_paint.py"
    if not errorlevel 1 exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0auto_paint.py"
    if not errorlevel 1 exit /b 0
)

echo [ERROR] Python 3 could not start.
echo Install Python 3, or create .venv in this folder, and try again.
pause
exit /b 1
