"""Offline mock provider.

Used by the automated tests and by ``scripts/smoke_run.py``. It produces a
labelled colour clip with FFmpeg when FFmpeg is available, otherwise a small
placeholder file, so the whole orchestration path can be exercised without
spending anybody's quota. It is never presented as AI footage.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from .base import PollResult, ProviderError, SubmitResult, VideoRequest, VideoProvider

FPS = 24


class MockProvider(VideoProvider):
    id = "mock"
    label = "Local mock (testing only)"
    requires_key = False

    def __init__(self, settings) -> None:  # noqa: ANN001 - Settings, kept loose for tests
        super().__init__(settings)
        self._jobs: dict[str, dict] = {}
        self.submit_calls: list[dict] = []
        #: Tests may set this to raise for the next N submissions.
        self.fail_next: list[str] = []

    # -- API -------------------------------------------------------------
    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        self.submit_calls.append(
            {
                "model": model,
                "prompt": request.prompt,
                "duration": request.duration,
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.resolution,
                "reference_image": str(request.reference_image) if request.reference_image else None,
                "key_index": key_index,
            }
        )
        if self.fail_next:
            kind = self.fail_next.pop(0)
            raise ProviderError(f"mock failure: {kind}", kind=kind)

        remote_id = f"mock-{uuid.uuid4().hex[:12]}"
        self._jobs[remote_id] = {
            "prompt": request.prompt,
            "duration": request.duration,
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "polls": 0,
            "status": "processing",
        }
        return SubmitResult(state="pending", remote_id=remote_id)

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        job = self._jobs.get(remote_id)
        if job is None:
            return PollResult(state="not_found", error="unknown mock job")
        job["polls"] += 1
        # Complete on the second poll so tests exercise the polling branch.
        if job["polls"] < 2:
            return PollResult(state="pending", progress=0.5)
        try:
            url = await self._render(remote_id, job)
        except ProviderError as exc:
            job["status"] = "failed"
            return PollResult(state="failed", error=str(exc), error_kind=exc.kind)
        job["status"] = "succeeded"
        return PollResult(state="succeeded", video_url=url, progress=1.0)

    # -- rendering -------------------------------------------------------
    async def _render(self, remote_id: str, job: dict) -> str:
        settings = self.settings
        target = Path(settings.clips_dir) / "_mock" / f"{remote_id}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg = shutil.which(settings.ffmpeg) or shutil.which("ffmpeg")
        if ffmpeg is None:
            # No FFmpeg: still hand back a file so orchestration can be tested.
            target.write_bytes(b"MOCK-CLIP" + remote_id.encode() + b"\n")
            return target.as_uri()

        duration = max(1.0, float(job["duration"]))
        resolution = str(job.get("resolution", "720p"))
        width, height = {
            "1080p": (1920, 1080),
            "720p": (1280, 720),
            "480p": (854, 480),
            "360p": (640, 360),
        }.get(resolution, (1280, 720))
        if job.get("aspect_ratio") == "9:16":
            width, height = height, width
        elif job.get("aspect_ratio") == "1:1":
            side = min(width, height)
            width = height = side

        hue = (abs(hash(remote_id)) % 360)
        args = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{hue:06x}:s={width}x{height}:r={FPS}:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-t",
            str(duration),
            str(target),
        ]
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise ProviderError(
                f"mock renderer failed: {stderr.decode('utf-8', 'replace')[-300:]}",
                kind="transient",
            )
        return target.as_uri()

    async def download(self, url: str, destination: Path) -> Path:
        """The mock writes locally, so hand the file over instead of fetching it."""
        if url.startswith("file://"):
            source = Path(url[len("file://") :])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return destination
        return await super().download(url, destination)
