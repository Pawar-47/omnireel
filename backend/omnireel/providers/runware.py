"""Runware video adapter.

Runware exposes a single task endpoint (``POST https://api.runware.ai/v1``) that
takes an array of task objects. Video generation uses ``taskType:
"videoInference"`` and asynchronous delivery, where the finished clip arrives at
``data[0].videoURL`` and a pending task is re-queried with ``taskType:
"getResponse"``.

This adapter follows that published task shape, but the exact model id and
duration set differ per model, so the catalogue ships it disabled and the
documentation asks you to confirm the id in the Runware dashboard first. Runware
startup credits are one-off, never recurring, so free-only mode skips it.
"""

from __future__ import annotations

import os
import uuid

import httpx

from .base import (
    PollResult,
    ProviderError,
    SubmitResult,
    VideoProvider,
    VideoRequest,
    classify_http,
)

DEFAULT_ENDPOINT = "https://api.runware.ai/v1"


class RunwareProvider(VideoProvider):
    id = "runware"
    label = "Runware"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.endpoint = os.environ.get("RUNWARE_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")

    def _headers(self, key_index: int) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key(key_index)}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _dimensions(aspect_ratio: str, resolution: str) -> tuple[int, int]:
        long_side = 1280 if (resolution or "720p").lower() != "1080p" else 1920
        short_side = long_side * 9 // 16
        if aspect_ratio == "9:16":
            return short_side, long_side
        if aspect_ratio == "1:1":
            return short_side, short_side
        return long_side, short_side

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        width, height = self._dimensions(request.aspect_ratio, request.resolution)
        task_uuid = str(uuid.uuid4())
        task: dict = {
            "taskType": "videoInference",
            "taskUUID": task_uuid,
            "model": model,
            "positivePrompt": request.prompt,
            "duration": int(round(float(request.duration or 5))),
            "width": width,
            "height": height,
            "deliveryMethod": "async",
        }
        if request.negative_prompt:
            task["negativePrompt"] = request.negative_prompt
        if request.seed is not None:
            task["seed"] = int(request.seed)
        try:
            response = await client.post(
                self.endpoint, json=[task], headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"runware: submit failed: {exc}", kind="transient") from exc

        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("runware: submit returned non-JSON", kind="transient") from exc

        errors = payload.get("errors") or []
        if errors:
            message = "; ".join(str(item.get("message", item)) for item in errors)
            raise ProviderError(f"runware: {message}", kind="invalid_request")

        items = payload.get("data") or []
        if not items:
            raise ProviderError("runware: empty task response", kind="transient")
        item = items[0]
        if item.get("videoURL"):
            return SubmitResult(
                state="succeeded", remote_id=task_uuid, video_url=item["videoURL"], raw=item
            )
        return SubmitResult(state="pending", remote_id=task_uuid, raw=item)

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        client = await self.client()
        task = {"taskType": "getResponse", "taskUUID": remote_id}
        try:
            response = await client.post(
                self.endpoint, json=[task], headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"runware: poll failed: {exc}", kind="transient") from exc

        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError:
            return PollResult(state="pending")

        errors = payload.get("errors") or []
        if errors:
            message = "; ".join(str(item.get("message", item)) for item in errors)
            return PollResult(state="failed", error=message, error_kind="invalid_request")

        items = payload.get("data") or []
        if not items:
            return PollResult(state="pending")
        item = items[0]
        status = str(item.get("status") or "").lower()
        if item.get("videoURL"):
            return PollResult(state="succeeded", video_url=item["videoURL"], progress=1.0, raw=item)
        if status in {"error", "failed", "cancelled", "canceled"}:
            return PollResult(
                state="failed",
                error=str(item.get("error") or item.get("message") or "runware task failed"),
                error_kind="transient",
                raw=item,
            )
        return PollResult(state="pending", raw=item)
