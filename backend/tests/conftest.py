"""Shared test fixtures.

Nothing here touches the network. The mock provider stands in for every cloud
back end, so the orchestration path (planning, scheduling, failover, quota,
recovery, assembly planning) runs end to end in milliseconds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omnireel import store  # noqa: E402
from omnireel.app import Application  # noqa: E402
from omnireel.assembly import Assembler  # noqa: E402
from omnireel.capabilities import CapabilityRegistry, ModelSpec  # noqa: E402
from omnireel.config import Settings  # noqa: E402
from omnireel.continuity import MediaTools  # noqa: E402
from omnireel.db import Database  # noqa: E402
from omnireel.planner import plan_storyboard  # noqa: E402
from omnireel.providers.mock import MockProvider  # noqa: E402
from omnireel.quota import ProviderHealth, QuotaLedger  # noqa: E402
from omnireel.scheduler import Scheduler  # noqa: E402
from omnireel.worker import GenerationWorker  # noqa: E402


class SecondMockProvider(MockProvider):
    """A second independent 'provider' so distribution and failover are testable."""

    id = "mock2"
    label = "Mock 2"


def make_spec(provider: str, **overrides) -> ModelSpec:
    data = {
        "provider": provider,
        "model": "mock-video",
        "label": f"{provider} mock",
        "durations": [12, 10, 8, 6, 5, 4],
        "free_kind": "recurring",
        "min_interval_seconds": 0.0,
        "quality": 3,
        "speed": 3,
        "enabled": True,
        "verified_at": "test",
    }
    data.update(overrides)
    return ModelSpec(**data)


@pytest.fixture
def settings(tmp_path) -> Settings:
    instance = Settings(data_dir=tmp_path / "data")
    instance.ensure_dirs()
    instance.poll_interval_seconds = 0.0
    instance.max_concurrent_generations = 2
    instance.allow_paid = False
    instance.free_only = True
    return instance


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings.db_path)
    database.init()
    return database


@pytest.fixture
def app_ctx(settings, db) -> Application:
    registry = CapabilityRegistry([make_spec("mock"), make_spec("mock2")])
    providers = {"mock": MockProvider(settings), "mock2": SecondMockProvider(settings)}
    ledger = QuotaLedger(db, settings)
    health = ProviderHealth(db)
    media = MediaTools(settings)
    scheduler = Scheduler(db, settings, registry, providers, ledger, health, media)
    assembler = Assembler(db, settings, media)
    application = Application(
        settings=settings,
        db=db,
        registry=registry,
        providers=providers,
        ledger=ledger,
        health=health,
        media=media,
        scheduler=scheduler,
        assembler=assembler,
    )
    application.worker = GenerationWorker(application)
    return application


def create_running_project(
    app: Application,
    *,
    prompt: str = "a cinematic documentary about a mysterious tropical island",
    target: float = 40.0,
    status: str = "running",
    free_only: bool = True,
    mode: str = "free_max",
) -> tuple[dict, list]:
    storyboard = plan_storyboard(
        prompt,
        target_seconds=target,
        registry=app.registry,
        free_only=free_only,
    )
    project = store.create_project(
        app.db,
        storyboard=storyboard,
        aspect_ratio="16:9",
        resolution="720p",
        mode=mode,
        free_only=free_only,
        preferred_provider=None,
        status=status,
    )
    return project, storyboard.shots
