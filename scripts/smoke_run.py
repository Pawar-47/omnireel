#!/usr/bin/env python3
"""End-to-end smoke run with the offline mock provider.

This proves the whole chain on your machine without spending a single API call
and without pretending anything is AI-generated: the clips are plain colour
cards, the film is real.

    python scripts/smoke_run.py --minutes 2

It exercises: storyboard planning, dynamic segment lengths, multi-provider
scheduling, quota ledgers, scene downloading, FFmpeg normalisation, concatenation
and final duration validation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["OMNIREEL_ENABLE_MOCK"] = "1"
os.environ.setdefault("OMNIREEL_ALLOW_PAID", "false")

from omnireel import store  # noqa: E402
from omnireel.app import Application  # noqa: E402
from omnireel.assembly import AssemblyError  # noqa: E402
from omnireel.capabilities import CapabilityRegistry, ModelSpec  # noqa: E402
from omnireel.config import Settings  # noqa: E402
from omnireel.planner import plan_storyboard  # noqa: E402
from omnireel.providers.mock import MockProvider  # noqa: E402


class SecondMock(MockProvider):
    id = "mock2"
    label = "Mock provider 2"


def build_app(settings: Settings) -> Application:
    application = Application.build(settings)
    # Add a second fake provider so distribution and failover are visible.
    spec = ModelSpec(
        provider="mock2",
        model="mock-video",
        label="Mock 2",
        durations=[12, 10, 8, 6, 5, 4],
        free_kind="recurring",
        min_interval_seconds=0,
        quality=3,
        speed=3,
        enabled=True,
        verified_at="smoke",
    )
    application.registry.replace(spec)
    application.providers["mock2"] = SecondMock(settings)
    application.scheduler.providers = application.providers
    return application


async def run(minutes: float) -> int:
    settings = Settings.from_env()
    settings.poll_interval_seconds = 0.0
    settings.worker_interval_seconds = 0.0
    settings.max_concurrent_generations = 2
    settings.ensure_dirs()

    if not application_has_ffmpeg(settings):
        print(
            "FFmpeg is missing, so the film cannot be assembled.\n"
            "Install it first:  sudo apt install -y ffmpeg",
            file=sys.stderr,
        )
        return 2

    application = build_app(settings)
    storyboard = plan_storyboard(
        "a smoke test documentary about a lighthouse at dawn",
        target_seconds=minutes * 60,
        registry=application.registry,
        free_only=True,
    )
    project = store.create_project(
        application.db,
        storyboard=storyboard,
        aspect_ratio="16:9",
        resolution="720p",
        mode="free_max",
        free_only=True,
        preferred_provider=None,
        status="running",
    )

    print(
        f"Project {project['id']}: {len(storyboard.shots)} segments, "
        f"{storyboard.planned_seconds:.1f}s planned, mock providers only\n"
    )

    for tick in range(2000):
        await application.worker.tick()
        current = store.get_project(application.db, project["id"])
        scenes = store.scenes_for(application.db, project["id"])
        ready = sum(1 for scene in scenes if scene["status"] == "ready")
        providers = {scene["provider"] for scene in scenes if scene["provider"]}
        print(
            f"\rtick {tick:4d}  {ready:2d}/{len(scenes)} scenes  "
            f"providers={','.join(sorted(providers)) or '-'}  status={current['status']}   ",
            end="",
            flush=True,
        )
        if current["status"] in {"completed", "failed"}:
            print()
            break
        await asyncio.sleep(0.05)

    final = store.get_project(application.db, project["id"])
    print()
    if final["status"] == "completed":
        print(f"OK - final film: {final['final_video_path']}")
        print(f"     duration: {final['final_duration']:.2f}s")
        print(f"     scenes:   {len(store.scenes_for(application.db, project['id']))}")
        return 0

    print(f"FAILED - {final['status_message']}", file=sys.stderr)
    if final["status_message"] and "assembly failed" in final["status_message"]:
        print(
            "Every scene was still generated and kept - only the join step failed.",
            file=sys.stderr,
        )
    return 1


def application_has_ffmpeg(settings: Settings) -> bool:
    from shutil import which

    return which(settings.ffmpeg) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline end-to-end smoke run")
    parser.add_argument("--minutes", type=float, default=2.0, help="film length in minutes")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.minutes))
    except AssemblyError as exc:  # pragma: no cover - defensive
        print(f"assembly error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
