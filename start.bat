@echo off
REM ============================================================
REM  Launches the Upwork Job Review tool.
REM  Double-click this file. Nothing to remember.
REM
REM  - cd's to this folder (the project root, where pyproject.toml lives)
REM  - prefers `uv run`, which syncs deps from uv.lock and starts the app
REM  - falls back to the existing .venv if uv isn't on PATH
REM  - the ANTHROPIC_API_KEY is already set in your user environment
REM ============================================================

cd /d "%~dp0"

where uv >nul 2>nul
if %errorlevel%==0 (
    echo Starting via uv ...
    uv run python src\main.py
) else if exist ".venv\Scripts\python.exe" (
    echo uv not found on PATH; starting via the existing .venv ...
    ".venv\Scripts\python.exe" src\main.py
) else (
    echo.
    echo Could not find 'uv' on PATH and no .venv exists yet.
    echo Install uv, then this launcher will work:
    echo     winget install --id=astral-sh.uv
    echo     uv sync
    echo.
    pause
    exit /b 1
)

REM If the app exited with an error, keep the window open so the message
REM (e.g. a missing API key or a Claude error) stays readable.
if errorlevel 1 (
    echo.
    echo The app exited with an error. See the message above.
    pause
)
