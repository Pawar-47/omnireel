"""Project persistence and serialisation.

Kept separate from the API layer so the worker, the CLI and the tests all speak
the same vocabulary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database, dumps, loads
from .planner import Storyboard

PROJECT_FIELDS = (
    "id",
    "created_at",
    "updated_at",
    "title",
    "prompt",
    "style",
    "aspect_ratio",
    "resolution",
    "target_seconds",
    "mode",
    "free_only",
    "preferred_provider",
    "status",
    "status_message",
    "final_video_path",
    "final_duration",
    "next_retry_at",
)

SCENE_FIELDS = (
    "id",
    "index_no",
    "duration_seconds",
    "actual_duration",
    "description",
    "prompt",
    "camera",
    "subject_motion",
    "lighting",
    "environment",
    "continuity",
    "status",
    "provider",
    "model",
    "clip_path",
    "remote_job_id",
    "remote_url",
    "reference_image_path",
    "attempts",
    "last_error",
    "updated_at",
)

DEFERRED_STATUSES = ("planned", "failed")
LIVE_STATUSES = ("queued", "dispatched", "polling", "downloading")
FINAL_STATUSES = ("completed", "failed", "cancelled")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def default_settings() -> dict:
    return {
        "continuity": True,
        "crossfade": False,
        "crossfade_seconds": 0.5,
        "keep_scene_audio": True,
        "music_path": None,
        "narration_script": None,
        "music_volume": 0.25,
        "fps": 24,
        "crf": 20,
        "preset": "veryfast",
    }


def create_project(
    db: Database,
    *,
    storyboard: Storyboard,
    aspect_ratio: str,
    resolution: str,
    mode: str,
    free_only: bool,
    preferred_provider: str | None,
    settings: dict | None = None,
    status: str = "planned",
) -> dict:
    project_id = new_id()
    timestamp = now_iso()
    options = {**default_settings(), **(settings or {})}
    db.execute(
        """
        INSERT INTO projects(id, created_at, updated_at, title, prompt, style, aspect_ratio,
            resolution, target_seconds, mode, free_only, preferred_provider, status,
            world_bible, settings, status_message)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            project_id,
            timestamp,
            timestamp,
            storyboard.title,
            storyboard.prompt,
            storyboard.style,
            aspect_ratio,
            resolution,
            float(storyboard.target_seconds),
            mode,
            1 if free_only else 0,
            preferred_provider,
            status,
            dumps(storyboard.world_bible),
            dumps(options),
            "storyboard ready" if status == "ready" else "storyboard drafted",
        ),
    )
    for shot in storyboard.shots:
        db.execute(
            """
            INSERT INTO scenes(id, project_id, index_no, duration_seconds, description, prompt,
                camera, subject_motion, lighting, environment, continuity, status, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                new_id(),
                project_id,
                shot.index,
                float(shot.duration),
                shot.description,
                shot.prompt,
                shot.camera,
                shot.subject_motion,
                shot.lighting,
                shot.environment,
                shot.continuity,
                "planned",
                timestamp,
            ),
        )
    project = get_project(db, project_id)
    assert project is not None
    return project


def get_project(db: Database, project_id: str) -> dict | None:
    return db.one("SELECT * FROM projects WHERE id=?", (project_id,))


def list_projects(db: Database, limit: int = 100) -> list[dict]:
    return db.query(
        "SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
    )


def scenes_for(db: Database, project_id: str) -> list[dict]:
    return db.query(
        "SELECT * FROM scenes WHERE project_id=? ORDER BY index_no", (project_id,)
    )


def get_scene(db: Database, project_id: str, scene_id: str) -> dict | None:
    return db.one(
        "SELECT * FROM scenes WHERE project_id=? AND id=?", (project_id, scene_id)
    )


def set_project_status(
    db: Database, project_id: str, status: str, message: str | None = None
) -> None:
    db.execute(
        "UPDATE projects SET status=?, status_message=?, updated_at=? WHERE id=?",
        (status, message, now_iso(), project_id),
    )


def touch_project(db: Database, project_id: str) -> None:
    db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))


def active_projects(db: Database) -> list[dict]:
    return db.query(
        "SELECT * FROM projects WHERE status IN ('running','waiting','assembling') "
        "ORDER BY updated_at LIMIT 20"
    )


def next_scene_to_generate(db: Database, project_id: str) -> dict | None:
    return db.one(
        """
        SELECT * FROM scenes
         WHERE project_id=? AND status IN ('planned','failed')
         ORDER BY index_no LIMIT 1
        """,
        (project_id,),
    )


def live_scenes(db: Database, project_id: str) -> list[dict]:
    placeholders = ",".join("?" for _ in LIVE_STATUSES)
    return db.query(
        f"SELECT * FROM scenes WHERE project_id=? AND status IN ({placeholders}) "
        "ORDER BY index_no",
        (project_id, *LIVE_STATUSES),
    )


def update_scene(db: Database, scene_id: str, **fields: Any) -> None:
    allowed = {
        "duration_seconds",
        "description",
        "prompt",
        "camera",
        "subject_motion",
        "lighting",
        "environment",
        "continuity",
        "status",
        "provider",
        "model",
        "clip_path",
        "remote_job_id",
        "remote_url",
        "reference_image_path",
        "last_error",
        "actual_duration",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key}=?" for key in updates)
    db.execute(
        f"UPDATE scenes SET {assignments} WHERE id=?",
        (*updates.values(), scene_id),
    )


def project_progress(db: Database, project_id: str) -> dict:
    rows = scenes_for(db, project_id)
    ready = [row for row in rows if row["status"] == "ready"]
    planned = round(sum(float(row["duration_seconds"] or 0) for row in rows), 2)
    generated = round(
        sum(float(row["actual_duration"] or row["duration_seconds"] or 0) for row in ready), 2
    )
    providers: dict[str, int] = {}
    for row in ready:
        if row.get("provider"):
            providers[row["provider"]] = providers.get(row["provider"], 0) + 1
    return {
        "scenes_total": len(rows),
        "scenes_ready": len(ready),
        "scenes_pending": len(rows) - len(ready),
        "planned_seconds": planned,
        "generated_seconds": generated,
        "remaining_seconds": round(max(0.0, planned - generated), 2),
        "completion": round(len(ready) / len(rows), 3) if rows else 0.0,
        "duration_completion": round(min(1.0, generated / planned), 3) if planned else 0.0,
        "providers_used": providers,
    }


def _clip_url(project_id: str, scene: dict) -> str | None:
    if scene.get("status") != "ready" or not scene.get("clip_path"):
        return None
    return f"/api/projects/{project_id}/scenes/{scene['id']}/clip"


def serialise_scene(db: Database, project_id: str, scene: dict) -> dict:
    data = {key: scene.get(key) for key in SCENE_FIELDS}
    data["id"] = scene["id"]
    data["index"] = int(scene.get("index_no") or 0)
    data["duration_seconds"] = float(scene.get("duration_seconds") or 0)
    data["attempts"] = int(scene.get("attempts") or 0)
    data["clip_url"] = _clip_url(project_id, scene)
    data["attempts_log"] = db.query(
        "SELECT provider, model, status, error, started_at, finished_at FROM scene_attempts "
        "WHERE scene_id=? ORDER BY id DESC LIMIT 8",
        (scene["id"],),
    )
    return data


def serialise_project(db: Database, project: dict, *, include_scenes: bool = True) -> dict:
    data = {key: project.get(key) for key in PROJECT_FIELDS}
    data["target_seconds"] = float(project.get("target_seconds") or 0)
    data["free_only"] = bool(project.get("free_only"))
    data["world_bible"] = loads(project.get("world_bible"), {})
    data["settings"] = loads(project.get("settings"), {})
    data["progress"] = project_progress(db, project["id"])
    data["video_url"] = (
        f"/api/projects/{project['id']}/video" if project.get("final_video_path") else None
    )
    if include_scenes:
        scenes = scenes_for(db, project["id"])
        data["scenes"] = [serialise_scene(db, project["id"], scene) for scene in scenes]
    return data


def delete_project(db: Database, project_id: str) -> None:
    db.execute("DELETE FROM scene_attempts WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM jobs WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM projects WHERE id=?", (project_id,))


def project_clip_paths(db: Database, project_id: str) -> list[Path]:
    paths: list[Path] = []
    for scene in scenes_for(db, project_id):
        if scene.get("clip_path"):
            paths.append(Path(scene["clip_path"]))
    return paths
