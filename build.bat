@echo off
REM Build PDF Toolkit as a standalone Windows application.
REM Requires: pip install pyinstaller
REM Output:   dist\PDFToolkit\PDFToolkit.exe

echo.
echo  Building PDF Toolkit...
echo  --------------------------
echo.

python -m PyInstaller pdf_toolkit.spec --noconfirm

if %ERRORLEVEL% neq 0 (
    echo.
    echo  BUILD FAILED
    echo.
    pause
    exit /b 1
)

echo.
echo  Build complete: dist\PDFToolkit\PDFToolkit.exe
echo.
pause
