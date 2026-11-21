"""ModelScope API-Inference video adapter.

ModelScope (魔搭) serves open video models to registered users through a
task-based API. Verified against ``modelscope.cn/docs/model-service/API-Inference``
(see PROVIDER_RESEARCH.md):

* ``POST https://api-inference.modelscope.cn/v1/videos/generations`` with
  ``{"model", "prompt", "size", "duration"}`` answers a ``task_id``;
* ``GET https://api-inference.modelscope.cn/v1/tasks/{task_id}`` polled with
  ``X-ModelScope-Task-Type: video_generation`` answers ``task_status`` of
  ``PENDING`` / ``RUNNING`` / ``SUCCEED`` / ``FAILED`` and the finished clip at
  ``outputs.url``;
* auth is ``Authorization: Bearer <access token>``, and the account must have a
  bound, real-name-verified Alibaba Cloud account before API-Inference works.

The service is free but credit-metered: a wallet of 魔粒 (magicubes) refreshes
daily (roughly 200 for signing in, plus 50 for binding an Alibaba Cloud account)
and one video call costs a handful of them. OmniReel therefore treats it as a
recurring free allowance and keeps an estimated ledger, because ModelScope does
not publish a per-model call count.

Image conditioning is left out on purpose: the documented ``media`` field takes a
public image URL, which this controller does not have for a locally extracted
continuity frame. The catalogue entries say so.
"""

from __future__ import annotations

import os

import httpx

from .base import (
    PollResult,
    ProviderError,
    SubmitResult,
    VideoProvider,
    VideoRequest,
    classify_http,
)

DEFAULT_BASE = "https://api-inference.modelscope.cn"

#: ModelScope tags each task family on read.
TASK_TYPE_HEADER = "X-ModelScope-Task-Type"
TASK_TYPE = "video_generation"

_SIZES = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "1024x1024"}

#: Words that mean "you are out of credits" rather than "that request was bad".
_QUOTA_WORDS = ("magicube", "魔粒", "quota", "insufficient", "balance", "credit", "limit exceeded")


def _message_from(payload: object) -> str:
    """ModelScope reports failures as ``errors`` (dict/list) or ``message``."""
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if isinstance(errors, dict):
        return str(errors.get("message") or errors.get("detail") or errors)
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
        return str(first)
    return str(payload.get("message") or "")


class ModelScopeProvider(VideoProvider):
    id = "modelscope"
    label = "ModelScope API-Inference"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.base_url = os.environ.get("MODELSCOPE_BASE_URL", DEFAULT_BASE).rstrip("/")

    def _headers(self, key_index: int, *, poll: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.key(key_index)}",
            "Content-Type": "application/json",
        }
        if poll:
            headers[TASK_TYPE_HEADER] = TASK_TYPE
        return headers

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        payload: dict = {
            "model": model,
            "prompt": request.prompt,
            "size": _SIZES.get(request.aspect_ratio, _SIZES["16:9"]),
            "duration": max(1, int(round(float(request.duration or 5.0)))),
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            payload["seed"] = int(request.seed)
        try:
            response = await client.post(
                f"{self.base_url}/v1/videos/generations",
                json=payload,
                headers=self._headers(key_index),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"modelscope: submit failed: {exc}", kind="transient") from exc

        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("modelscope: submit returned non-JSON", kind="transient") from exc

        if not isinstance(data, dict):
            raise ProviderError("modelscope: submit returned non-JSON", kind="transient")

        message = _message_from(data)
        lowered = message.lower()
        if any(word in lowered for word in _QUOTA_WORDS):
            raise ProviderError(f"modelscope: {message}", kind="quota")

        remote_id = data.get("task_id")
        if not remote_id:
            raise ProviderError(
                f"modelscope: submit returned no task id{': ' + message if message else ''}",
                kind="invalid_request" if message else "transient",
            )
        if str(data.get("status") or "").upper() == "FAILED":
            raise ProviderError(
                f"modelscope: task failed on submission: {message or 'no detail'}",
                kind="transient",
            )
        return SubmitResult(state="pending", remote_id=str(remote_id), raw=data)

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        client = await self.client()
        try:
            response = await client.get(
                f"{self.base_url}/v1/tasks/{remote_id}", headers=self._headers(key_index, poll=True)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"modelscope: poll failed: {exc}", kind="transient") from exc

        if response.status_code == 404:
            return PollResult(state="not_found")
        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError:
            return PollResult(state="pending")

        if not isinstance(data, dict):
            return PollResult(state="pending")

        status = str(data.get("task_status") or "").upper()
        if status == "SUCCEED":
            outputs = data.get("outputs")
            url = outputs.get("url") if isinstance(outputs, dict) else None
            if not url:
                return PollResult(
                    state="failed",
                    error="modelscope finished without an output url",
                    error_kind="transient",
                    raw=data,
                )
            return PollResult(state="succeeded", video_url=str(url), progress=1.0, raw=data)
        if status == "FAILED":
            message = _message_from(data) or "modelscope task failed"
            kind = "quota" if any(word in message.lower() for word in _QUOTA_WORDS) else "transient"
            return PollResult(state="failed", error=message, error_kind=kind, raw=data)
        return PollResult(state="pending", raw=data)
