"""HTTP API.

The API is intentionally small: create a storyboard, review and edit it, start
generation, watch progress, regenerate single scenes, assemble, download.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, store
from .app import Application
from .config import (
    MAX_TARGET_SECONDS,
    MIN_TARGET_SECONDS,
    Settings,
)
from .planner import STYLE_PROFILES, plan_storyboard
from .scheduler import MODES
from .audio import available_engines

logger = logging.getLogger("omnireel.api")

ASPECT_RATIOS = ["16:9", "9:16", "1:1"]
RESOLUTIONS = ["720p", "1080p", "480p"]


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    target_seconds: float = 120.0
    style: str = "cinematic"
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    mode: str = "free_max"
    free_only: bool = True
    preferred_provider: str | None = None
    continuity: bool = True
    crossfade: bool = False
    keep_scene_audio: bool = True
    music_path: str | None = None
    narration_script: str | None = None


class ScenePatch(BaseModel):
    prompt: str | None = Field(default=None, max_length=4000)
    description: str | None = None
    duration_seconds: float | None = Field(default=None, ge=1.0, le=30.0)
    camera: str | None = None
    subject_motion: str | None = None
    lighting: str | None = None
    environment: str | None = None
    continuity: str | None = None


class AssignRequest(BaseModel):
    provider: str
    model: str


class SettingsPatch(BaseModel):
    continuity: bool | None = None
    crossfade: bool | None = None
    crossfade_seconds: float | None = Field(default=None, ge=0.1, le=3.0)
    keep_scene_audio: bool | None = None
    music_path: str | None = None
    narration_script: str | None = None
    music_volume: float | None = Field(default=None, ge=0.0, le=1.5)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _omni(request: Request) -> Application:
    return request.app.state.omni


def _get_project(app: Application, project_id: str) -> dict:
    project = store.get_project(app.db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _validate_target(value: float) -> float:
    if not (MIN_TARGET_SECONDS <= value <= MAX_TARGET_SECONDS):
        raise HTTPException(
            status_code=422,
            detail=(
                f"target_seconds must be between {MIN_TARGET_SECONDS} and "
                f"{MAX_TARGET_SECONDS} seconds"
            ),
        )
    return round(float(value), 2)


def _clip_response(path: str) -> FileResponse:
    candidate = Path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="file is no longer on disk")
    return FileResponse(candidate, media_type="video/mp4", filename=candidate.name)


# --------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    omni = Application.build(settings)

    app = FastAPI(
        title="OmniReel",
        version=__version__,
        description=(
            "Multi-provider AI video studio: one prompt in, one assembled ~120 second film out."
        ),
    )
    app.state.omni = omni

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        omni.worker.start()  # type: ignore[union-attr]
        logger.info(
            "OmniReel started - providers configured: %s",
            ", ".join(omni.configured_provider_ids()) or "none",
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await omni.aclose()

    # -- meta -----------------------------------------------------------
    @app.get("/api/health")
    def health(request: Request) -> dict:
        app_ctx = _omni(request)
        counts = app_ctx.db.one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed "
            "FROM projects"
        ) or {}
        return {
            "status": "ok",
            "version": __version__,
            "ffmpeg": app_ctx.media.available,
            "free_only": app_ctx.settings.free_only,
            "allow_paid": app_ctx.settings.allow_paid,
            "providers_configured": app_ctx.configured_provider_ids(),
            "projects": int(counts.get("total") or 0),
            "completed": int(counts.get("completed") or 0),
            "narration_engines": available_engines(),
            "data_dir": str(app_ctx.settings.data_dir),
        }

    @app.get("/api/config")
    def config(request: Request) -> dict:
        app_ctx = _omni(request)
        return {
            "version": __version__,
            "styles": [
                {"id": key, "label": value["label"]} for key, value in STYLE_PROFILES.items()
            ],
            "modes": list(MODES),
            "aspect_ratios": ASPECT_RATIOS,
            "resolutions": RESOLUTIONS,
            "min_target_seconds": MIN_TARGET_SECONDS,
            "max_target_seconds": MAX_TARGET_SECONDS,
            "default_target_seconds": app_ctx.settings.default_target_seconds,
            "free_only": app_ctx.settings.free_only,
            "allow_paid": app_ctx.settings.allow_paid,
            "ffmpeg": app_ctx.media.available,
            "narration_engines": available_engines(),
            "providers_configured": app_ctx.configured_provider_ids(),
            "durations_ladder": app_ctx.registry.durations_ladder(
                free_only=app_ctx.settings.free_only
            ),
        }

    # -- providers ------------------------------------------------------
    @app.get("/api/providers")
    def providers(request: Request) -> dict:
        app_ctx = _omni(request)
        return {
            "providers": app_ctx.scheduler.provider_status(),
            "usage": app_ctx.ledger.usage_summary(100),
            "free_only": app_ctx.settings.free_only,
            "allow_paid": app_ctx.settings.allow_paid,
        }

    @app.post("/api/providers/{provider_id}/reset")
    def reset_provider(provider_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        app_ctx.health.reset(provider_id)
        return {"provider": provider_id, "reset": True}

    # -- projects -------------------------------------------------------
    @app.post("/api/projects", status_code=201)
    def create_project(payload: CreateProjectRequest, request: Request) -> dict:
        app_ctx = _omni(request)
        target = _validate_target(payload.target_seconds)
        if payload.mode not in MODES:
            raise HTTPException(status_code=422, detail=f"unknown generation mode '{payload.mode}'")
        if payload.aspect_ratio not in ASPECT_RATIOS:
            raise HTTPException(status_code=422, detail="unsupported aspect ratio")
        # Paid generation is impossible unless the operator enabled it, and a project
        # that asks for free-only stays free-only even then.
        free_only = bool(payload.free_only) or not app_ctx.settings.allow_paid

        storyboard = plan_storyboard(
            payload.prompt,
            target_seconds=target,
            style=payload.style,
            aspect_ratio=payload.aspect_ratio,
            resolution=payload.resolution,
            registry=app_ctx.registry,
            free_only=free_only,
        )
        project = store.create_project(
            app_ctx.db,
            storyboard=storyboard,
            aspect_ratio=payload.aspect_ratio,
            resolution=payload.resolution,
            mode=payload.mode,
            free_only=free_only,
            preferred_provider=payload.preferred_provider,
            settings={
                "continuity": payload.continuity,
                "crossfade": payload.crossfade,
                "keep_scene_audio": payload.keep_scene_audio,
                "music_path": payload.music_path,
                "narration_script": payload.narration_script,
            },
            status="ready",
        )
        data = store.serialise_project(app_ctx.db, project)
        data["assignments"] = app_ctx.scheduler.preview_assignments(project, data["scenes"])
        return data

    @app.get("/api/projects")
    def list_projects(request: Request, limit: int = 50) -> dict:
        app_ctx = _omni(request)
        projects = store.list_projects(app_ctx.db, limit=max(1, min(limit, 200)))
        return {
            "projects": [store.serialise_project(app_ctx.db, row) for row in projects]
        }

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        data = store.serialise_project(app_ctx.db, project)
        if project["status"] in {"planned", "ready"}:
            data["assignments"] = app_ctx.scheduler.preview_assignments(project, data["scenes"])
        return data

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        _get_project(app_ctx, project_id)
        store.delete_project(app_ctx.db, project_id)
        return {"deleted": project_id}

    # -- storyboard -----------------------------------------------------
    @app.post("/api/projects/{project_id}/plan")
    def replan_project(project_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        scenes = store.scenes_for(app_ctx.db, project_id)
        if any(scene["status"] != "planned" for scene in scenes):
            raise HTTPException(
                status_code=409,
                detail="storyboard can only be re-planned before generation has started",
            )

        storyboard = plan_storyboard(
            project["prompt"],
            target_seconds=float(project["target_seconds"]),
            style=project["style"],
            aspect_ratio=project["aspect_ratio"],
            resolution=project["resolution"],
            registry=app_ctx.registry,
            free_only=bool(project["free_only"]),
        )
        app_ctx.db.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
        for shot in storyboard.shots:
            app_ctx.db.execute(
                """
                INSERT INTO scenes(id, project_id, index_no, duration_seconds, description,
                    prompt, camera, subject_motion, lighting, environment, continuity,
                    status, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    store.new_id(),
                    project_id,
                    shot.index,
                    shot.duration,
                    shot.description,
                    shot.prompt,
                    shot.camera,
                    shot.subject_motion,
                    shot.lighting,
                    shot.environment,
                    shot.continuity,
                    "planned",
                    store.now_iso(),
                ),
            )
        store.set_project_status(app_ctx.db, project_id, "ready", "storyboard re-planned")
        refreshed = _get_project(app_ctx, project_id)
        data = store.serialise_project(app_ctx.db, refreshed)
        data["assignments"] = app_ctx.scheduler.preview_assignments(
            refreshed, data["scenes"]
        )
        return data

    @app.patch("/api/projects/{project_id}/scenes/{scene_id}")
    def patch_scene(
        project_id: str, scene_id: str, payload: ScenePatch, request: Request
    ) -> dict:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        scene = store.get_scene(app_ctx.db, project_id, scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="scene not found")
        if scene["status"] == "ready":
            raise HTTPException(
                status_code=409,
                detail="this scene is already generated - use regenerate to change it",
            )
        updates = payload.model_dump(exclude_none=True)
        store.update_scene(app_ctx.db, scene_id, **updates)
        store.touch_project(app_ctx.db, project_id)
        refreshed = _get_project(app_ctx, project_id)
        return store.serialise_project(app_ctx.db, refreshed)

    @app.post("/api/projects/{project_id}/settings")
    def patch_settings(project_id: str, payload: SettingsPatch, request: Request) -> dict:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        from .db import dumps, loads

        options = loads(project.get("settings"), {})
        options.update({key: value for key, value in payload.model_dump().items() if value is not None})
        app_ctx.db.execute(
            "UPDATE projects SET settings=?, updated_at=? WHERE id=?",
            (dumps(options), store.now_iso(), project_id),
        )
        return store.serialise_project(app_ctx.db, _get_project(app_ctx, project_id))

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/assign")
    def assign_scene(
        project_id: str, scene_id: str, payload: AssignRequest, request: Request
    ) -> dict:
        app_ctx = _omni(request)
        _get_project(app_ctx, project_id)
        scene = store.get_scene(app_ctx.db, project_id, scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="scene not found")
        if scene["status"] == "ready":
            raise HTTPException(
                status_code=409, detail="scene already generated - regenerate it instead"
            )
        spec = app_ctx.registry.get(payload.provider, payload.model)
        if spec is None:
            raise HTTPException(status_code=404, detail="unknown model for this provider")
        store.update_scene(
            app_ctx.db,
            scene_id,
            provider=payload.provider,
            model=payload.model,
            status="planned",
        )
        store.touch_project(app_ctx.db, project_id)
        return store.serialise_project(app_ctx.db, _get_project(app_ctx, project_id))

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/regenerate")
    def regenerate_scene(project_id: str, scene_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        scene = store.get_scene(app_ctx.db, project_id, scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="scene not found")
        app_ctx.scheduler.reset_scene(scene)
        app_ctx.db.execute(
            "UPDATE projects SET status='running', status_message=?, final_video_path=NULL, "
            "final_duration=NULL, updated_at=? WHERE id=? AND status='completed'",
            ("regenerating one scene", store.now_iso(), project_id),
        )
        if project["status"] in {"completed", "failed", "waiting", "paused"}:
            store.set_project_status(
                app_ctx.db, project_id, "running", "regenerating one scene"
            )
        app_ctx.worker.wake()  # type: ignore[union-attr]
        return store.serialise_project(app_ctx.db, _get_project(app_ctx, project_id))

    # -- generation control ---------------------------------------------
    @app.post("/api/projects/{project_id}/start")
    def start_project(project_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        if project["status"] == "completed":
            raise HTTPException(status_code=409, detail="project is already finished")
        scenes = store.scenes_for(app_ctx.db, project_id)
        if not scenes:
            raise HTTPException(status_code=409, detail="project has no scenes")
        store.set_project_status(app_ctx.db, project_id, "running", "generation started")
        app_ctx.db.execute(
            "UPDATE projects SET next_retry_at=NULL WHERE id=?", (project_id,)
        )
        app_ctx.worker.wake()  # type: ignore[union-attr]
        return store.serialise_project(app_ctx.db, _get_project(app_ctx, project_id))

    @app.post("/api/projects/{project_id}/pause")
    def pause_project(project_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        _get_project(app_ctx, project_id)
        store.set_project_status(app_ctx.db, project_id, "paused", "paused by user")
        return store.serialise_project(app_ctx.db, _get_project(app_ctx, project_id))

    @app.post("/api/projects/{project_id}/assemble")
    def assemble_now(project_id: str, request: Request) -> dict:
        app_ctx = _omni(request)
        _get_project(app_ctx, project_id)
        scenes = store.scenes_for(app_ctx.db, project_id)
        unfinished = [scene for scene in scenes if scene["status"] != "ready"]
        if unfinished:
            raise HTTPException(
                status_code=409,
                detail=f"{len(unfinished)} scene(s) are still missing; nothing to assemble yet",
            )
        store.set_project_status(app_ctx.db, project_id, "assembling", "manual assembly")
        app_ctx.worker.wake()  # type: ignore[union-attr]
        return {"status": "assembling", "scenes": len(scenes)}

    # -- downloads ------------------------------------------------------
    @app.get("/api/projects/{project_id}/video")
    def download_video(project_id: str, request: Request) -> FileResponse:
        app_ctx = _omni(request)
        project = _get_project(app_ctx, project_id)
        if not project.get("final_video_path"):
            raise HTTPException(status_code=404, detail="no finished video yet")
        return _clip_response(project["final_video_path"])

    @app.get("/api/projects/{project_id}/scenes/{scene_id}/clip")
    def download_clip(project_id: str, scene_id: str, request: Request) -> FileResponse:
        app_ctx = _omni(request)
        scene = store.get_scene(app_ctx.db, project_id, scene_id)
        if scene is None or not scene.get("clip_path"):
            raise HTTPException(status_code=404, detail="no clip for this scene yet")
        return _clip_response(scene["clip_path"])

    @app.get("/api/library")
    def library(request: Request, limit: int = 50) -> dict:
        app_ctx = _omni(request)
        rows = app_ctx.db.query(
            "SELECT * FROM projects WHERE final_video_path IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
        return {
            "items": [
                {
                    **{key: row.get(key) for key in store.PROJECT_FIELDS},
                    "progress": store.project_progress(app_ctx.db, row["id"]),
                    "video_url": f"/api/projects/{row['id']}/video",
                }
                for row in rows
            ]
        }

    # -- static frontend ------------------------------------------------
    dist = settings.frontend_dist
    if dist.is_dir() and (dist / "index.html").exists():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Any:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(dist.resolve())):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


__all__ = ["create_app"]
