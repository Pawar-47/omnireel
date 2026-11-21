#!/usr/bin/env bash
# OmniReel installer for Linux Mint / Ubuntu style systems.
# Installs Python deps, builds the frontend, checks FFmpeg and seeds .env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warning] %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m[error] %s\033[0m\n' "$*"; exit 1; }

say "Checking prerequisites"
command -v python3 >/dev/null 2>&1 || fail "python3 is required (sudo apt install python3 python3-venv)"
PY_OK=$(python3 - <<'PY'
import sys
print(1 if sys.version_info >= (3, 10) else 0)
PY
)
[ "$PY_OK" = "1" ] || fail "Python 3.10+ is required (found $(python3 -V))"

if ! python3 -c "import venv" >/dev/null 2>&1; then
  fail "The python3-venv package is missing: sudo apt install python3-venv"
fi

if command -v ffmpeg >/dev/null 2>&1; then
  say "FFmpeg found: $(ffmpeg -version | head -n1)"
else
  warn "FFmpeg is not installed. Final assembly will fail until you run:"
  warn "  sudo apt update && sudo apt install -y ffmpeg"
fi

say "Creating the Python virtual environment"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null

say "Installing backend dependencies"
pip install -r backend/requirements.txt

say "Building the frontend"
if command -v npm >/dev/null 2>&1; then
  ( cd frontend && npm install --no-audit --no-fund && npm run build )
else
  warn "npm was not found, so the pre-built UI will not exist."
  warn "Install Node 18+ (sudo apt install nodejs npm) and re-run, or use ./scripts/dev.sh"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env - add AGNES_API_KEY before generating anything"
else
  say ".env already exists, leaving it alone"
fi

if [ ! -f config/providers.json ]; then
  cp config/providers.json.example config/providers.json
  say "Created config/providers.json from the example (all overrides disabled)"
fi

say "Install complete"
cat <<'TXT'
Next steps:

  1. Edit .env and set AGNES_API_KEY (free key: https://platform.agnes-ai.com)
  2. Start the app:      ./scripts/run.sh
  3. Open the studio:    http://127.0.0.1:8000
  4. Prove the machine:  .venv/bin/python scripts/smoke_run.py --minutes 2

TXT
