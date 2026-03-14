@echo off
:: PDF Compress — double-click to open, or drag PDFs onto this file.
:: Uses pythonw (no console window). Falls back to python if needed.

set "SCRIPT=%~dp0app.py"

:: Try pythonw first (no console window)
where pythonw >nul 2>&1
if %errorlevel% == 0 (
    start "" pythonw "%SCRIPT%" %*
    exit /b
)

:: Try py launcher with windowless flag
where py >nul 2>&1
if %errorlevel% == 0 (
    start "" py -w "%SCRIPT%" %*
    exit /b
)

:: Fallback to python (console stays open on error)
where python >nul 2>&1
if %errorlevel% == 0 (
    python "%SCRIPT%" %*
    if %errorlevel% neq 0 pause
    exit /b
)

echo.
echo  Python not found.
echo  Install from https://www.python.org/downloads/
echo  Tick "Add python.exe to PATH" during install.
echo.
pause
