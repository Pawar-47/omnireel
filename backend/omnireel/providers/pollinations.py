"""Pollinations video adapter - one key, one API, ~28 video models.

Contract verified against the live service (see PROVIDER_RESEARCH.md):

* ``GET https://gen.pollinations.ai/video/{prompt}?model=…&duration=…`` streams
  MP4 bytes, or answers ``202 Accepted`` with a job id when the request carries
  ``Prefer: respond-async``;
* ``GET https://gen.pollinations.ai/v1/jobs/{id}`` reports a ``status`` and, once
  finished, a ``result_url``;
* auth is ``Authorization: Bearer sk_…``;
* ``GET https://gen.pollinations.ai/video/models`` is a machine-readable
  capability registry - min/max duration, allowed duration steps, resolutions,
  frame controls, price and a ``paid_only`` flag. ``scripts/discover_models.py``
  reads it so the catalogue can be refreshed without guessing.

Every video model in that registry is currently flagged ``paid_only``, so this is
a *paid* aggregator and free-only mode never selects it. Its value is breadth:
Veo, Kling, Seedance, Wan, LTX, MiniMax and Grok behind a single key, which makes
it the practical way to finish a project whose free sources are all resting.

Image conditioning is not wired: the start-frame parameter takes a public URL and
this controller only has local continuity frames. The catalogue says so.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

from .base import (
    PollResult,
    ProviderError,
    SubmitResult,
    VideoProvider,
    VideoRequest,
    classify_http,
)

DEFAULT_BASE = "https://gen.pollinations.ai"


class PollinationsProvider(VideoProvider):
    id = "pollinations"
    label = "Pollinations"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.base_url = os.environ.get("POLLINATIONS_BASE_URL", DEFAULT_BASE).rstrip("/")
        #: Clips that arrived inline as bytes rather than as a remote URL.
        self._pending: dict[str, Path] = {}

    def _headers(self, key_index: int, *, async_mode: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.key(key_index)}"}
        if async_mode:
            headers["Prefer"] = "respond-async"
        return headers

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        params: dict[str, object] = {"model": model}
        duration = int(round(float(request.duration or 5.0)))
        if duration > 0:
            params["duration"] = duration
        try:
            response = await client.get(
                f"{self.base_url}/video/{quote(request.prompt, safe='')}",
                params=params,
                headers=self._headers(key_index),
                timeout=max(self.settings.http_timeout_seconds, 300.0),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"pollinations: submit failed: {exc}", kind="transient") from exc

        if response.status_code == 202:
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError(
                    "pollinations: async submit returned non-JSON", kind="transient"
                ) from exc
            if not isinstance(data, dict):
                raise ProviderError(
                    "pollinations: async submit returned non-JSON", kind="transient"
                )
            job_id = data.get("id") or data.get("job_id")
            if not job_id:
                raise ProviderError("pollinations: async submit returned no job id", kind="transient")
            return SubmitResult(state="pending", remote_id=str(job_id), raw=data)

        if response.status_code == 200:
            content_type = (response.headers.get("content-type") or "").lower()
            if content_type.startswith("video/"):
                return self._store_inline(response.content)
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError(
                    "pollinations: unexpected response body", kind="unsupported"
                ) from exc
            if not isinstance(data, dict):
                raise ProviderError(
                    "pollinations: unexpected response body", kind="unsupported"
                )
            url = data.get("result_url") or data.get("url")
            if isinstance(url, str) and url.startswith("http"):
                return SubmitResult(state="succeeded", remote_id=url, video_url=url, raw=data)
            job_id = data.get("id") or data.get("job_id")
            if job_id:
                return SubmitResult(state="pending", remote_id=str(job_id), raw=data)
            raise ProviderError(
                "pollinations: response carried neither media nor a job id", kind="unsupported"
            )

        raise classify_http(response.status_code, response.text)

    def _store_inline(self, payload: bytes) -> SubmitResult:
        if len(payload) < 1024:
            raise ProviderError("pollinations: returned a clip that is too small", kind="transient")
        target = Path(self.settings.data_dir) / "tmp" / f"pollinations-{uuid.uuid4().hex}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        remote_id = target.stem
        self._pending[remote_id] = target
        return SubmitResult(state="succeeded", remote_id=remote_id, video_url=target.as_uri())

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        target = self._pending.get(remote_id)
        if target is not None:
            if target.exists():
                return PollResult(state="succeeded", video_url=target.as_uri(), progress=1.0)
            return PollResult(state="not_found")

        client = await self.client()
        try:
            response = await client.get(
                f"{self.base_url}/v1/jobs/{remote_id}", headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"pollinations: poll failed: {exc}", kind="transient") from exc

        if response.status_code == 404:
            return PollResult(state="not_found")
        if response.status_code == 409:
            # The result endpoint answers 409 while the job is still running.
            return PollResult(state="pending")
        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError:
            return PollResult(state="pending")

        if not isinstance(data, dict):
            return PollResult(state="pending")

        status = str(data.get("status") or "").lower()
        if status in {"succeeded", "success", "completed", "complete"}:
            url = data.get("result_url") or data.get("url")
            if not isinstance(url, str) or not url.startswith("http"):
                return PollResult(
                    state="failed",
                    error="pollinations job finished without a result url",
                    error_kind="transient",
                    raw=data,
                )
            return PollResult(state="succeeded", video_url=url, progress=1.0, raw=data)
        if status in {"failed", "error", "cancelled", "canceled"}:
            return PollResult(
                state="failed",
                error=str(data.get("error") or "pollinations job failed"),
                error_kind="transient",
                raw=data,
            )
        return PollResult(state="pending", raw=data)

    async def download(self, url: str, destination: Path) -> Path:
        if url.startswith("file://"):
            source = Path(url[len("file://") :])
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            return destination
        return await super().download(url, destination)
