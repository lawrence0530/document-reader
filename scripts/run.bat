@echo off
REM ======================================================================
REM  Document Reader Skill - single entry point (run.bat, Windows)
REM  Usage:
REM    scripts\run.bat <file-path> [--file-type-hint pdf] [--password xxx]
REM                    [--pages 1-50] [--ocr] [--mineru-mode flash|precision]
REM                    [--no-prefer-mineru-sdk] [--enable-ocr-plugin]
REM                    [--output-format json|text|markdown] [--chars 2000]
REM ======================================================================
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%\..") do set "SKILL_DIR=%%~fI"
cd /d "%SKILL_DIR%"

REM --- 1. Locate uv.exe ---
set "LOCAL_UV_DIR=%SKILL_DIR%\.local\bin"
set "UV_EXE="
where uv >nul 2>nul
if %ERRORLEVEL% equ 0 (
    for /f "delims=" %%i in ('where uv 2^>nul') do set "UV_EXE=%%i" & goto :found
)
:found
if "%UV_EXE%"=="" if exist "%LOCAL_UV_DIR%\uv.exe" set "UV_EXE=%LOCAL_UV_DIR%\uv.exe"

if "%UV_EXE%"=="" (
    echo [run] uv not found. Please install uv from https://docs.astral.sh/uv/
    echo or place it at "%LOCAL_UV_DIR%\uv.exe".
    exit /b 10
)

REM --- 2. Ensure Python 3.12 and deps are installed ---
echo [run] Ensuring Python 3.12 + dependencies installed...
"%UV_EXE%" sync --all-extras
if %ERRORLEVEL% neq 0 (
    echo [run] FAILED: uv sync exited with code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)

REM --- 3. Forward arguments to main.py (module mode, avoids script-directory sys.path trap) ---
"%UV_EXE%" run python -m document_reader.main %*
exit /b %ERRORLEVEL%
