@echo off
:: ============================================================
:: compress_pdf.bat — Drag and drop PDFs onto this file
:: to compress them. Edit QUALITY below to change default.
::
:: Quality levels:
::   1 = Minimum  (smallest file, rough images)
::   2 = Low
::   3 = Medium   (default, good for lecture notes)
::   4 = High
::   5 = Maximum  (lightest compression, best quality)
:: ============================================================

set QUALITY=3
set SCRIPT=%~dp0compress_pdf.py

if "%~1"=="" (
    echo.
    echo  Drag and drop one or more PDF files onto this script to compress them.
    echo  Current quality level: %QUALITY%/5
    echo.
    pause
    exit /b
)

python "%SCRIPT%" %* -q %QUALITY%
