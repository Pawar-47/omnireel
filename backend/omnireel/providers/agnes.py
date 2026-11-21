"""Agnes AI video provider.

Protocol verified against the vendor wiki and the published reference client
(2026-09):

* ``POST {base}/videos`` with ``model``, ``prompt``, ``mode`` (``text`` /
  ``keyframe`` / ``reference``), ``seconds`` (``"4"``-``"12"``), ``size``
  (``720P`` / ``1080P``) and ``aspect_ratio``; answers ``{"video_id": ...}``.
* ``GET {base}/videos/{id}`` (fallback: ``GET {root}/agnesapi?video_id=...``)
  answers ``status`` (``processing`` / ``completed`` / ``failed``), ``progress``,
  ``error: {code, message}`` and the finished file in ``metadata.url``.
* Rate limit is roughly one video submission per minute with a broader
  ~16 requests/minute ceiling; a saturated free queue answers
  ``503 {"code": "video_queue_full"}`` without consuming quota.

Keyframe conditioning needs a publicly reachable URL, so a local reference
frame is first hosted through Agnes' own image endpoint, with a base64 data URI
as the fallback path.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .base import (
    PollResult,
    ProviderError,
    SubmitResult,
    VideoProvider,
    VideoRequest,
    classify_http,
    image_to_data_uri,
)

DEFAULT_HOST = "https://apihub.agnes-ai.com"
MAX_SECONDS = 12
MIN_SECONDS = 4


class AgnesProvider(VideoProvider):
    id = "agnes"
    label = "Agnes AI"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        raw = os.environ.get("AGNES_BASE_URL", DEFAULT_HOST).strip().rstrip("/")
        if raw.endswith("/v1"):
            raw = raw[:-3]
        self.api_root = raw.rstrip("/")
        self.base_url = f"{self.api_root}/v1"

    # -- payload ---------------------------------------------------------
    @staticmethod
    def _normalise_seconds(duration: float) -> str:
        seconds = int(round(float(duration or MIN_SECONDS)))
        return str(max(MIN_SECONDS, min(seconds, MAX_SECONDS)))

    @staticmethod
    def _size_for(model: str, resolution: str) -> str:
        if "flash" in model.lower():
            return "720P"
        return {
            "1080p": "1080P",
            "720p": "720P",
            "480p": "720P",
            "360p": "720P",
        }.get((resolution or "720p").lower(), "720P")

    @staticmethod
    def _aspect(aspect_ratio: str) -> str:
        return aspect_ratio if aspect_ratio in {"16:9", "9:16", "1:1"} else "16:9"

    def _headers(self, key_index: int) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key(key_index)}",
            "Content-Type": "application/json",
        }

    def _payload(self, model: str, request: VideoRequest) -> dict:
        payload: dict = {
            "model": model,
            "prompt": request.prompt,
            "mode": "text",
            "seconds": self._normalise_seconds(request.duration),
            "size": self._size_for(model, request.resolution),
            "aspect_ratio": self._aspect(request.aspect_ratio),
        }
        if request.seed is not None:
            payload["seed"] = int(request.seed)
        return payload

    # -- reference hosting -----------------------------------------------
    @staticmethod
    def _hosting_size(aspect_ratio: str) -> str:
        """The image endpoint redraws at the size you ask for, so match the framing."""
        return {
            "9:16": "720x1280",
            "1:1": "1024x1024",
        }.get(aspect_ratio, "1280x720")

    async def _host_image(self, path: Path, key_index: int, aspect_ratio: str = "16:9") -> str | None:
        """Make a local frame reachable for keyframe mode."""
        client = await self.client()
        data_uri = image_to_data_uri(path)
        payload = {
            "model": "agnes-image-2.5-flash",
            "prompt": "Keep the image exactly as it is",
            "n": 1,
            "size": self._hosting_size(aspect_ratio),
            "extra_body": {"response_format": "url", "image": data_uri},
        }
        try:
            response = await client.post(
                f"{self.base_url}/images/generations",
                json=payload,
                headers=self._headers(key_index),
                timeout=180.0,
            )
        except httpx.HTTPError:
            return data_uri
        if response.status_code != 200:
            return data_uri
        try:
            data = response.json().get("data") or []
            url = data[0].get("url") if data else ""
        except (ValueError, IndexError, AttributeError):
            return data_uri
        return url or data_uri

    # -- errors ----------------------------------------------------------
    def _error(self, response: httpx.Response) -> ProviderError:
        body = response.text or ""
        lowered = body.lower()
        if "video_queue_full" in lowered or ("queue" in lowered and "full" in lowered):
            return ProviderError(
                "agnes: the free video queue is full, try again shortly",
                kind="unavailable",
                retry_after=120.0,
                status_code=response.status_code,
            )
        error = classify_http(response.status_code, body)
        if error.kind == "rate_limit" and error.retry_after is None:
            error.retry_after = 60.0
        return error

    # -- API -------------------------------------------------------------
    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        payload = self._payload(model, request)
        if request.reference_image is not None and request.reference_image.exists():
            hosted = await self._host_image(
                request.reference_image, key_index, request.aspect_ratio
            )
            if hosted:
                payload["mode"] = "keyframe"
                payload["first_frame"] = hosted
        try:
            response = await client.post(
                f"{self.base_url}/videos", json=payload, headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"agnes: submit failed: {exc}", kind="transient") from exc

        if response.status_code != 200:
            raise self._error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("agnes: submit returned non-JSON", kind="transient") from exc
        remote_id = data.get("video_id") or data.get("task_id") or data.get("id")
        if not remote_id:
            raise ProviderError(
                f"agnes: submit returned no video id: {str(data)[:200]}", kind="transient"
            )
        return SubmitResult(state="pending", remote_id=str(remote_id), raw=data)

    @staticmethod
    def _extract_url(data: dict) -> str:
        for field in ("video_url", "url", "remixed_from_video_id"):
            value = data.get(field)
            if isinstance(value, str) and value.startswith("http"):
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            for field in ("video_url", "url"):
                value = nested.get(field)
                if isinstance(value, str) and value.startswith("http"):
                    return value
        meta = data.get("metadata")
        if isinstance(meta, dict):
            for field in ("url", "video_url"):
                value = meta.get(field)
                if isinstance(value, str) and value.startswith("http"):
                    return value
        return ""

    @staticmethod
    def _extract_error(data: dict) -> str:
        error = data.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = str(error.get("code") or "").strip()
            if message and code:
                return f"{message} ({code})"
            return message or code or ""
        if isinstance(error, str):
            return error
        return str(data.get("message") or "")

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        client = await self.client()
        headers = self._headers(key_index)
        try:
            response = await client.get(f"{self.base_url}/videos/{remote_id}", headers=headers)
            if response.status_code in (400, 404):
                # The older gateway endpoint uses 404 for an unknown task; the
                # REST endpoint uses 400 with code "task_not_exist".
                legacy = await client.get(
                    f"{self.api_root}/agnesapi",
                    params={"video_id": remote_id, "model_name": model},
                    headers=headers,
                )
                if legacy.status_code != 200:
                    if legacy.status_code == 404:
                        return PollResult(state="not_found")
                    raise self._error(legacy)
                response = legacy
            if response.status_code != 200:
                raise self._error(response)
        except httpx.HTTPError as exc:
            raise ProviderError(f"agnes: poll failed: {exc}", kind="transient") from exc

        try:
            data = response.json()
        except ValueError:
            return PollResult(state="pending")

        status = str(data.get("status") or "").lower()
        if status in {"completed", "succeeded", "success"}:
            url = self._extract_url(data)
            if not url:
                return PollResult(
                    state="failed",
                    error="agnes reported completion without a video URL",
                    error_kind="transient",
                )
            return PollResult(state="succeeded", video_url=url, progress=1.0, raw=data)
        if status in {"failed", "error", "cancelled", "canceled"}:
            message = self._extract_error(data) or "agnes generation failed"
            lowered = message.lower()
            kind = "invalid_request" if any(
                word in lowered for word in ("moderation", "sensitive", "policy", "content")
            ) else "transient"
            return PollResult(state="failed", error=message, error_kind=kind, raw=data)

        progress = data.get("progress")
        try:
            progress_value = float(progress) / 100.0 if progress is not None else None
        except (TypeError, ValueError):
            progress_value = None
        return PollResult(state="pending", progress=progress_value, raw=data)
