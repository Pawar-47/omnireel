#!/usr/bin/env bash
# Start OmniReel (API + built frontend) on http://127.0.0.1:8000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "No .venv found. Run ./scripts/install.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[warning] ffmpeg not found - generation works, final assembly will not." >&2
fi

if [ ! -f .env ]; then
  echo "[warning] no .env yet; copy .env.example and add a provider key." >&2
fi

cd backend
exec python main.py
