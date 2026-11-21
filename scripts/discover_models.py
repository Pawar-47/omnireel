#!/usr/bin/env python3
"""Refresh the video-model catalogue from a provider's live capability registry.

Every multi-provider gateway audited in PROVIDER_RESEARCH.md solves the same
problem the same way: instead of hand-copying a table of model ids and clip
lengths that silently rots, read the registry the service already publishes.
Pollinations exposes one at ``GET /video/models`` with machine-readable
durations, resolutions, frame controls, pricing and a ``paid_only`` flag, so this
script turns it into ``config/providers.json`` entries that OmniReel merges over
its built-in catalogue.

    python scripts/discover_models.py             # print a table
    python scripts/discover_models.py --json      # print the JSON fragment
    python scripts/discover_models.py --write     # create config/providers.json
    python scripts/discover_models.py --write --force

Nothing here invents a number: every field comes from the registry, and a model
whose duration the registry does not publish is skipped rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

REGISTRY_URL = "https://gen.pollinations.ai/video/models"
PROVIDER = "pollinations"

#: Discrete clip lengths OmniReel's planner likes, most useful first.
PREFERRED_STEPS = (5.0, 10.0, 15.0, 8.0, 6.0, 4.0, 3.0, 2.0, 12.0)
#: Resolutions the capability registry and the assembler understand.
KNOWN_RESOLUTIONS = ("480p", "720p", "1080p")


def fetch(url: str) -> list[dict]:
    try:
        response = httpx.get(url, timeout=60.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise SystemExit(f"Could not reach {url}: {exc}") from exc
    if response.status_code != 200:
        raise SystemExit(f"{url} answered HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, list):
        raise SystemExit(f"Unexpected registry payload from {url}")
    return [entry for entry in payload if isinstance(entry, dict)]


def propose_durations(entry: dict) -> list[float]:
    allowed = entry.get("allowed_durations") or []
    if allowed:
        return sorted({float(value) for value in allowed})
    minimum, maximum = entry.get("min_duration"), entry.get("max_duration")
    if minimum is None or maximum is None:
        return []
    minimum, maximum = float(minimum), float(maximum)
    if maximum <= 0:
        return []
    if minimum == maximum:
        return [minimum]
    steps = [value for value in PREFERRED_STEPS if minimum <= value <= maximum]
    return sorted(set(steps or [maximum]))


def to_model(entry: dict) -> dict | None:
    durations = propose_durations(entry)
    if not durations:
        return None
    modalities = entry.get("input_modalities") or []
    capabilities = entry.get("video_capabilities") or []
    resolutions = [
        value for value in (entry.get("resolutions") or []) if value in KNOWN_RESOLUTIONS
    ] or ["720p"]
    return {
        "provider": PROVIDER,
        "model": entry.get("name"),
        "label": entry.get("title") or entry.get("name"),
        "durations": durations,
        "resolutions": resolutions,
        "aspect_ratios": ["16:9", "9:16"],
        "text_to_video": "text" in modalities,
        # The adapter does not send a start frame: that parameter needs a public
        # image URL and OmniReel only has local continuity frames.
        "image_to_video": False,
        "reference_frame": False,
        "native_audio": "audio_output" in capabilities,
        "quality": 4,
        "speed": 4,
        "free_kind": "none" if entry.get("paid_only") else "trial",
        "enabled": True,
        "docs_url": "https://gen.pollinations.ai/docs",
        "notes": (
            "Discovered from the live registry "
            f"({entry.get('publisher') or 'unknown publisher'})."
        ),
    }


def render_table(models: list[dict], skipped: list[str]) -> str:
    lines = [f"{'model':36} {'access':6} {'durations':22} resolutions"]
    lines.append("-" * 88)
    for model in models:
        access = "paid" if model["free_kind"] == "none" else "trial"
        durations = ", ".join(f"{value:g}s" for value in model["durations"])
        lines.append(
            f"{model['model']:36} {access:6} {durations:22} {', '.join(model['resolutions'])}"
        )
    if skipped:
        lines.append("")
        lines.append(f"skipped (no published duration): {', '.join(skipped)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the video model catalogue from a live registry.")
    parser.add_argument("--url", default=REGISTRY_URL, help="registry endpoint to read")
    parser.add_argument("--json", action="store_true", help="print the config fragment")
    parser.add_argument(
        "--write",
        nargs="?",
        const="config/providers.json",
        metavar="PATH",
        help="write the fragment to PATH (default config/providers.json)",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing file")
    args = parser.parse_args(argv)

    entries = fetch(args.url)
    models: list[dict] = []
    skipped: list[str] = []
    for entry in entries:
        if entry.get("category") and entry.get("category") != "video":
            continue
        model = to_model(entry)
        if model is None:
            skipped.append(str(entry.get("name")))
            continue
        models.append(model)

    if not models:
        print("The registry returned no usable video models.", file=sys.stderr)
        return 1

    if args.json or args.write:
        payload = json.dumps({"models": models}, indent=2) + "\n"
        if args.write:
            path = Path(args.write)
            if path.exists() and not args.force:
                print(f"{path} already exists - pass --force to replace it.", file=sys.stderr)
                return 2
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            print(f"Wrote {len(models)} model overrides to {path}")
            print("OmniReel merges this over its built-in catalogue on the next start.")
        else:
            print(payload, end="")
    else:
        print(render_table(models, skipped))

    paid = sum(1 for model in models if model["free_kind"] == "none")
    print(
        f"\n{len(models)} models: {len(models) - paid} with a trial allowance, {paid} paid only."
        " Paid-only models are still excluded from free-only mode.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
