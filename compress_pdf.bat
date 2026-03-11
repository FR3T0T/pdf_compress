@echo off
set SCRIPT=%~dp0pdf_compress_gui.py

:: Try "python" first, then "py" launcher
python --version >nul 2>&1
if %errorlevel% == 0 (
    python "%SCRIPT%" %*
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Script failed. See message above.
        pause
    )
    exit /b
)

py --version >nul 2>&1
if %errorlevel% == 0 (
    py "%SCRIPT%" %*
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Script failed. See message above.
        pause
    )
    exit /b
)

echo.
echo Python not found. Please install it from https://www.python.org/downloads/
echo Make sure to tick "Add python.exe to PATH" during install.
echo.
pause
