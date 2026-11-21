"""Background generation worker.

One long-lived asyncio task advances every running project:

* polls in-flight remote jobs (throttled to ``poll_interval_seconds``);
* downloads finished clips and validates them;
* dispatches the next pending scene while concurrency slots are free;
* assembles the film as soon as every scene is ready;
* parks a project as ``waiting`` when no provider can serve it, and picks it up
  again automatically once quota or health recovers.

Because every step reads its state from SQLite, restarting the application
resumes exactly where it stopped: finished clips stay finished, remote job ids
are still polled, and nothing is regenerated for free.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import store
from .assembly import AssemblyError
from .quota import from_iso, utcnow
from .scheduler import NoCandidateError

if TYPE_CHECKING:  # pragma: no cover
    from .app import Application

logger = logging.getLogger("omnireel.worker")

#: A scene is parked after this many failed dispatches, keeping its history.
MAX_SCENE_ATTEMPTS = 12
#: How long to wait before re-checking a project that has no eligible provider.
WAIT_RETRY_SECONDS = 60.0


class GenerationWorker:
    def __init__(self, app: "Application") -> None:
        self.app = app
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="omnireel-worker")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    # -- loop ------------------------------------------------------------
    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must never die
                logger.exception("worker tick failed")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.app.settings.worker_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            for project in store.active_projects(self.app.db):
                try:
                    await self._advance(project)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("project %s failed to advance", project.get("id"))

    # -- project stepping -------------------------------------------------
    async def _advance(self, project: dict) -> None:
        app = self.app
        db = app.db
        project_id = project["id"]
        scenes = store.scenes_for(db, project_id)
        if not scenes:
            return

        if all(scene["status"] == "ready" for scene in scenes):
            if project.get("status") != "completed":
                await self._assemble(project, scenes)
            return

        # 1. Move in-flight work forward.
        for scene in store.live_scenes(db, project_id):
            current = store.get_scene(db, project_id, scene["id"]) or scene
            if current["status"] == "downloading":
                await app.scheduler.download_scene(project, current)
                continue
            if not self._due_for_poll(current):
                continue
            state = await app.scheduler.advance_scene(project, current)
            if state == "download":
                refreshed = store.get_scene(db, project_id, current["id"]) or current
                await app.scheduler.download_scene(project, refreshed)

        # 2. Dispatch new scenes while global concurrency slots are free.
        slots = max(0, app.settings.max_concurrent_generations - self._global_live_count())
        if slots == 0:
            return

        retry_at = from_iso(project.get("next_retry_at"))
        if retry_at is not None and retry_at > utcnow() and project.get("status") == "waiting":
            return

        dispatched = 0
        while slots > 0:
            scene = store.next_scene_to_generate(db, project_id)
            if scene is None:
                break
            attempts = int(scene.get("attempts") or 0)
            if attempts >= MAX_SCENE_ATTEMPTS:
                store.update_scene(
                    db,
                    scene["id"],
                    status="blocked",
                    last_error=(
                        f"stopped after {attempts} attempts - no provider could generate this "
                        "scene; regenerate it manually once a provider recovers"
                    ),
                )
                continue
            try:
                await app.scheduler.start_scene(project, scene)
            except NoCandidateError as exc:
                self._mark_waiting(project, str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("dispatch failed for project %s", project_id)
                self._mark_waiting(project, f"scheduler error: {exc}")
                return

            dispatched += 1
            slots -= 1
            if project.get("status") == "waiting":
                store.set_project_status(db, project_id, "running", "generation resumed")

        if dispatched == 0 and not store.live_scenes(db, project_id):
            self._mark_waiting(
                project,
                "waiting for an eligible free model - completed work is preserved",
            )

    def _due_for_poll(self, scene: dict) -> bool:
        job = self.app.db.one(
            "SELECT last_poll_at FROM jobs WHERE scene_id=? ORDER BY created_at DESC LIMIT 1",
            (scene["id"],),
        )
        last = from_iso((job or {}).get("last_poll_at"))
        if last is None:
            return True
        elapsed = (utcnow() - last).total_seconds()
        return elapsed >= self.app.settings.poll_interval_seconds

    def _global_live_count(self) -> int:
        """In-flight scenes across every project, so the cap is machine-wide."""
        placeholders = ",".join("?" for _ in store.LIVE_STATUSES)
        row = self.app.db.one(
            f"SELECT COUNT(*) AS n FROM scenes WHERE status IN ({placeholders})",
            store.LIVE_STATUSES,
        )
        return int((row or {}).get("n") or 0)

    def _mark_waiting(self, project: dict, message: str) -> None:
        retry_at = utcnow().timestamp() + WAIT_RETRY_SECONDS
        next_retry = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat(timespec="seconds")
        self.app.db.execute(
            "UPDATE projects SET status='waiting', status_message=?, next_retry_at=?, updated_at=? "
            "WHERE id=? AND status<>'completed'",
            (message[:500], next_retry, store.now_iso(), project["id"]),
        )

    # -- assembly ---------------------------------------------------------
    async def _assemble(self, project: dict, scenes: list[dict]) -> None:
        app = self.app
        db = app.db
        project_id = project["id"]
        store.set_project_status(db, project_id, "assembling", "assembling the final cut")
        try:
            result = await app.assembler.assemble(project, scenes)
        except AssemblyError as exc:
            logger.warning("assembly failed for %s: %s", project_id, exc)
            db.execute(
                "UPDATE projects SET status='failed', status_message=?, updated_at=? WHERE id=?",
                (f"assembly failed: {str(exc)[:400]}", store.now_iso(), project_id),
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected assembly failure for %s", project_id)
            db.execute(
                "UPDATE projects SET status='failed', status_message=?, updated_at=? WHERE id=?",
                (f"assembly failed: {exc!r}", store.now_iso(), project_id),
            )
            return

        db.execute(
            "UPDATE projects SET status='completed', final_video_path=?, final_duration=?, "
            "status_message=?, next_retry_at=NULL, updated_at=? WHERE id=?",
            (
                str(result.output),
                result.duration,
                f"finished {result.duration or 0:.1f}s from {result.scenes} clips",
                store.now_iso(),
                project_id,
            ),
        )
