"""Generic configurable HTTP video adapter (used for YepAPI-style gateways).

YepAPI publishes its own model catalogue and pricing but does not expose a single
stable public contract for third-party video jobs. Rather than guessing endpoints
and pretending they work, this adapter takes the contract from configuration:

    YEPAPI_API_KEY=<key>
    YEPAPI_BASE_URL=https://api.yepapi.com/v1
    YEPAPI_SUBMIT_PATH=/video/generations        # POST -> {"id": "..."} or {"data": {"id": ...}}
    YEPAPI_STATUS_PATH=/video/generations/{id}   # GET  -> {"status", "url"/"video_url"}

Until every variable is set the provider reports itself unconfigured, so the
scheduler never touches it. Credit grants on gateways like this are one-off, so
the catalogue also marks it ``trial`` and keeps it out of free-only mode.
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

TERMINAL_OK = {"succeeded", "success", "completed", "done", "finished"}
TERMINAL_FAIL = {"failed", "error", "cancelled", "canceled", "rejected"}


class YepApiProvider(VideoProvider):
    id = "yepapi"
    label = "YepAPI (configurable gateway)"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.base_url = os.environ.get("YEPAPI_BASE_URL", "").strip().rstrip("/")
        self.submit_path = os.environ.get("YEPAPI_SUBMIT_PATH", "/video/generations").strip()
        self.status_path = os.environ.get(
            "YEPAPI_STATUS_PATH", "/video/generations/{id}"
        ).strip()

    @property
    def configured(self) -> bool:
        return bool(self.keys) and bool(self.base_url)

    def _headers(self, key_index: int) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key(key_index)}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise ProviderError("yepapi: YEPAPI_BASE_URL is not configured", kind="auth")
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    @staticmethod
    def _first_url(data: dict) -> str | None:
        for field in ("url", "video_url", "videoUrl", "output_url"):
            value = data.get(field)
            if isinstance(value, str) and value.startswith("http"):
                return value
        output = data.get("output")
        if isinstance(output, str) and output.startswith("http"):
            return output
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
        return None

    @staticmethod
    def _job_id(data: dict) -> str | None:
        for field in ("id", "task_id", "taskId", "job_id"):
            value = data.get(field)
            if value:
                return str(value)
        nested = data.get("data")
        if isinstance(nested, dict):
            for field in ("id", "task_id", "taskId"):
                value = nested.get(field)
                if value:
                    return str(value)
        return None

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        payload = {
            "model": model,
            "prompt": request.prompt,
            "duration": int(round(float(request.duration or 5))),
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        try:
            response = await client.post(
                self._url(self.submit_path), json=payload, headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"yepapi: submit failed: {exc}", kind="transient") from exc

        if response.status_code not in (200, 201, 202):
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("yepapi: submit returned non-JSON", kind="transient") from exc

        remote_id = self._job_id(data)
        url = self._first_url(data)
        if url and not remote_id:
            return SubmitResult(state="succeeded", remote_id=url, video_url=url, raw=data)
        if not remote_id:
            raise ProviderError("yepapi: response had neither job id nor video URL", kind="transient")
        return SubmitResult(state="pending", remote_id=remote_id, raw=data)

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        client = await self.client()
        path = self.status_path.replace("{id}", remote_id)
        try:
            response = await client.get(self._url(path), headers=self._headers(key_index))
        except httpx.HTTPError as exc:
            raise ProviderError(f"yepapi: poll failed: {exc}", kind="transient") from exc

        if response.status_code == 404:
            return PollResult(state="not_found")
        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError:
            return PollResult(state="pending")

        status = str(data.get("status") or data.get("state") or "").lower()
        url = self._first_url(data)
        if url and status in TERMINAL_OK | {""}:
            return PollResult(state="succeeded", video_url=url, progress=1.0, raw=data)
        if status in TERMINAL_FAIL:
            return PollResult(
                state="failed",
                error=str(data.get("error") or data.get("message") or "yepapi job failed"),
                error_kind="transient",
                raw=data,
            )
        return PollResult(state="pending", raw=data)
