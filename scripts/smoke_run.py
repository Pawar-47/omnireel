#!/usr/bin/env python3
"""End-to-end run: planner -> scheduler -> providers -> FFmpeg -> one MP4.

Two modes, and the difference matters:

* **offline (default).** Uses the mock provider, which draws labelled colour
  cards. No API call is made and no quota is spent. This proves the *pipeline*
  and the *film*; the pixels are never AI footage.
* **live (``--live``).** Uses whatever real providers your ``.env`` configures
  and spends real quota. A 20-second film needs only two calls.

Examples::

    python scripts/smoke_run.py --seconds 20 --expect 20      # offline proof
    python scripts/smoke_run.py --seconds 20 --dry-run        # the plan, no calls
    python scripts/smoke_run.py --seconds 20 --live           # a real 20 s film
    python scripts/smoke_run.py --minutes 2 --expect 120      # full length

It exercises: storyboard planning, dynamic segment lengths, multi-provider
scheduling, quota ledgers, scene downloading, FFmpeg normalisation,
concatenation and final duration validation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from omnireel import store  # noqa: E402
from omnireel.app import Application  # noqa: E402
from omnireel.assembly import AssemblyError  # noqa: E402
from omnireel.capabilities import CapabilityRegistry, ModelSpec  # noqa: E402
from omnireel.config import Settings  # noqa: E402
from omnireel.planner import plan_storyboard  # noqa: E402
from omnireel.providers import build_providers, configured_providers  # noqa: E402
from omnireel.providers.mock import MockProvider  # noqa: E402

#: How far the finished film may drift from ``--expect`` before it is a failure.
EXPECT_TOLERANCE = 1.0

#: Prompt used when ``--prompt`` is not given: two visual beats, because a
#: 20-second film over a free ladder is usually two clips.
DEFAULT_PROMPT = (
    "A red fox trots through a snowy pine forest at dusk, stops, "
    "and looks straight at the camera"
)


def print_storyboard(storyboard, *, ladder: list[float] | None = None) -> None:
    """Show exactly what a run would generate, without spending a call."""
    if ladder:
        print("clip lengths available: " + ", ".join(f"{value:g}s" for value in ladder))
    print(
        f"title:  {storyboard.title}\n"
        f"style:  {storyboard.style}\n"
        f"plan:   {len(storyboard.shots)} scenes, "
        f"{storyboard.planned_seconds:.1f}s of {storyboard.target_seconds:g}s"
    )
    for position, shot in enumerate(storyboard.shots, start=1):
        print(f"\n  scene {position}  {shot.duration:.1f}s  {shot.beat or 'beat'}")
        print(f"    description: {shot.description}")
        print(f"    prompt:      {shot.prompt}")
        print(f"    camera:      {shot.camera}")
        print(f"    lighting:    {shot.lighting}")
        print(f"    continuity:  {shot.continuity}")


class SecondMock(MockProvider):
    id = "mock2"
    label = "Mock provider 2"


def build_app(settings: Settings, *, with_second_mock: bool) -> Application:
    application = Application.build(settings)
    if with_second_mock:
        # A second fake provider so distribution and failover are visible.
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


async def run(
    target: float,
    *,
    prompt: str,
    style: str,
    live: bool,
    expect: float | None,
    timeout: float,
    dry_run: bool,
) -> int:
    # The catalogue reads this flag when the registry is built, so it has to be
    # set before Settings and Application are created.
    if live:
        os.environ["OMNIREEL_ENABLE_MOCK"] = "0"
    else:
        os.environ["OMNIREEL_ENABLE_MOCK"] = "1"
        os.environ.setdefault("OMNIREEL_ALLOW_PAID", "false")

    settings = Settings.from_env()

    if dry_run:
        registry = CapabilityRegistry.default(settings).restrict_to(
            configured_providers(build_providers(settings))
        )
        storyboard = plan_storyboard(
            prompt,
            target_seconds=target,
            style=style,
            registry=registry,
            free_only=settings.free_only,
        )
        print_storyboard(
            storyboard, ladder=registry.durations_ladder(free_only=settings.free_only)
        )
        if not registry.durations_ladder(free_only=settings.free_only):
            print(
                "warning: no configured provider offers a free-eligible model, so the "
                "planner used its default clip lengths. Add a key to see the real plan.",
                file=sys.stderr,
            )
        print("\nNo API call made - this is the plan a real run would execute.")
        return 0

    if not live:
        settings.poll_interval_seconds = 0.0
        settings.worker_interval_seconds = 0.0
    settings.max_concurrent_generations = max(1, settings.max_concurrent_generations)
    settings.ensure_dirs()

    if not ffmpeg_available(settings):
        print(
            "FFmpeg is missing, so the film cannot be assembled.\n"
            "Install it first:  sudo apt install -y ffmpeg",
            file=sys.stderr,
        )
        return 2

    application = build_app(settings, with_second_mock=not live)

    usable = [row for row in application.scheduler.provider_status() if row["configured"]]
    if live:
        # A "live" film must never contain colour cards.
        usable = [row for row in usable if row["provider"] != "mock"]
    usable_ids = {row["provider"] for row in usable}
    eligible = [
        spec
        for spec in application.registry.enabled()
        if spec.provider in usable_ids and (not settings.free_only or spec.free_eligible)
    ]
    if not eligible:
        print(
            "No provider can take this film.\n"
            f"  configured providers: {', '.join(sorted(usable_ids)) or 'none'}\n"
            f"  free_only:            {settings.free_only}\n"
            "Add a free key to .env first - BIGMODEL_API_KEY (cogvideox-flash is free)\n"
            "or AGNES_API_KEY - or run without --live to prove the pipeline offline.",
            file=sys.stderr,
        )
        return 2

    storyboard = plan_storyboard(
        prompt,
        target_seconds=target,
        style=style,
        registry=application.registry.restrict_to(
            configured_providers(application.providers)
        ),
        free_only=settings.free_only,
    )
    project = store.create_project(
        application.db,
        storyboard=storyboard,
        aspect_ratio="16:9",
        resolution="720p",
        mode=settings.default_mode,
        free_only=settings.free_only,
        preferred_provider=None,
        status="running",
    )

    mode = "live" if live else "offline (mock clips, never AI footage)"
    print(
        f"Project {project['id']}: {len(storyboard.shots)} segments, "
        f"{storyboard.planned_seconds:.1f}s planned, mode={mode}"
    )
    print("Providers: " + ", ".join(row["label"] for row in usable) + "\n")

    deadline = time.monotonic() + (timeout if live else 600.0)
    tick = 0
    while time.monotonic() < deadline:
        await application.worker.tick()
        current = store.get_project(application.db, project["id"])
        scenes = store.scenes_for(application.db, project["id"])
        ready = sum(1 for scene in scenes if scene["status"] == "ready")
        providers = {scene["provider"] for scene in scenes if scene["provider"]}
        print(
            f"\rtick {tick:4d}  {ready:2d}/{len(scenes)} scenes  "
            f"providers={','.join(sorted(providers)) or '-'}  "
            f"status={current['status']}   ",
            end="",
            flush=True,
        )
        tick += 1
        if current["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05 if not live else max(1.0, settings.poll_interval_seconds))

    print()
    final = store.get_project(application.db, project["id"])
    if final["status"] != "completed":
        print(f"FAILED - {final['status']}", file=sys.stderr)
        if final["status_message"]:
            print(f"         {final['status_message']}", file=sys.stderr)
        if "assembly failed" in (final["status_message"] or ""):
            print(
                "Every scene was still generated and kept - only the join step failed.",
                file=sys.stderr,
            )
        return 1

    scenes = store.scenes_for(application.db, project["id"])
    mix: dict[str, int] = {}
    for scene in scenes:
        key = scene["provider"] or "unassigned"
        mix[key] = mix.get(key, 0) + 1

    duration = final["final_duration"] or 0.0
    print(f"OK - final film: {final['final_video_path']}")
    print(f"     duration: {duration:.2f}s  (target {target:g}s)")
    print(
        f"     scenes:   {len(scenes)}"
        f"  (weights: {', '.join(f'{k} x{v}' for k, v in sorted(mix.items()))})"
    )

    if expect is not None and abs(duration - expect) > EXPECT_TOLERANCE:
        print(
            f"FAILED - expected {expect:g}s +/- {EXPECT_TOLERANCE:g}s, "
            f"measured {duration:.2f}s",
            file=sys.stderr,
        )
        return 3
    if expect is not None:
        print(f"     within {EXPECT_TOLERANCE:g}s of the requested {expect:g}s")
    return 0


def ffmpeg_available(settings: Settings) -> bool:
    from shutil import which

    return which(settings.ffmpeg) is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end film run (offline or live)")
    parser.add_argument(
        "--seconds", type=float, help="film length in seconds (wins over --minutes)"
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="the creative prompt to film (default: a two-beat 20 s shot)",
    )
    parser.add_argument(
        "--style",
        default="cinematic",
        help="a style key from /api/config - cinematic, documentary, anime, nature, "
        "noir, scifi, product, explainer ... (default: cinematic)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the storyboard and the available clip lengths, then stop",
    )
    parser.add_argument("--minutes", type=float, default=None, help="film length in minutes")
    parser.add_argument(
        "--expect",
        type=float,
        default=None,
        help=f"exit non-zero unless the finished film is within {EXPECT_TOLERANCE:g}s of this",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the real providers configured in .env (spends real quota)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        help="live-mode deadline in seconds (default 1800)",
    )
    args = parser.parse_args(argv)

    if args.seconds is not None:
        target = args.seconds
    elif args.minutes is not None:
        target = args.minutes * 60.0
    else:
        target = 120.0
    if target <= 0:
        parser.error("the film length must be positive")

    if args.live and target < 20:
        print(
            "warning: OmniReel's own minimum project length is 20 s; "
            "this run plans whatever you asked for.",
            file=sys.stderr,
        )

    try:
        return asyncio.run(
            run(
                target,
                prompt=args.prompt,
                style=args.style,
                live=args.live,
                expect=args.expect,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
        )
    except AssemblyError as exc:  # pragma: no cover - defensive
        print(f"assembly error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
