"""Provider contract shared by every online video backend.

A provider is a thin, stateless HTTP adapter: submit a job, poll it, download
the finished clip. All scheduling, quota, retry and failover policy lives in
``scheduler.py`` so that adding a provider never means touching orchestration.
"""

from __future__ import annotations

import asyncio
import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings

ERROR_KINDS = {
    "quota",
    "rate_limit",
    "auth",
    "invalid_request",
    "transient",
    "unavailable",
    "not_found",
    "paid_required",
    "unsupported",
}

#: Kinds that mean "this provider is unhealthy for a while".
COOLDOWN_KINDS = {"auth", "unavailable", "transient", "rate_limit"}


class ProviderError(RuntimeError):
    """Normalised provider failure.

    ``kind`` drives the scheduler: quota and rate-limit errors pause the model
    instead of the whole provider, auth errors park the provider until the key
    is fixed, and transient errors trigger immediate failover to another model.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "transient",
        retry_after: float | None = None,
        status_code: int | None = None,
        transient: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "transient"
        self.retry_after = retry_after
        self.status_code = status_code
        self.transient = transient if transient is not None else self.kind in {
            "transient",
            "unavailable",
            "rate_limit",
        }


@dataclass
class VideoRequest:
    """Everything a model may need for one scene."""

    prompt: str
    duration: float = 10.0
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    negative_prompt: str = ""
    reference_image: Path | None = None
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitResult:
    state: str
    remote_id: str = ""
    video_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PollResult:
    state: str  # pending | succeeded | failed | not_found
    video_url: str | None = None
    error: str | None = None
    error_kind: str | None = None
    progress: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def classify_http(status_code: int, body: str = "") -> ProviderError:
    """Map an HTTP failure onto the scheduler's error kinds."""
    text = (body or "").strip()
    snippet = text[:400].replace("\n", " ") if text else ""
    lowered = text.lower()

    if status_code in (401, 403):
        kind = "auth"
    elif status_code == 402:
        kind = "paid_required"
    elif status_code == 404:
        kind = "not_found"
    elif status_code == 429:
        kind = "rate_limit"
    elif status_code == 400 or status_code == 422:
        kind = "invalid_request"
    elif status_code in (500, 502, 503, 504):
        if "queue" in lowered and "full" in lowered:
            kind = "quota"
        else:
            kind = "unavailable"
    else:
        kind = "transient"

    if any(token in lowered for token in ("insufficient", "quota", "credit", "balance")):
        kind = "quota"

    return ProviderError(
        f"HTTP {status_code}: {snippet or 'no response body'}",
        kind=kind,
        status_code=status_code,
    )


def retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def image_to_data_uri(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "png")
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{payload}"


class VideoProvider(ABC):
    """Base class for cloud video providers."""

    id: str = "base"
    label: str = ""
    #: Providers that need no credentials (mock/testing).
    requires_key: bool = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    # -- credentials -----------------------------------------------------
    @property
    def keys(self) -> list[str]:
        return self.settings.keys_for(self.id)

    @property
    def configured(self) -> bool:
        return bool(self.keys) or not self.requires_key

    def key(self, index: int = 0) -> str:
        keys = self.keys
        if not keys:
            raise ProviderError(f"{self.id}: no API key configured", kind="auth")
        return keys[index % len(keys)]

    def key_id(self, index: int = 0) -> str:
        total = max(1, len(self.keys))
        return f"{self.id}:{(index % total) + 1}"

    # -- http ------------------------------------------------------------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.http_timeout_seconds, connect=20.0),
                follow_redirects=True,
                headers={"User-Agent": "OmniReel/1.0"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def download(self, url: str, destination: Path) -> Path:
        """Stream a finished clip to disk, atomically."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        client = await self.client()
        try:
            async with client.stream(
                "GET", url, timeout=self.settings.download_timeout_seconds
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise classify_http(response.status_code, body)
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1 << 16):
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise ProviderError(f"{self.id}: download failed: {exc}", kind="transient") from exc
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        if not partial.exists() or partial.stat().st_size < 1024:
            partial.unlink(missing_ok=True)
            raise ProviderError(
                f"{self.id}: downloaded clip is empty or truncated", kind="transient"
            )
        partial.replace(destination)
        return destination

    # -- provider API ----------------------------------------------------
    @abstractmethod
    async def submit(self, model: str, request: VideoRequest, *, key_index: int = 0) -> SubmitResult:
        """Create a generation job and return a handle to poll."""

    @abstractmethod
    async def poll(self, model: str, remote_id: str, *, key_index: int = 0) -> PollResult:
        """Check one job. Must never raise for a still-running job."""

    # -- convenience -----------------------------------------------------
    async def generate_blocking(
        self,
        model: str,
        request: VideoRequest,
        *,
        key_index: int = 0,
        poll_interval: float | None = None,
        max_wait: float = 900.0,
    ) -> str:
        """Submit + poll until terminal. Used by the test harness and CLI probe."""
        interval = poll_interval or self.settings.poll_interval_seconds
        submitted = await self.submit(model, request, key_index=key_index)
        if submitted.state == "succeeded":
            if not submitted.video_url:
                raise ProviderError(f"{self.id}: job finished without a video URL")
            return submitted.video_url
        if submitted.state == "failed":
            raise ProviderError(f"{self.id}: job failed immediately", kind="transient")

        waited = 0.0
        while waited < max_wait:
            await asyncio.sleep(interval)
            waited += interval
            result = await self.poll(model, submitted.remote_id, key_index=key_index)
            if result.state == "succeeded":
                if not result.video_url:
                    raise ProviderError(f"{self.id}: job finished without a video URL")
                return result.video_url
            if result.state == "failed":
                raise ProviderError(
                    result.error or f"{self.id}: job failed",
                    kind=result.error_kind or "transient",
                )
        raise ProviderError(f"{self.id}: job did not finish within {max_wait:.0f}s", kind="transient")
