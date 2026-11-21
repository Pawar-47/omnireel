"""Uvicorn entry point.

Run from the repository root:

    cd backend && python main.py

or via the helper script: ``./scripts/run.sh``
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omnireel.api import create_app  # noqa: E402
from omnireel.config import Settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

settings = Settings.from_env()
app = create_app(settings)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
