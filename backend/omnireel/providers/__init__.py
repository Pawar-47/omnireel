"""Provider registry.

Adding a provider means adding a class with an ``id`` and implementing
``submit``/``poll``; the scheduler never needs to know it exists.
"""

from __future__ import annotations

from ..config import Settings
from .agnes import AgnesProvider
from .base import (
    PollResult,
    ProviderError,
    SubmitResult,
    VideoProvider,
    VideoRequest,
    classify_http,
)
from .bigmodel import BigModelProvider
from .huggingface import HuggingFaceProvider
from .mock import MockProvider
from .modelscope import ModelScopeProvider
from .pollinations import PollinationsProvider
from .replicate import ReplicateProvider
from .runware import RunwareProvider
from .yepapi import YepApiProvider

PROVIDER_CLASSES: tuple[type[VideoProvider], ...] = (
    AgnesProvider,
    BigModelProvider,
    ModelScopeProvider,
    PollinationsProvider,
    ReplicateProvider,
    HuggingFaceProvider,
    RunwareProvider,
    YepApiProvider,
    MockProvider,
)


def build_providers(settings: Settings) -> dict[str, VideoProvider]:
    return {provider_cls.id: provider_cls(settings) for provider_cls in PROVIDER_CLASSES}


def configured_providers(providers: dict[str, VideoProvider]) -> list[str]:
    return [provider_id for provider_id, provider in providers.items() if provider.configured]


__all__ = [
    "PROVIDER_CLASSES",
    "AgnesProvider",
    "BigModelProvider",
    "HuggingFaceProvider",
    "MockProvider",
    "ModelScopeProvider",
    "PollResult",
    "PollinationsProvider",
    "ProviderError",
    "ReplicateProvider",
    "RunwareProvider",
    "SubmitResult",
    "VideoProvider",
    "VideoRequest",
    "YepApiProvider",
    "build_providers",
    "classify_http",
    "configured_providers",
]
