"""HTTP API smoke tests (no network, no keys)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from omnireel.api import create_app
from omnireel.config import Settings


def build_client(tmp_path, monkeypatch) -> tuple[TestClient, object]:
    monkeypatch.setenv("OMNIREEL_ENABLE_MOCK", "1")
    monkeypatch.delenv("OMNIREEL_ALLOW_PAID", raising=False)
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    settings.poll_interval_seconds = 0.0
    settings.allow_paid = False
    settings.free_only = True
    app = create_app(settings)
    # No context manager: startup does not run, so the test drives the worker itself.
    return TestClient(app), app


def test_health_and_config(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    config = client.get("/api/config").json()
    assert config["min_target_seconds"] <= 120 <= config["max_target_seconds"]
    assert config["modes"]
    assert config["styles"]
    assert config["durations_ladder"], "at least one clip length must be available"


def test_create_project_plans_two_minutes(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/projects",
        json={
            "prompt": "A cinematic documentary about exploring a mysterious tropical island",
            "target_seconds": 120,
            "style": "cinematic",
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "mode": "free_max",
        },
    )
    assert response.status_code == 201
    project = response.json()
    scenes = project["scenes"]
    assert len(scenes) >= 10
    total = sum(scene["duration_seconds"] for scene in scenes)
    assert abs(total - 120) < 0.5
    assert project["status"] == "ready"
    assert project["assignments"], "the API must show a provisional provider plan"
    assert all(4 <= scene["duration_seconds"] <= 12 for scene in scenes)


def test_target_duration_is_validated(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/projects", json={"prompt": "too short", "target_seconds": 1}
    )
    assert response.status_code == 422


def test_full_generation_flow_with_the_mock_provider(tmp_path, monkeypatch):
    client, app = build_client(tmp_path, monkeypatch)

    created = client.post(
        "/api/projects",
        json={"prompt": "a lighthouse in a storm", "target_seconds": 30},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    started = client.post(f"/api/projects/{project_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    omni = app.state.omni
    for _ in range(40):
        asyncio.run(omni.worker.tick())

    project = client.get(f"/api/projects/{project_id}").json()
    assert project["progress"]["scenes_ready"] == project["progress"]["scenes_total"]
    assert project["progress"]["generated_seconds"] > 0
    # Assembly needs real FFmpeg output; either it finished or it reported why.
    assert project["status"] in {"completed", "failed", "assembling"}
    if project["status"] == "failed":
        assert "assembly failed" in project["status_message"]


def test_pause_and_resume(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    project_id = client.post(
        "/api/projects", json={"prompt": "a slow river", "target_seconds": 30}
    ).json()["id"]

    client.post(f"/api/projects/{project_id}/start")
    paused = client.post(f"/api/projects/{project_id}/pause").json()
    assert paused["status"] == "paused"
    resumed = client.post(f"/api/projects/{project_id}/start").json()
    assert resumed["status"] == "running"


def test_scene_prompt_can_be_edited_before_generation(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"prompt": "an ancient library", "target_seconds": 30}
    ).json()
    scene = project["scenes"][0]

    response = client.patch(
        f"/api/projects/{project['id']}/scenes/{scene['id']}",
        json={"prompt": "A slow push through an ancient library at dawn."},
    )
    assert response.status_code == 200
    edited = response.json()["scenes"][0]
    assert edited["prompt"].startswith("A slow push through")


def test_provider_dashboard_lists_every_provider(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    payload = client.get("/api/providers").json()
    providers = {entry["provider"]: entry for entry in payload["providers"]}
    assert "agnes" in providers
    assert providers["agnes"]["configured"] is False
    assert providers["mock"]["configured"] is True
    assert any(model["model"] == "agnes-video-2.5-flash" for model in providers["agnes"]["models"])


def test_library_starts_empty(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    assert client.get("/api/library").json()["items"] == []
