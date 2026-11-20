@echo off
setlocal
title TermiCode AI Demo Launcher

echo ===============================================================================
echo                           TermiCode AI Demo Launcher
echo ===============================================================================
echo.
echo   [1] Launch Web Assistant (Browser GUI - http://127.0.0.1:8765)
echo   [2] Launch Interactive Terminal Chat (Rich CLI)
echo   [3] Run Automated Test Suite (397 passing tests)
echo   [0] Exit
echo.
echo ===============================================================================
set /p choice="Select an option [1-3, or 0 to exit]: "

if "%choice%"=="1" goto launch_web
if "%choice%"=="2" goto launch_cli
if "%choice%"=="3" goto run_tests
if "%choice%"=="0" goto end
goto launch_web

:launch_web
echo.
echo [*] Launching TermiCode Web Companion on port 8765...
powershell -NoProfile -ExecutionPolicy Bypass -Command ".\.venv\Scripts\python.exe -m termicode.cli web --open-browser"
goto end

:launch_cli
echo.
echo [*] Launching TermiCode Terminal Chat...
powershell -NoProfile -ExecutionPolicy Bypass -Command ".\.venv\Scripts\python.exe -m termicode.cli chat"
goto end

:run_tests
echo.
echo [*] Running TermiCode test suite...
.\.venv\Scripts\pytest.exe -q
pause
goto end

:end
