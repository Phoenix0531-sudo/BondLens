#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export FLASK_RUN_HOST="${BOND_DEMO_HOST:-127.0.0.1}"
export PORT="${BOND_DEMO_PORT:-8765}"
export BOND_DATA_MODE="${BOND_DATA_MODE:-auto}"
export SECRET_KEY="${SECRET_KEY:-local-dev}"
export FLASK_ENV="${FLASK_ENV:-production}"

# Keep the default demo deterministic and secret-free. Set BOND_DEMO_WITH_LLM=1
# if you explicitly want to pass an already-exported OPENAI_* environment through.
if [[ "${BOND_DEMO_WITH_LLM:-0}" != "1" ]]; then
  unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL OPENAI_MODEL_FALLBACKS OPENAI_API_STYLE OPENAI_TIMEOUT_SECONDS
fi

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x ".venv/Scripts/python.exe" ]]; then
    PYTHON_BIN=".venv/Scripts/python.exe"
  elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python"
  fi
fi

echo "BondLens demo starting: http://${FLASK_RUN_HOST}:${PORT}/agent"
echo "Data mode: ${BOND_DATA_MODE}; LLM: $([[ "${BOND_DEMO_WITH_LLM:-0}" == "1" ]] && echo 'env passthrough' || echo 'disabled/deterministic')"
exec "$PYTHON_BIN" app.py
