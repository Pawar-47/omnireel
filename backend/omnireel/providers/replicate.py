"""Replicate video provider.

Uses Replicate's official predictions API:

* ``POST https://api.replicate.com/v1/models/{owner}/{model}/predictions`` with
  ``{"input": {...}}`` (no version pin needed for official models);
* ``GET  https://api.replicate.com/v1/predictions/{id}`` for status;
* ``output`` is either a URL or a list of URLs.

Replicate is a trial fallback: free test runs are limited, then it bills per
run, so the catalogue marks it ``trial`` and free-only mode never selects it.
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
    image_to_data_uri,
)

DEFAULT_BASE = "https://api.replicate.com/v1"


class ReplicateProvider(VideoProvider):
    id = "replicate"
    label = "Replicate"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.base_url = os.environ.get("REPLICATE_BASE_URL", DEFAULT_BASE).rstrip("/")

    def _headers(self, key_index: int) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.key(key_index)}",
            "Content-Type": "application/json",
        }

    def _input(self, model: str, request: VideoRequest) -> dict:
        payload: dict = {"prompt": request.prompt}
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            payload["seed"] = int(request.seed)
        if request.reference_image is not None and request.reference_image.exists():
            # Official video models accept a data URI as the first frame.
            payload["first_frame_image"] = image_to_data_uri(request.reference_image)
        return payload

    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        client = await self.client()
        url = f"{self.base_url}/models/{model}/predictions"
        try:
            response = await client.post(
                url, json={"input": self._input(model, request)}, headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"replicate: submit failed: {exc}", kind="transient") from exc

        if response.status_code not in (200, 201):
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("replicate: submit returned non-JSON", kind="transient") from exc

        remote_id = data.get("id")
        if not remote_id:
            raise ProviderError("replicate: submit returned no prediction id", kind="transient")
        state = "succeeded" if data.get("status") == "succeeded" else "pending"
        return SubmitResult(
            state=state,
            remote_id=str(remote_id),
            video_url=self._extract_url(data.get("output")) if state == "succeeded" else None,
            raw=data,
        )

    @staticmethod
    def _extract_url(output) -> str | None:  # noqa: ANN001
        if isinstance(output, str):
            return output if output.startswith("http") else None
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
        return None

    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        client = await self.client()
        try:
            response = await client.get(
                f"{self.base_url}/predictions/{remote_id}", headers=self._headers(key_index)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"replicate: poll failed: {exc}", kind="transient") from exc

        if response.status_code == 404:
            return PollResult(state="not_found")
        if response.status_code != 200:
            raise classify_http(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError:
            return PollResult(state="pending")

        status = str(data.get("status") or "").lower()
        if status == "succeeded":
            url = self._extract_url(data.get("output"))
            if not url:
                return PollResult(
                    state="failed",
                    error="replicate finished without output",
                    error_kind="transient",
                )
            return PollResult(state="succeeded", video_url=url, progress=1.0, raw=data)
        if status in {"failed", "canceled", "cancelled"}:
            return PollResult(
                state="failed",
                error=str(data.get("error") or "replicate prediction failed"),
                error_kind="transient",
                raw=data,
            )
        return PollResult(state="pending", raw=data)
