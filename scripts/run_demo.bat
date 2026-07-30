@echo off
setlocal
cd /d "%~dp0\.."

if "%BOND_DEMO_HOST%"=="" set "BOND_DEMO_HOST=127.0.0.1"
if "%BOND_DEMO_PORT%"=="" set "BOND_DEMO_PORT=8765"
set "FLASK_RUN_HOST=%BOND_DEMO_HOST%"
set "PORT=%BOND_DEMO_PORT%"
if "%BOND_DATA_MODE%"=="" set "BOND_DATA_MODE=auto"
if "%SECRET_KEY%"=="" set "SECRET_KEY=local-dev"
if "%FLASK_ENV%"=="" set "FLASK_ENV=production"

REM Keep the default demo deterministic and secret-free. Set BOND_DEMO_WITH_LLM=1
REM if you explicitly want to pass an already-set OPENAI_* environment through.
if not "%BOND_DEMO_WITH_LLM%"=="1" (
  set "OPENAI_API_KEY="
  set "OPENAI_BASE_URL="
  set "OPENAI_MODEL="
  set "OPENAI_MODEL_FALLBACKS="
  set "OPENAI_API_STYLE="
  set "OPENAI_TIMEOUT_SECONDS="
)

set "HTTP_PROXY="
set "HTTPS_PROXY="
set "http_proxy="
set "https_proxy="
set "ALL_PROXY="
set "all_proxy="

if "%PYTHON_BIN%"=="" (
  if exist ".venv\Scripts\python.exe" (
    set "PYTHON_BIN=.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

echo BondLens demo starting: http://%FLASK_RUN_HOST%:%PORT%/agent
echo Data mode: %BOND_DATA_MODE%; LLM default: disabled/deterministic
"%PYTHON_BIN%" app.py
