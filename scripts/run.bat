@echo off
REM ======================================================================
REM  Document Reader Skill - single entry point (run.bat, Windows)
REM  Usage:
REM    scripts\run.bat <file-path> [--file-type-hint pdf] [--password xxx]
REM                    [--pages 1-50] [--ocr] [--mineru-mode flash|precision]
REM                    [--no-prefer-mineru-sdk] [--enable-ocr-plugin]
REM                    [--output-format json|text|markdown] [--chars 2000]
REM
REM  This script does three things:
REM    1) Sanity-check the environment (uv present, Python 3.12, deps installed)
REM    2) Bootstrap if anything is missing (download uv + Python 3.12 + uv sync)
REM    3) Forward every argument to src\main.py
REM  The SKILL root is one level above this scripts\ directory.
REM ======================================================================
setlocal EnableExtensions EnableDelayedExpansion

REM --- resolve SCRIPT_DIR (this scripts\ dir) then SKILL_DIR = one level up ---
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%\..") do set "SKILL_DIR=%%~fI"
cd /d "%SKILL_DIR%"

REM --- 1. Locate uv.exe: PATH first, then skill-local .local\bin ---
set "LOCAL_UV_DIR=%SKILL_DIR%\.local\bin"
set "UV_EXE="
where uv >nul 2>nul
if %ERRORLEVEL%==0 (
    for /f "delims=" %%i in ('where uv 2^>nul') do set "UV_EXE=%%i" & goto :uv_found
)
:uv_found
if "%UV_EXE%"=="" if exist "%LOCAL_UV_DIR%\uv.exe" set "UV_EXE=%LOCAL_UV_DIR%\uv.exe"

REM --- 2. Bootstrap uv if not present ---
if "%UV_EXE%"=="" (
    echo [run] uv not found - bootstrapping uv (single-file binary)...
    if not exist "%LOCAL_UV_DIR%" mkdir "%LOCAL_UV_DIR%" 2>nul

    REM --- 2a. Try Astral's official install.ps1 ---
    where pwsh >nul 2>nul
    if %ERRORLEVEL%==0 (
        pwsh -NoProfile -ExecutionPolicy Bypass -Command `
            "irm https://astral.sh/uv/install.ps1 | iex; if (Test-Path $env:USERPROFILE\.local\bin\uv.exe) { Copy-Item $env:USERPROFILE\.local\bin\uv.exe '%LOCAL_UV_DIR%\uv.exe' -Force }" >nul 2>nul
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command `
            "Set-ExecutionPolicy Bypass -Scope Process -Force; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex; if (Test-Path $env:USERPROFILE\.local\bin\uv.exe) { Copy-Item $env:USERPROFILE\.local\bin\uv.exe '%LOCAL_UV_DIR%\uv.exe' -Force }" >nul 2>nul
    )

    if exist "%LOCAL_UV_DIR%\uv.exe" set "UV_EXE=%LOCAL_UV_DIR%\uv.exe"

    REM --- 2b. If official installer failed, download uv zip directly from GitHub Releases ---
    if "%UV_EXE%"=="" (
        echo [run] Official install failed - downloading uv binary directly from GitHub...
        for /f "delims=" %%a in ('powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).OSArchitecture" 2^>nul') do set "ARCH=%%a"
        set "UV_TAG=latest"
        echo !ARCH! | findstr /i "ARM64 aarch64" >nul && (set "UV_ARTIFACT=uv-aarch64-pc-windows-msvc.zip") || (set "UV_ARTIFACT=uv-x86_64-pc-windows-msvc.zip")
        set "UV_URL=https://github.com/astral-sh/uv/releases/%UV_TAG%/download/!UV_ARTIFACT!"
        set "UV_ZIP=%TEMP%\uv-windows.zip"
        powershell -NoProfile -ExecutionPolicy Bypass -Command `
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('!UV_URL!', '%UV_ZIP%'); Expand-Archive -Path '%UV_ZIP%' -DestinationPath '%LOCAL_UV_DIR%' -Force; Remove-Item '%UV_ZIP%' -Force"
        if exist "%LOCAL_UV_DIR%\uv.exe" set "UV_EXE=%LOCAL_UV_DIR%\uv.exe"
    )

    REM --- 2c. Prepend local dir to PATH so subsequent calls find uv ---
    if "%UV_EXE%"=="" (
        echo [run] FAILED to auto-download uv. Please install manually: https://docs.astral.sh/uv/getting-started/installation/
        exit /b 10
    )
    set "PATH=%LOCAL_UV_DIR%;%PATH%"
)

REM --- 3. Ensure CPython 3.12 (per .python-version) + all deps are installed ---
echo [run] Ensuring Python 3.12 + dependencies installed...
"%UV_EXE%" sync --all-extras
if %ERRORLEVEL% neq 0 (
    echo [run] FAILED: uv sync exited with code %ERRORLEVEL%. Check network or run: "%UV_EXE%" sync --all-extras --reinstall
    exit /b %ERRORLEVEL%
)

REM --- 4. Real work: forward all arguments to src\main.py ---
"%UV_EXE%" run python "%SKILL_DIR%\src\main.py" %*
exit /b %ERRORLEVEL%
