"""Visual continuity helpers.

Continuity works on two levels:

1. **Prompt continuity** - every shot carries the same world bible (subject,
   wardrobe, palette, grade), so switching provider midway does not switch the
   look. That is handled by the planner.
2. **Reference frames** - when the previous clip exists, its final frame is
   extracted and offered to the next model. The scheduler only passes it to
   models that declare ``image_to_video`` or ``reference_frame`` support; for
   the rest the frame is used as documentation only.

This module owns the FFmpeg probing/extraction so assembly and the scheduler
share one implementation and one set of failure semantics.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from .config import Settings


class MediaError(RuntimeError):
    pass


class MediaTools:
    """Small async wrapper around ffmpeg/ffprobe."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- discovery -------------------------------------------------------
    def ffmpeg_path(self) -> str | None:
        return shutil.which(self.settings.ffmpeg) or shutil.which("ffmpeg")

    def ffprobe_path(self) -> str | None:
        return shutil.which(self.settings.ffprobe) or shutil.which("ffprobe")

    @property
    def available(self) -> bool:
        return self.ffmpeg_path() is not None

    # -- probing ---------------------------------------------------------
    async def _run(self, args: list[str], timeout: float | None = None) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout or self.settings.ffmpeg_timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise MediaError(f"command timed out: {args[0]}") from None
        return process.returncode or 0, stdout, stderr

    async def probe(self, path: Path) -> dict:
        """Return ``{duration, width, height, fps, has_audio, codec}``."""
        ffprobe = self.ffprobe_path()
        if ffprobe is None:
            return {
                "duration": None,
                "width": None,
                "height": None,
                "fps": None,
                "has_audio": None,
                "codec": None,
                "probe_available": False,
            }
        code, stdout, stderr = await self._run(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            timeout=60,
        )
        if code != 0:
            raise MediaError(f"ffprobe failed on {path.name}: {stderr.decode('utf-8', 'replace')[-300:]}")
        try:
            data = json.loads(stdout.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise MediaError(f"ffprobe returned invalid JSON for {path.name}") from exc

        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = None
        try:
            duration = float((data.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            for stream in streams:
                try:
                    duration = float(stream.get("duration"))
                    break
                except (TypeError, ValueError):
                    continue

        fps = None
        if video is not None:
            raw_rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
            if "/" in raw_rate:
                numerator, _, denominator = raw_rate.partition("/")
                try:
                    den = float(denominator)
                    fps = round(float(numerator) / den, 3) if den else None
                except (TypeError, ValueError):
                    fps = None
        return {
            "duration": duration,
            "width": video.get("width") if video else None,
            "height": video.get("height") if video else None,
            "fps": fps,
            "has_audio": audio is not None,
            "codec": video.get("codec_name") if video else None,
            "probe_available": True,
        }

    async def duration(self, path: Path) -> float | None:
        try:
            info = await self.probe(path)
        except MediaError:
            return None
        return info.get("duration")

    # -- frames ----------------------------------------------------------
    async def extract_frame(self, clip: Path, destination: Path, *, position: str = "last") -> Path | None:
        """Write a PNG of the first or last frame. Returns None when impossible."""
        ffmpeg = self.ffmpeg_path()
        if ffmpeg is None or not clip.exists():
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        base = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if position == "last":
            args = base + [
                "-sseof",
                "-0.25",
                "-i",
                str(clip),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(destination),
            ]
        else:
            args = base + ["-i", str(clip), "-frames:v", "1", "-q:v", "2", str(destination)]
        code, _, _ = await self._run(args, timeout=120)
        if code != 0 or not destination.exists() or destination.stat().st_size == 0:
            # Fall back to the first frame, which is always reachable.
            if position == "last":
                return await self.extract_frame(clip, destination, position="first")
            return None
        return destination

    async def has_audio_stream(self, path: Path) -> bool:
        try:
            info = await self.probe(path)
        except MediaError:
            return False
        return bool(info.get("has_audio"))


def continuity_reference_path(settings: Settings, project_id: str, index: int) -> Path:
    return Path(settings.frames_dir) / project_id / f"scene_{index:03d}_ref.png"


async def prepare_continuity_reference(
    tools: MediaTools,
    settings: Settings,
    *,
    project_id: str,
    index: int,
    previous_clip: Path | None,
) -> Path | None:
    """Extract the last frame of the previous clip, ready to condition the next one."""
    if previous_clip is None or not previous_clip.exists():
        return None
    destination = continuity_reference_path(settings, project_id, index)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    return await tools.extract_frame(previous_clip, destination, position="last")
