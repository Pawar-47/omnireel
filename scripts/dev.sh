#!/usr/bin/env bash
# Development mode: API with reload on :8000 plus the Vite dev server on :5173.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "No .venv found. Run ./scripts/install.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

cleanup() {
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

( cd backend && uvicorn main:app --reload --port 8000 ) &
API_PID=$!

( cd frontend && npm run dev -- --port 5173 ) &
WEB_PID=$!

echo "API: http://127.0.0.1:8000  UI: http://127.0.0.1:5173"
wait
