"""Worker recovery behaviour: nothing finished is ever thrown away."""

from __future__ import annotations

import asyncio

import pytest

from conftest import create_running_project
from omnireel import store
from omnireel.assembly import AssemblyError


class FailingAssembler:
    """Stands in for a machine where FFmpeg has a problem."""

    def __init__(self) -> None:
        self.calls = 0

    async def assemble(self, project, scenes):  # noqa: ANN001
        self.calls += 1
        raise AssemblyError("ffmpeg exploded")


def run_ticks(app, count: int) -> None:
    async def runner() -> None:
        for _ in range(count):
            await app.worker.tick()
            await asyncio.sleep(0)

    asyncio.run(runner())


def test_worker_generates_every_scene_then_keeps_them_when_assembly_fails(app_ctx):
    app_ctx.assembler = FailingAssembler()
    project, _ = create_running_project(app_ctx, target=40)

    run_ticks(app_ctx, 60)

    scenes = store.scenes_for(app_ctx.db, project["id"])
    assert scenes
    assert all(scene["status"] == "ready" for scene in scenes)
    assert all(scene["clip_path"] for scene in scenes)

    refreshed = store.get_project(app_ctx.db, project["id"])
    assert refreshed["status"] == "failed"
    assert "assembly failed" in (refreshed["status_message"] or "")
    # The footage survives the failure and can be re-assembled later.
    assert refreshed["final_video_path"] is None


def test_waiting_project_recovers_when_a_provider_returns(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]

    # Make both providers unavailable: the project must park, not fail.
    app_ctx.db.execute("DELETE FROM scenes WHERE project_id<>?", (project["id"],))
    app_ctx.health.record_failure("mock", "down", kind="unavailable")
    app_ctx.health.record_failure("mock2", "down", kind="unavailable")

    run_ticks(app_ctx, 3)
    parked = store.get_project(app_ctx.db, project["id"])
    assert parked["status"] == "waiting"
    assert parked["next_retry_at"] is not None

    # Providers recover; the worker picks the project back up.
    app_ctx.health.record_success("mock")
    app_ctx.health.record_success("mock2")
    app_ctx.db.execute("UPDATE projects SET next_retry_at=NULL WHERE id=?", (project["id"],))
    run_ticks(app_ctx, 2)
    resumed = store.get_project(app_ctx.db, project["id"])
    assert resumed["status"] in {"running", "assembling", "failed", "waiting"}
    assert store.get_scene(app_ctx.db, project["id"], scene["id"])["status"] != "planned"


def test_completed_scenes_are_never_regenerated(app_ctx):
    project, _ = create_running_project(app_ctx, target=40)
    scenes = store.scenes_for(app_ctx.db, project["id"])
    assert len(scenes) >= 4

    # Pretend the first two scenes already finished before a crash.
    keeper = []
    for scene in scenes[:2]:
        clip = app_ctx.settings.project_dir(project["id"]) / f"{scene['index_no']}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"already generated")
        store.update_scene(
            app_ctx.db,
            scene["id"],
            status="ready",
            provider="mock",
            model="mock-video",
            clip_path=str(clip),
            actual_duration=scene["duration_seconds"],
        )
        keeper.append(str(clip))

    app_ctx.assembler = FailingAssembler()
    run_ticks(app_ctx, 60)

    refreshed = [store.get_scene(app_ctx.db, project["id"], scene["id"]) for scene in scenes[:2]]
    assert [row["clip_path"] for row in refreshed] == keeper
    assert all(row["attempts"] == 0 for row in refreshed)


def test_paused_project_is_not_touched(app_ctx):
    project, _ = create_running_project(app_ctx, target=40, status="paused")
    run_ticks(app_ctx, 3)
    refreshed = store.get_project(app_ctx.db, project["id"])
    assert refreshed["status"] == "paused"
    assert all(
        scene["status"] == "planned" for scene in store.scenes_for(app_ctx.db, project["id"])
    )


def test_scene_attempt_cap_parks_it_instead_of_spinning(app_ctx):
    project, _ = create_running_project(app_ctx, target=20)
    scene = store.scenes_for(app_ctx.db, project["id"])[0]
    app_ctx.db.execute(
        "UPDATE scenes SET status='failed', attempts=99 WHERE id=?", (scene["id"],)
    )
    run_ticks(app_ctx, 3)
    refreshed = store.get_scene(app_ctx.db, project["id"], scene["id"])
    assert refreshed["status"] == "blocked"
    assert "stopped after" in (refreshed["last_error"] or "")
