#!/usr/bin/env bash
# 生产启动（Linux 服务器）
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

mkdir -p data/uploads
export $(grep -v '^#' .env | grep -E '^[A-Z0-9_]+=' | xargs -d '\n' -r) 2>/dev/null || true

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"

echo "Starting coldchain backend on ${HOST}:${PORT} (workers=${WORKERS})"
echo "DATABASE_PATH=${DATABASE_PATH:-data/device_data.db}"
exec uvicorn main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
