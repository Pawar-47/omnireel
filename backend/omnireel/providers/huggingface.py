"""Hugging Face Inference Providers text-to-video adapter.

Unlike Agnes and Replicate this endpoint answers with video bytes rather than a
job id, so ``submit`` performs the whole generation and parks the result in a
local ``file://`` URL that ``poll`` reports as finished. The scheduler contract
stays exactly the same.

Inference Providers is pay-as-you-go (monthly credits only on PRO/Team plans),
so the catalogue ships this model disabled and outside free-only mode.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import httpx

from .base import (
    PollResult,
    ProviderError,
    SubmitResult,
    VideoProvider,
    VideoRequest,
    classify_http,
)

DEFAULT_BASE = "https://router.huggingface.co/hf-inference/models"


class HuggingFaceProvider(VideoProvider):
    id = "huggingface"
    label = "Hugging Face Inference Providers"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.base_url = os.environ.get("HF_INFERENCE_BASE", DEFAULT_BASE).rstrip("/")
        self._pending: dict[str, Path] = {}

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        headers = {
            "Authorization": f"Bearer {self.key(key_index)}",
            "Accept": "video/mp4",
            "Content-Type": "application/json",
        }
        try:
            response = await client.post(
                f"{self.base_url}/{model}",
                json={"inputs": request.prompt},
                headers=headers,
                timeout=max(self.settings.http_timeout_seconds, 600.0),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"huggingface: generation failed: {exc}", kind="transient") from exc

        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("video/") and not content_type == "application/octet-stream":
            raise ProviderError(
                f"huggingface: unexpected response type '{content_type or 'unknown'}'",
                kind="unsupported",
            )

        target = Path(self.settings.data_dir) / "tmp" / f"hf-{uuid.uuid4().hex}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        remote_id = target.stem
        self._pending[remote_id] = target
        return SubmitResult(state="succeeded", remote_id=remote_id, video_url=target.as_uri())

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        target = self._pending.get(remote_id)
        if target is None or not target.exists():
            return PollResult(state="not_found")
        return PollResult(state="succeeded", video_url=target.as_uri(), progress=1.0)

    async def download(self, url: str, destination: Path) -> Path:
        if url.startswith("file://"):
            source = Path(url[len("file://") :])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return destination
        return await super().download(url, destination)
