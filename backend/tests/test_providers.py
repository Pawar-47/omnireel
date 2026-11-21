"""Adapter contract tests - never touch the network.

Each adapter is driven through ``httpx.MockTransport`` with the response shapes
taken from the provider's own documentation (see PROVIDER_RESEARCH.md), so a
regression in parsing, status handling or error mapping fails here instead of on
a real, quota-consuming call.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from omnireel.capabilities import CapabilityRegistry
from omnireel.config import Settings
from omnireel.planner import allocate_durations
from omnireel.providers.base import ProviderError, VideoRequest
from omnireel.providers.bigmodel import BigModelProvider
from omnireel.providers.modelscope import ModelScopeProvider
from omnireel.providers.pollinations import PollinationsProvider

REQUEST = VideoRequest(prompt="a lighthouse at dawn", duration=10.0, aspect_ratio="16:9")


def build_provider(cls, tmp_path, provider_id: str):  # noqa: ANN001, ANN201
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    settings.keys = {provider_id: ["test-key"]}
    return cls(settings)


def attach(provider, handler):  # noqa: ANN001, ANN201
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


# --------------------------------------------------------------------- bigmodel
def test_bigmodel_submit_then_poll_collects_video_result_url(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/videos/generations"):
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.content)
            assert body["model"] == "cogvideox-flash"
            assert body["size"] == "1920x1080"
            # cogvideox-flash has no duration field; sending one would be a 400.
            assert "duration" not in body
            return httpx.Response(200, json={"id": "task-1", "task_status": "PROCESSING"})
        assert request.url.path.endswith("/api/paas/v4/async-result/task-1")
        return httpx.Response(
            200,
            json={
                "task_status": "SUCCESS",
                "video_result": [{"url": "https://cdn.example/clip.mp4", "cover_image_url": "x"}],
            },
        )

    provider = attach(build_provider(BigModelProvider, tmp_path, "bigmodel"), handler)
    submitted = asyncio.run(provider.submit("cogvideox-flash", REQUEST))
    assert (submitted.state, submitted.remote_id) == ("pending", "task-1")

    polled = asyncio.run(provider.poll("cogvideox-flash", "task-1"))
    assert polled.state == "succeeded"
    assert polled.video_url == "https://cdn.example/clip.mp4"
    assert len(seen) == 2


def test_bigmodel_sends_ten_seconds_for_the_model_that_accepts_it(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "task-9", "task_status": "PROCESSING"})

    provider = attach(build_provider(BigModelProvider, tmp_path, "bigmodel"), handler)
    asyncio.run(provider.submit("cogvideox-3", REQUEST))
    assert captured["duration"] == 10
    assert captured["with_audio"] is True


def test_bigmodel_concurrency_error_becomes_rate_limit(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": "1302", "message": "并发过高"}})

    provider = attach(build_provider(BigModelProvider, tmp_path, "bigmodel"), handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.submit("cogvideox-flash", REQUEST))
    assert caught.value.kind == "rate_limit"


def test_bigmodel_failed_task_reports_the_reason(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"task_status": "FAIL", "error": {"code": "1305", "message": "busy"}}
        )

    provider = attach(build_provider(BigModelProvider, tmp_path, "bigmodel"), handler)
    result = asyncio.run(provider.poll("cogvideox-flash", "task-1"))
    assert result.state == "failed"
    assert result.error == "busy"
    assert result.error_kind == "unavailable"


def test_bigmodel_honours_a_base64_reference_frame(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "task-2", "task_status": "PROCESSING"})

    provider = attach(build_provider(BigModelProvider, tmp_path, "bigmodel"), handler)
    request = VideoRequest(prompt="continue", duration=5.0, reference_image=frame)
    asyncio.run(provider.submit("cogvideox-flash", request))
    assert captured["image_url"].startswith("iVBOR")


# ------------------------------------------------------------------- modelscope
def test_modelscope_submit_and_poll_with_task_type_header(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/videos/generations"):
            body = json.loads(request.content)
            assert body["model"] == "MiniMax/MiniMax-H3"
            assert body["duration"] == 10
            assert body["size"] == "1280x720"
            return httpx.Response(200, json={"task_id": "abc123", "status": "PENDING"})
        assert request.url.path.endswith("/v1/tasks/abc123")
        assert request.headers["x-modelscope-task-type"] == "video_generation"
        return httpx.Response(
            200,
            json={"task_status": "SUCCEED", "outputs": {"url": "https://cdn.example/wan.mp4"}},
        )

    provider = attach(build_provider(ModelScopeProvider, tmp_path, "modelscope"), handler)
    submitted = asyncio.run(provider.submit("MiniMax/MiniMax-H3", REQUEST))
    assert submitted.remote_id == "abc123"
    polled = asyncio.run(provider.poll("MiniMax/MiniMax-H3", "abc123"))
    assert polled.video_url == "https://cdn.example/wan.mp4"


def test_modelscope_magicube_exhaustion_is_quota_not_a_bad_request(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": {"message": "insufficient 魔粒 balance for this model"}}
        )

    provider = attach(build_provider(ModelScopeProvider, tmp_path, "modelscope"), handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.submit("MiniMax/MiniMax-H3", REQUEST))
    assert caught.value.kind == "quota"


def test_modelscope_running_task_stays_pending(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"task_status": "RUNNING"})

    provider = attach(build_provider(ModelScopeProvider, tmp_path, "modelscope"), handler)
    assert asyncio.run(provider.poll("MiniMax/MiniMax-H3", "abc123")).state == "pending"


# ----------------------------------------------------------------- pollinations
def test_pollinations_async_job_then_result_url(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/video/"):
            assert request.headers["prefer"] == "respond-async"
            assert request.headers["authorization"] == "Bearer test-key"
            assert request.url.params["model"] == "google/veo-3.1-fast"
            assert request.url.params["duration"] == "10"
            return httpx.Response(202, json={"id": "job-7"})
        assert request.url.path == "/v1/jobs/job-7"
        return httpx.Response(
            200, json={"status": "succeeded", "result_url": "https://cdn.example/veo.mp4"}
        )

    provider = attach(build_provider(PollinationsProvider, tmp_path, "pollinations"), handler)
    submitted = asyncio.run(provider.submit("google/veo-3.1-fast", REQUEST))
    assert (submitted.state, submitted.remote_id) == ("pending", "job-7")
    polled = asyncio.run(provider.poll("google/veo-3.1-fast", "job-7"))
    assert polled.video_url == "https://cdn.example/veo.mp4"


def test_pollinations_pending_result_endpoint_409_is_not_a_failure(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "still running"})

    provider = attach(build_provider(PollinationsProvider, tmp_path, "pollinations"), handler)
    assert asyncio.run(provider.poll("alibaba/wan-2.6", "job-7")).state == "pending"


def test_pollinations_inline_media_is_saved_and_downloadable(tmp_path):
    payload = b"\x00\x00\x00\x18ftypmp42" + b"v" * 4096

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=payload, headers={"content-type": "video/mp4"}
        )

    provider = attach(build_provider(PollinationsProvider, tmp_path, "pollinations"), handler)
    submitted = asyncio.run(provider.submit("minimax/minimax-h3", REQUEST))
    assert submitted.state == "succeeded"
    assert submitted.video_url.startswith("file://")

    destination = tmp_path / "clip.mp4"
    asyncio.run(provider.download(submitted.video_url, destination))
    assert destination.read_bytes() == payload


def test_pollinations_payment_required_is_its_own_kind(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "video models need paid balance"})

    provider = attach(build_provider(PollinationsProvider, tmp_path, "pollinations"), handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.submit("alibaba/wan-2.6", REQUEST))
    assert caught.value.kind == "paid_required"


# -------------------------------------------------------------------- catalogue
def test_catalogue_flags_the_two_new_free_sources_and_keeps_paid_models_out():
    registry = CapabilityRegistry.default()

    flash = registry.get("bigmodel", "cogvideox-flash")
    assert flash is not None and flash.enabled
    assert flash.free_eligible and flash.free_kind == "recurring"

    modelscope = registry.get("modelscope", "MiniMax/MiniMax-H3")
    assert modelscope is not None and modelscope.enabled
    assert modelscope.free_eligible and modelscope.durations == [3.0, 5.0, 10.0]

    pollinations = registry.for_provider("pollinations")
    assert len(pollinations) >= 6
    assert all(not spec.free_eligible for spec in pollinations)
    assert any(spec.durations == [5.0, 10.0, 15.0] for spec in pollinations)


def test_free_ladder_still_solves_an_exact_two_minute_timeline():
    registry = CapabilityRegistry.default()
    ladder = registry.durations_ladder(free_only=True)

    assert {3.0, 5.0, 10.0} <= set(ladder)
    # 15 s exists only on a paid model, so free-only must never plan around it.
    assert 15.0 not in ladder

    durations = allocate_durations(120.0, ladder)
    assert abs(sum(durations) - 120.0) <= 0.75


def test_every_new_catalogue_entry_names_a_provider_class():
    from omnireel.providers import PROVIDER_CLASSES

    known = {cls.id for cls in PROVIDER_CLASSES}
    registry = CapabilityRegistry.default()
    unknown = {spec.provider for spec in registry.all()} - known
    assert unknown == set()
