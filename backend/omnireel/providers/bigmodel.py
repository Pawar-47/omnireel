"""Zhipu AI (BigModel) video adapter - the second genuinely free video API.

Contract verified against ``docs.bigmodel.cn`` (see PROVIDER_RESEARCH.md for the
pages and dates):

* ``POST https://open.bigmodel.cn/api/paas/v4/videos/generations`` with
  ``{"model", "prompt", "image_url"?, "size", "fps", "quality"}`` answers
  ``{"model", "id", "request_id", "task_status"}``;
* ``GET https://open.bigmodel.cn/api/paas/v4/async-result/{id}`` answers
  ``task_status`` of ``PROCESSING`` / ``SUCCESS`` / ``FAIL`` and, once finished,
  ``video_result[0].url``;
* auth is a plain ``Authorization: Bearer <api key>`` header.

``cogvideox-flash`` is listed at price 免费 (free) on BigModel's own pricing page,
which makes it a second recurring free video source next to Agnes. Its request
schema carries **no duration field**: clip length is chosen by the service, so
OmniReel records the length it asked for, measures the length it got back and
lets FFmpeg trim. That is why the catalogue entry is marked verified for price
and endpoints but the duration is flagged as unconfirmed.

``cogvideox-3`` does take ``duration`` (5 or 10) and is billed at 1 CNY per call,
so it stays out of free-only mode.
"""

from __future__ import annotations

import base64
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

DEFAULT_BASE = "https://open.bigmodel.cn/api/paas/v4"

#: ``size`` values from the published schema (the enum has no 1280x720 entry).
_SIZES = {"16:9": "1920x1080", "9:16": "1080x1920", "1:1": "1024x1024"}

#: Models whose request schema publishes a ``duration`` field.
_DURATION_MODELS = {"cogvideox-3"}

#: Models documented to render audio worth keeping.
_AUDIO_MODELS = {"cogvideox-3"}

#: Documented business error codes.
_ERROR_KINDS = {
    "1000": "auth",
    "1001": "auth",
    "1002": "auth",
    "1003": "auth",
    "1004": "auth",
    "1113": "quota",  # 余额不足
    "1302": "rate_limit",  # per-account concurrency
    "1305": "unavailable",  # platform overloaded
}


class BigModelProvider(VideoProvider):
    id = "bigmodel"
    label = "Zhipu BigModel (智谱)"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.base_url = os.environ.get("BIGMODEL_BASE_URL", DEFAULT_BASE).rstrip("/")

    def _headers(self, key_index: int) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key(key_index)}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _error_detail(response: httpx.Response) -> tuple[str, str]:
        try:
            payload = response.json()
        except ValueError:
            return "", ""
        if not isinstance(payload, dict):
            return "", ""
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or ""), str(error.get("message") or "")
        return str(payload.get("code") or ""), str(payload.get("message") or "")

    @staticmethod
    def _business_error(payload: object) -> ProviderError | None:
        """BigModel reports refusals inside the body, sometimes with HTTP 200."""
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        code = str(error.get("code") or "")
        return ProviderError(
            f"bigmodel: {error.get('message') or 'request failed'} (code {code})",
            kind=_ERROR_KINDS.get(code, "transient"),
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        code, message = self._error_detail(response)
        kind = _ERROR_KINDS.get(code)
        if kind is None:
            raise classify_http(response.status_code, response.text)
        raise ProviderError(
            f"bigmodel: {message or 'request failed'} (code {code})",
            kind=kind,
            status_code=response.status_code,
        )

    def _payload(self, model: str, request: VideoRequest) -> dict:
        payload: dict = {
            "model": model,
            "prompt": request.prompt[:512],
            "request_id": uuid.uuid4().hex,
            "size": _SIZES.get(request.aspect_ratio, _SIZES["16:9"]),
            "fps": 30,
        }
        if model in _AUDIO_MODELS:
            payload["with_audio"] = True
        if model in _DURATION_MODELS:
            wanted = float(request.duration or 5.0)
            payload["duration"] = 10 if wanted > 7.5 else 5
        if request.reference_image is not None and request.reference_image.exists():
            # The schema accepts a URL or Base64-encoded image bytes.
            payload["image_url"] = base64.b64encode(
                request.reference_image.read_bytes()
            ).decode("ascii")
        return payload

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        try:
            response = await client.post(
                f"{self.base_url}/videos/generations",
                json=self._payload(model, request),
                headers=self._headers(key_index),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"bigmodel: submit failed: {exc}", kind="transient") from exc

        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("bigmodel: submit returned non-JSON", kind="transient") from exc

        if not isinstance(data, dict):
            raise ProviderError("bigmodel: submit returned non-JSON", kind="transient")

        error = self._business_error(data)
        if error is not None:
            raise error

        remote_id = data.get("id")
        if not remote_id:
            raise ProviderError("bigmodel: submit returned no task id", kind="transient")
        if str(data.get("task_status") or "").upper() == "FAIL":
            raise ProviderError("bigmodel: task failed on submission", kind="transient")
        # Even an immediately-finished task answers without video_result, so the
        # poll below is what actually collects the clip.
        return SubmitResult(state="pending", remote_id=str(remote_id), raw=data)

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        client = await self.client()
        try:
            response = await client.get(
                f"{self.base_url}/async-result/{remote_id}", headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"bigmodel: poll failed: {exc}", kind="transient") from exc

        if response.status_code == 404:
            return PollResult(state="not_found")
        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError:
            return PollResult(state="pending")

        if not isinstance(data, dict):
            return PollResult(state="pending")

        status = str(data.get("task_status") or "").upper()
        if status == "SUCCESS":
            results = data.get("video_result") or []
            url = None
            if isinstance(results, list) and results:
                first = results[0]
                if isinstance(first, dict):
                    url = first.get("url")
            if not url:
                return PollResult(
                    state="failed",
                    error="bigmodel finished without a video url",
                    error_kind="transient",
                    raw=data,
                )
            return PollResult(state="succeeded", video_url=str(url), progress=1.0, raw=data)
        if status == "FAIL":
            code, message = "", ""
            error = data.get("error")
            if isinstance(error, dict):
                code, message = str(error.get("code") or ""), str(error.get("message") or "")
            return PollResult(
                state="failed",
                error=message or f"bigmodel task failed (code {code or 'unknown'})",
                error_kind=_ERROR_KINDS.get(code, "transient"),
                raw=data,
            )
        business_error = self._business_error(data)
        if business_error is not None and not status:
            raise business_error
        return PollResult(state="pending", raw=data)
