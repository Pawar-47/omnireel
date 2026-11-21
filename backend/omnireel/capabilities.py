"""Model capability catalogue.

Every model the scheduler may use is described here: supported clip durations,
resolutions, aspect ratios, text/image conditioning, audio and free-tier status.
Nothing is hardcoded to "10 seconds" - the scheduler reads these numbers.

Honesty rules for this file:

* ``verified_at`` is set only for numbers that were read from a provider's own
  documentation or the provider's public reference client. Everything else is
  marked unverified and ships disabled, so the scheduler can never quietly claim
  a quota or a duration that was guessed.
* ``free_kind`` distinguishes recurring free allowances (``recurring``),
  limited-time promotions (``promo``), one-off trial credits (``trial``) and
  paid-only models (``none``). Free-only mode only ever uses the first two.
* Site-wide overrides live in ``config/providers.json`` so users can add models,
  correct a duration or disable a provider without editing code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ValidationError

from .config import Settings

FREE_KINDS = ("recurring", "promo", "trial", "none")
#: Kinds the free-only scheduler is allowed to spend.
FREE_ELIGIBLE = {"recurring", "promo"}


class ModelSpec(BaseModel):
    """One model on one provider."""

    provider: str
    model: str
    label: str = ""
    durations: list[float]
    resolutions: list[str] = ["720p"]
    aspect_ratios: list[str] = ["16:9", "9:16", "1:1"]
    text_to_video: bool = True
    image_to_video: bool = False
    reference_frame: bool = False
    native_audio: bool = False
    quality: int = 3
    speed: int = 3
    free_kind: str = "none"
    daily_free_requests: int | None = None
    total_free_requests: int | None = None
    min_interval_seconds: float = 0.0
    enabled: bool = True
    docs_url: str = ""
    verified_at: str | None = None
    notes: str = ""

    model_config = {"extra": "ignore"}

    # -- helpers ---------------------------------------------------------
    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def max_duration(self) -> float:
        return max(self.durations) if self.durations else 0.0

    @property
    def min_duration(self) -> float:
        return min(self.durations) if self.durations else 0.0

    @property
    def free_eligible(self) -> bool:
        return self.free_kind in FREE_ELIGIBLE

    @property
    def verified(self) -> bool:
        return bool(self.verified_at)

    def supports_duration(self, seconds: float, tolerance: float = 0.51) -> bool:
        return any(abs(value - seconds) <= tolerance for value in self.durations)

    def snap_duration(self, seconds: float) -> float | None:
        """Closest duration this model can actually generate."""
        if not self.durations:
            return None
        return min(self.durations, key=lambda value: (abs(value - seconds), -value))

    def supports_aspect(self, aspect_ratio: str) -> bool:
        return aspect_ratio in self.aspect_ratios

    def supported_resolution(self, requested: str) -> str | None:
        if requested in self.resolutions:
            return requested
        order = ["1080p", "720p", "480p", "360p"]
        if requested in order:
            wanted = order.index(requested)
            cheaper = [r for r in self.resolutions if r in order and order.index(r) >= wanted]
            if cheaper:
                return min(cheaper, key=lambda r: order.index(r))
        return self.resolutions[0] if self.resolutions else None

    def public_dict(self) -> dict:
        data = self.model_dump()
        data["key"] = self.key
        data["free_eligible"] = self.free_eligible
        data["verified"] = self.verified
        return data


def _spec(**kwargs) -> ModelSpec:
    return ModelSpec(**kwargs)


#: Built-in catalogue. Verified entries were checked against provider docs or a
#: provider-maintained reference client; see PROVIDER_RESEARCH.md for dates.
CATALOG: list[ModelSpec] = [
    _spec(
        provider="agnes",
        model="agnes-video-2.5-flash",
        label="Agnes Video 2.5 Flash",
        durations=[4, 6, 8, 10, 12],
        resolutions=["720p"],
        aspect_ratios=["16:9", "9:16", "1:1"],
        text_to_video=True,
        image_to_video=True,
        reference_frame=True,
        native_audio=False,
        quality=3,
        speed=4,
        free_kind="recurring",
        daily_free_requests=None,
        min_interval_seconds=3.75,
        docs_url="https://wiki.agnes-ai.com/en/docs/overview",
        verified_at="2026-09-22",
        notes=(
            "Text-to-video / image-to-video at 720p, 4-12 s clips. Free with a rate "
            "limit around 16 requests/minute (the reference client's own figure); no "
            "documented hard daily cap, so OmniReel keeps an estimated ledger."
        ),
    ),
    _spec(
        provider="agnes",
        model="agnes-video-2.5",
        label="Agnes Video 2.5",
        durations=[5, 10, 15, 20],
        resolutions=["720p"],
        aspect_ratios=["16:9", "9:16", "1:1"],
        text_to_video=True,
        image_to_video=True,
        reference_frame=True,
        native_audio=False,
        quality=4,
        speed=2,
        free_kind="promo",
        daily_free_requests=None,
        min_interval_seconds=4.0,
        enabled=False,
        docs_url="https://wiki.agnes-ai.com/en/docs/overview",
        verified_at=None,
        notes=(
            "Higher-quality sibling of Flash. The published reference project quotes "
            "up to 20 s per clip, but the current wiki does not state this model's exact "
            "durations, so it ships disabled until you confirm them in the console."
        ),
    ),
    _spec(
        provider="replicate",
        model="minimax/video-01",
        label="MiniMax Video-01 (Replicate)",
        durations=[6],
        resolutions=["720p"],
        aspect_ratios=["16:9", "9:16", "1:1"],
        text_to_video=True,
        image_to_video=True,
        reference_frame=True,
        native_audio=False,
        quality=4,
        speed=3,
        free_kind="trial",
        daily_free_requests=None,
        min_interval_seconds=2.0,
        docs_url="https://replicate.com/minimax/video-01",
        verified_at=None,
        notes=(
            "6 s 720p clips, text- and image-to-video. Replicate only offers limited "
            "free test runs; afterwards it bills per run, so this model is a trial "
            "fallback and is never used in free-only mode."
        ),
    ),
    _spec(
        provider="huggingface",
        model="Wan-AI/Wan2.2-T2V-A14B",
        label="WAN 2.2 T2V (HF Inference Providers)",
        durations=[5],
        resolutions=["480p", "720p"],
        aspect_ratios=["16:9"],
        text_to_video=True,
        image_to_video=False,
        reference_frame=False,
        quality=3,
        speed=2,
        free_kind="none",
        min_interval_seconds=2.0,
        enabled=False,
        docs_url="https://huggingface.co/docs/inference-providers",
        verified_at=None,
        notes=(
            "Routed through Hugging Face Inference Providers, which is pay-as-you-go "
            "with monthly credits only on PRO/Team/Enterprise. Not free recurring, and "
            "the exact per-model duration is provider dependent - disabled by default."
        ),
    ),
    _spec(
        provider="runware",
        model="video-default",
        label="Runware video (set your model id)",
        durations=[5, 10],
        resolutions=["720p"],
        aspect_ratios=["16:9", "9:16", "1:1"],
        text_to_video=True,
        image_to_video=True,
        reference_frame=False,
        quality=4,
        speed=4,
        free_kind="trial",
        enabled=False,
        docs_url="https://runware.ai/",
        verified_at=None,
        notes=(
            "Runware exposes many video models behind one API. Startup credits are "
            "one-off, not recurring. Replace 'video-default' with the exact model id "
            "from your dashboard in config/providers.json before enabling."
        ),
    ),
    _spec(
        provider="yepapi",
        model="video-default",
        label="YepAPI video (set your model id)",
        durations=[5, 8, 10],
        resolutions=["720p", "1080p"],
        aspect_ratios=["16:9", "9:16", "1:1"],
        text_to_video=True,
        image_to_video=True,
        reference_frame=False,
        quality=4,
        speed=3,
        free_kind="trial",
        enabled=False,
        docs_url="https://yepapi.com/",
        verified_at=None,
        notes=(
            "Aggregator with one-time free credits. One-off credit grants are not a "
            "recurring allowance, so this stays out of free-only mode. Set the real "
            "model id in config/providers.json before enabling."
        ),
    ),
    _spec(
        provider="mock",
        model="mock-video",
        label="Local mock (testing only)",
        durations=[4, 5, 6, 8, 10, 12],
        resolutions=["480p", "720p"],
        aspect_ratios=["16:9", "9:16", "1:1"],
        text_to_video=True,
        image_to_video=True,
        reference_frame=True,
        native_audio=False,
        quality=1,
        speed=1,
        free_kind="recurring",
        min_interval_seconds=0.0,
        enabled=False,
        docs_url="",
        verified_at="2026-09-22",
        notes=(
            "Deterministic offline provider used by the automated tests and the "
            "end-to-end smoke run. It produces labelled colour clips, never AI footage."
        ),
    ),
]


class CapabilityRegistry:
    """Queryable view over the model catalogue."""

    def __init__(self, models: Iterable[ModelSpec]) -> None:
        self._models: dict[str, ModelSpec] = {spec.key: spec for spec in models}

    # -- construction ----------------------------------------------------
    @classmethod
    def default(cls, settings: Settings | None = None) -> "CapabilityRegistry":
        models = [spec.model_copy(deep=True) for spec in CATALOG]
        if settings is not None:
            for spec in models:
                if spec.provider == "mock":
                    import os

                    spec.enabled = os.environ.get("OMNIREEL_ENABLE_MOCK", "").strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                    }
            models = apply_overrides(models, settings)
        return cls(models)

    # -- reads -----------------------------------------------------------
    def all(self) -> list[ModelSpec]:
        return list(self._models.values())

    def enabled(self) -> list[ModelSpec]:
        return [spec for spec in self._models.values() if spec.enabled]

    def get(self, provider: str, model: str) -> ModelSpec | None:
        return self._models.get(f"{provider}:{model}")

    def for_provider(self, provider: str) -> list[ModelSpec]:
        return [spec for spec in self._models.values() if spec.provider == provider]

    def providers(self) -> list[str]:
        seen: list[str] = []
        for spec in self._models.values():
            if spec.provider not in seen:
                seen.append(spec.provider)
        return seen

    def replace(self, spec: ModelSpec) -> None:
        self._models[spec.key] = spec

    def durations_ladder(
        self,
        *,
        free_only: bool = True,
        allowed_resolutions: Iterable[str] | None = None,
    ) -> list[float]:
        """Every clip length the enabled, eligible models can generate, longest first."""
        ladder: set[float] = set()
        for spec in self.enabled():
            if free_only and not spec.free_eligible:
                continue
            if allowed_resolutions is not None:
                wanted = set(allowed_resolutions)
                if not (set(spec.resolutions) & wanted):
                    continue
            ladder.update(float(value) for value in spec.durations)
        return sorted(ladder, reverse=True)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Invalid provider config at {path}: {exc}") from exc


def apply_overrides(models: list[ModelSpec], settings: Settings) -> list[ModelSpec]:
    """Merge ``config/providers.json`` over the built-in catalogue.

    Supported keys::

        {
          "disable": ["runware:video-default"],
          "enable":  ["agnes:agnes-video-2.5"],
          "models":  [{"provider": "agnes", "model": "...", "durations": [6, 10]}]
        }
    """
    from .config import REPO_ROOT

    path = settings.extra_config_path or (REPO_ROOT / "config" / "providers.json")
    data = _load_json(Path(path))
    if not data:
        return models

    by_key = {spec.key: spec for spec in models}

    for entry in data.get("models", []) or []:
        try:
            spec = ModelSpec(**entry)
        except ValidationError as exc:
            raise SystemExit(f"Invalid model entry in {path}: {exc}") from exc
        existing = by_key.get(spec.key)
        if existing is None:
            by_key[spec.key] = spec
        else:
            merged = existing.model_dump()
            merged.update({k: v for k, v in entry.items() if k in ModelSpec.model_fields})
            by_key[spec.key] = ModelSpec(**merged)

    for key in data.get("disable", []) or []:
        if key in by_key:
            by_key[key].enabled = False

    for key in data.get("enable", []) or []:
        if key in by_key:
            by_key[key].enabled = True

    return list(by_key.values())
