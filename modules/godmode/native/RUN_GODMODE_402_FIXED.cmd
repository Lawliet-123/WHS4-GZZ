@echo off
setlocal

set "INJECTOR=%~dp0godmode-simple-injector.exe"
set "GODMODE_DLL=%~dp0GodModeHost402_v4.dll"
set "ENABLE_FILE=%~dp0GodModeHost402.on"

echo === GODMODE 4.0.2 FIXED LAUNCHER ===
echo.

if not exist "%INJECTOR%" goto missing_injector
if not exist "%GODMODE_DLL%" goto missing_dll
if not exist "%ENABLE_FILE%" goto missing_enable

echo Injector and DLL files were found.
echo Attempting injection now...
echo.
"%INJECTOR%" "PenguinHotel-Win64-Shipping.exe" "%GODMODE_DLL%"
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" goto success
echo [FAILED] Injector error code: %RESULT%
echo Error 1 means the injector could not find the process.
echo Error 2 usually means an administrator privilege mismatch.
pause
exit /b %RESULT%

:success
echo [SUCCESS] GodModeHost402.dll was loaded.
echo Do not run this launcher again until the game is restarted.
pause
exit /b 0

:missing_injector
echo [FAILED] Missing godmode-simple-injector.exe
echo "%INJECTOR%"
pause
exit /b 10

:missing_dll
echo [FAILED] Missing GodModeHost402_v4.dll
echo "%GODMODE_DLL%"
pause
exit /b 11

:missing_enable
echo [FAILED] Missing GodModeHost402.on
echo "%ENABLE_FILE%"
pause
exit /b 12
