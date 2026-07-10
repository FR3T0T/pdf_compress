@echo off
REM Build the PDF Toolkit Windows installer:
REM   1) PyInstaller one-dir build  ->  dist\PDFToolkit\
REM   2) Inno Setup compile         ->  dist\PDFToolkit-Setup-<version>.exe
REM
REM Requires Inno Setup 6.3+ (free): https://jrsoftware.org/isinfo.php
REM iscc.exe is looked up on PATH first, then at the default install
REM location "C:\Program Files (x86)\Inno Setup 6\ISCC.exe".

echo.
echo  Building PDF Toolkit installer...
echo  ---------------------------------
echo.

python -m PyInstaller pdf_toolkit.spec --noconfirm
if %ERRORLEVEL% neq 0 (
    echo.
    echo  PYINSTALLER BUILD FAILED
    echo.
    pause
    exit /b 1
)

set "ISCC=iscc"
where iscc >nul 2>nul
if %ERRORLEVEL% neq 0 (
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
        set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    ) else (
        echo.
        echo  ISCC.EXE NOT FOUND
        echo  Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
        echo  or add iscc.exe to PATH.
        echo.
        pause
        exit /b 1
    )
)

"%ISCC%" installer.iss
if %ERRORLEVEL% neq 0 (
    echo.
    echo  INSTALLER COMPILE FAILED
    echo.
    pause
    exit /b 1
)

echo.
echo  Installer ready in dist\
echo.
pause
