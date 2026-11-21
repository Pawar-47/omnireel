"""Scheduler: distribution, ranking, failover and manual pinning."""

from __future__ import annotations

import asyncio

from conftest import create_running_project, make_spec
from omnireel import store
from omnireel.providers.base import ProviderError


def test_scene_is_assigned_to_an_eligible_model(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    candidates = app_ctx.scheduler.rank(project, scene)
    assert candidates, "expected at least one candidate"
    assert candidates[0].spec.provider in {"mock", "mock2"}


def test_preview_distributes_across_providers(app_ctx):
    project, _ = create_running_project(app_ctx, target=60)
    scenes = store.scenes_for(app_ctx.db, project["id"])
    assignments = app_ctx.scheduler.preview_assignments(project, scenes)
    used = {entry["provider"] for entry in assignments.values() if entry["provider"]}
    assert used == {"mock", "mock2"}


def test_free_only_skips_paid_models(settings, app_ctx):
    spec = make_spec("mock", model="paid-model", free_kind="none")
    app_ctx.registry.replace(spec)
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    ranked = app_ctx.scheduler.rank(project, scene)
    assert all(candidate.spec.free_kind != "none" for candidate in ranked)


def test_unconfigured_provider_is_never_ranked(app_ctx):
    app_ctx.providers["mock2"].requires_key = True
    app_ctx.providers["mock"].settings.keys = {"mock": ["test-key"], "mock2": []}
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    ranked = app_ctx.scheduler.rank(project, scene)
    assert ranked, "the configured provider must still be ranked"
    assert all(candidate.provider != "mock2" for candidate in ranked)


def test_failover_moves_the_scene_to_another_provider(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    app_ctx.providers["mock"].fail_next = ["transient"]

    result = asyncio.run(app_ctx.scheduler.start_scene(project, scene))

    assert result["provider"] == "mock2"
    refreshed = store.get_scene(app_ctx.db, project["id"], scene["id"])
    assert refreshed["provider"] == "mock2"
    assert refreshed["status"] == "polling"
    attempts = app_ctx.scheduler.scene_attempts(scene["id"])
    assert any(row["status"] == "failed" for row in attempts)


def test_every_provider_failing_raises_no_candidate(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    app_ctx.providers["mock"].fail_next = ["transient"]
    app_ctx.providers["mock2"].fail_next = ["transient"]

    try:
        asyncio.run(app_ctx.scheduler.start_scene(project, scene))
    except Exception as exc:  # NoCandidateError
        assert "no eligible" in str(exc).lower() or "mock failure" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("expected dispatch to fail when every provider errors")


def test_quota_exhaustion_skips_that_model_but_not_the_others(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    spec = app_ctx.registry.get("mock", "mock-video")
    assert spec is not None
    app_ctx.ledger.record_error(
        spec, "mock:none", kind="quota", message="daily cap", retry_after=3600
    )
    ranked = app_ctx.scheduler.rank(project, scene)
    assert all(candidate.provider != "mock" for candidate in ranked)


def test_manual_pin_is_honoured_first(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    store.update_scene(
        app_ctx.db, scene["id"], provider="mock2", model="mock-video", status="planned"
    )
    pinned = store.get_scene(app_ctx.db, project["id"], scene["id"])
    ranked = app_ctx.scheduler.rank(project, pinned)
    assert ranked[0].provider == "mock2"


def test_duration_snapping_when_no_exact_match_exists(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    scene["duration_seconds"] = 11.0
    ranked = app_ctx.scheduler.rank(project, scene)
    assert ranked, "11s should snap to a 10s or 12s capable model"
    assert abs(ranked[0].duration - 11.0) <= 2.5


def test_start_scene_stores_the_job_and_remote_id(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    result = asyncio.run(app_ctx.scheduler.start_scene(project, scene))
    assert result["started"] is True
    refreshed = store.get_scene(app_ctx.db, project["id"], scene["id"])
    assert refreshed["remote_job_id"]
    job = app_ctx.db.one("SELECT * FROM jobs WHERE scene_id=?", (scene["id"],))
    assert job is not None
    assert job["status"] in {"submitted", "succeeded"}



def test_paid_models_stay_excluded_until_the_operator_opts_in(app_ctx):
    """The safety invariant: no paid model may run without an explicit opt-in."""
    app_ctx.registry.replace(
        make_spec("mock", model="paid-only", free_kind="none", quality=5, speed=5)
    )

    # Operator has not enabled paid usage, and the project did not ask for free-only.
    app_ctx.settings.allow_paid = False
    project, _ = create_running_project(app_ctx, target=40, free_only=False)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    ranked = app_ctx.scheduler.rank(project, scene)
    assert ranked, "free models must still be ranked"
    assert all(candidate.spec.free_kind != "none" for candidate in ranked)

    # Even with paid usage allowed, a free-only project stays free-only.
    app_ctx.settings.allow_paid = True
    still_free, _ = create_running_project(app_ctx, target=40, free_only=True)
    scene = store.scenes_for(app_ctx.db, still_free["id"])[0]
    ranked = app_ctx.scheduler.rank(still_free, scene)
    assert all(candidate.spec.free_kind != "none" for candidate in ranked)

    # Only an explicitly paid project may reach it.
    paid, _ = create_running_project(app_ctx, target=40, free_only=False)
    scene = store.scenes_for(app_ctx.db, paid["id"])[0]
    ranked = app_ctx.scheduler.rank(paid, scene)
    assert any(candidate.spec.free_kind == "none" for candidate in ranked)
