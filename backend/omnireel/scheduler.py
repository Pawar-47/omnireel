"""Multi-provider scheduler.

For every pending scene the scheduler:

1. reads the scene's duration and creative requirements;
2. collects compatible models (duration, aspect, text/image conditioning);
3. filters by legitimate free eligibility and provider health;
4. checks the persistent quota ledger (rate windows, credits, cooldowns);
5. ranks what remains according to the project's generation mode;
6. submits to the best candidate;
7. fails over to the next candidate - for *that scene only* - when a provider
   refuses, is rate limited or errors out.

Nothing here restarts a project or discards finished work: every decision is
recorded against the scene, and the worker simply picks up wherever it stopped.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .capabilities import CapabilityRegistry, ModelSpec
from .config import Settings
from .continuity import MediaTools, prepare_continuity_reference
from .db import Database, dumps, loads
from .planner import Shot
from .providers.base import ProviderError, VideoProvider, VideoRequest
from .quota import ProviderHealth, QuotaLedger, to_iso, utcnow

MODES = ("free_max", "best_quality", "balanced", "preferred")

#: How many of this project's scenes one model may take before the scheduler
#: actively prefers a different provider (only applies when alternatives exist).
SOFT_CAP_PER_MODEL = 4
#: Score penalty for a model that has hit the soft cap.
SOFT_CAP_PENALTY = 15.0
#: Acceptable mismatch between a planned shot length and what a model can do.
DURATION_TOLERANCE = 0.51
LOOSE_DURATION_TOLERANCE = 2.5

WEIGHTS: dict[str, dict[str, float]] = {
    "free_max": {"quality": 1.0, "speed": 1.5, "diversity": 2.5, "headroom": 0.6, "preferred": 0.0},
    "balanced": {"quality": 2.0, "speed": 1.0, "diversity": 1.5, "headroom": 0.4, "preferred": 0.0},
    "best_quality": {"quality": 4.0, "speed": 0.3, "diversity": 0.3, "headroom": 0.15, "preferred": 0.0},
    "preferred": {"quality": 2.0, "speed": 1.0, "diversity": 1.2, "headroom": 0.4, "preferred": 6.0},
}


@dataclass
class Candidate:
    spec: ModelSpec
    duration: float
    key_index: int
    key_id: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def provider(self) -> str:
        return self.spec.provider

    @property
    def model(self) -> str:
        return self.spec.model

    def public_dict(self) -> dict:
        return {
            "provider": self.spec.provider,
            "model": self.spec.model,
            "label": self.spec.label or self.spec.model,
            "duration": self.duration,
            "key_id": self.key_id,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
            "quality": self.spec.quality,
            "free_kind": self.spec.free_kind,
            "verified": self.spec.verified,
        }


class NoCandidateError(RuntimeError):
    """No model can take this scene right now (quota, health or capability)."""


class Scheduler:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        registry: CapabilityRegistry,
        providers: dict[str, VideoProvider],
        ledger: QuotaLedger,
        health: ProviderHealth,
        media: MediaTools | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry
        self.providers = providers
        self.ledger = ledger
        self.health = health
        self.media = media or MediaTools(settings)

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    def _provider(self, provider_id: str) -> VideoProvider | None:
        provider = self.providers.get(provider_id)
        if provider is None or not provider.configured:
            return None
        return provider

    def provider_status(self) -> list[dict]:
        """Everything the dashboard needs about every provider."""
        rows: list[dict] = []
        states = self.health.all_states()
        for provider_id in self.registry.providers():
            provider = self.providers.get(provider_id)
            models = [spec for spec in self.registry.for_provider(provider_id) if spec.enabled]
            key_ids = self.settings.key_ids_for(provider_id) or [f"{provider_id}:none"]
            quotas = []
            for spec in models:
                for key_id in key_ids:
                    quotas.append(self.ledger.snapshot(spec, key_id).public_dict())
            state = states.get(provider_id, {})
            available, reason = self.health.is_available(provider_id)
            rows.append(
                {
                    "provider": provider_id,
                    "label": getattr(provider, "label", provider_id),
                    "configured": bool(provider and provider.configured),
                    "requires_key": bool(getattr(provider, "requires_key", True)),
                    "keys": len(self.settings.keys_for(provider_id)),
                    "healthy": available,
                    "health_reason": reason,
                    "consecutive_failures": int(state.get("consecutive_failures") or 0),
                    "cooldown_until": state.get("cooldown_until"),
                    "last_error": state.get("last_error"),
                    "last_success_at": state.get("last_success_at"),
                    "models": [spec.public_dict() for spec in models],
                    "quotas": quotas,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------
    def _usage(self, project_id: str) -> tuple[dict[str, int], dict[str, int]]:
        rows = self.db.query(
            """
            SELECT provider, model, COUNT(*) AS n
              FROM scenes
             WHERE project_id=?
               AND status IN ('queued','dispatched','polling','downloading','ready')
               AND provider IS NOT NULL
             GROUP BY provider, model
            """,
            (project_id,),
        )
        per_model: dict[str, int] = {}
        per_provider: dict[str, int] = {}
        for row in rows:
            count = int(row["n"] or 0)
            per_model[f"{row['provider']}:{row['model']}"] = count
            per_provider[row["provider"]] = per_provider.get(row["provider"], 0) + count
        return per_model, per_provider

    def rank(
        self,
        project: dict,
        scene: dict,
        *,
        mode: str | None = None,
        free_only: bool | None = None,
        exclude: Iterable[str] | None = None,
    ) -> list[Candidate]:
        """Every model that could take this scene, best first."""
        mode = mode or (project.get("mode") or self.settings.default_mode)
        if mode not in MODES:
            mode = "balanced"
        if free_only is None:
            free_only = bool(project.get("free_only", True)) and not self.settings.allow_paid
        weights = WEIGHTS[mode]
        preferred = (project.get("preferred_provider") or "").strip()
        wanted_duration = float(scene.get("duration_seconds") or 10.0)
        aspect = project.get("aspect_ratio") or self.settings.default_aspect_ratio
        resolution = project.get("resolution") or self.settings.default_resolution
        excluded = set(exclude or ())

        per_model, per_provider = self._usage(project["id"])

        def collect(tolerance: float) -> list[Candidate]:
            found: list[Candidate] = []
            for spec in self.registry.enabled():
                if spec.key in excluded:
                    continue
                if free_only and not spec.free_eligible:
                    continue
                snap = spec.snap_duration(wanted_duration)
                if snap is None or abs(snap - wanted_duration) > tolerance:
                    continue
                provider = self._provider(spec.provider)
                if provider is None:
                    continue
                available, _reason = self.health.is_available(spec.provider)
                if not available:
                    continue

                key_ids = self.settings.key_ids_for(spec.provider) or [f"{spec.provider}:none"]
                for index, key_id in enumerate(key_ids):
                    ok, reason = self.ledger.check(spec, key_id, free_only=free_only)
                    if not ok:
                        continue
                    snapshot = self.ledger.snapshot(spec, key_id)
                    score = 0.0
                    reasons: list[str] = []

                    score += weights["quality"] * spec.quality
                    score += weights["speed"] * spec.speed
                    if mode == "preferred" and preferred and spec.provider == preferred:
                        score += weights["preferred"]
                        reasons.append("preferred provider")
                    model_uses = per_model.get(spec.key, 0)
                    provider_uses = per_provider.get(spec.provider, 0)
                    score -= weights["diversity"] * model_uses
                    score -= 0.5 * weights["diversity"] * provider_uses
                    if snapshot.remaining is not None:
                        score += weights["headroom"] * min(snapshot.remaining, 12)
                    if spec.verified:
                        score += 1.5
                    else:
                        score -= 3.0
                        reasons.append("capabilities unverified")
                    if abs(snap - wanted_duration) > DURATION_TOLERANCE:
                        score -= 1.0
                        reasons.append(f"clip length snaps to {snap:g}s")
                    if not spec.supports_aspect(aspect):
                        score -= 2.0
                        reasons.append(f"no native {aspect} framing")
                    if spec.supported_resolution(resolution) != resolution:
                        score -= 0.5
                        reasons.append(f"renders at {spec.supported_resolution(resolution)}")
                    if mode == "free_max" and spec.free_kind == "promo":
                        reasons.append("promotional allowance")
                    if snapshot.estimated and snapshot.remaining is None:
                        reasons.append("quota not published by provider")

                    found.append(
                        Candidate(
                            spec=spec,
                            duration=float(snap),
                            key_index=index,
                            key_id=key_id,
                            score=score,
                            reasons=reasons,
                        )
                    )
            return found

        candidates = collect(DURATION_TOLERANCE)
        if not candidates:
            # Nothing matches exactly - relax, and let the assembler normalise later.
            candidates = collect(LOOSE_DURATION_TOLERANCE)
        if not candidates:
            return []

        # Spread the load: only push away from a model when alternatives exist.
        if len({candidate.spec.provider for candidate in candidates}) > 1:
            for candidate in candidates:
                if per_model.get(candidate.spec.key, 0) >= SOFT_CAP_PER_MODEL:
                    candidate.score -= SOFT_CAP_PENALTY
                    candidate.reasons.append("soft per-project cap reached")

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)

        # A manual per-scene provider choice (set from the UI) is honoured first,
        # but never at the cost of failover: the remaining candidates stay queued.
        manual_provider = (scene.get("provider") or "").strip()
        manual_model = (scene.get("model") or "").strip()
        if manual_provider and scene.get("status") == "planned":
            candidates.sort(
                key=lambda candidate: 0
                if candidate.spec.provider == manual_provider
                and (not manual_model or candidate.spec.model == manual_model)
                else 1
            )
        return candidates

    def preview_assignments(self, project: dict, scenes: list[dict]) -> dict[str, dict]:
        """Dry-run assignment used by the API so the storyboard can show a plan."""
        local_models: dict[str, int] = {}
        local_providers: dict[str, int] = {}
        assignments: dict[str, dict] = {}
        for scene in scenes:
            candidates = self.rank(project, scene)
            if not candidates:
                assignments[scene["id"]] = {
                    "provider": None,
                    "model": None,
                    "duration": scene.get("duration_seconds"),
                    "reason": "no eligible free model for this clip length",
                }
                continue

            # Spread the preview the same way the real scheduler will.
            ranked = sorted(
                candidates,
                key=lambda candidate: candidate.score
                - 2.5 * local_models.get(candidate.spec.key, 0)
                - 1.25 * local_providers.get(candidate.provider, 0),
                reverse=True,
            )
            chosen = ranked[0]
            assignments[scene["id"]] = {
                "provider": chosen.provider,
                "model": chosen.model,
                "label": chosen.spec.label or chosen.model,
                "duration": chosen.duration,
                "alternatives": [c.public_dict() for c in ranked[1:4]],
                "reason": ", ".join(chosen.reasons) or "best ranked free option",
            }
            local_models[chosen.spec.key] = local_models.get(chosen.spec.key, 0) + 1
            local_providers[chosen.provider] = local_providers.get(chosen.provider, 0) + 1
        return assignments

    # ------------------------------------------------------------------
    # Scene lifecycle
    # ------------------------------------------------------------------
    def reference_image_for(self, project: dict, scene: dict) -> Path | None:
        """The previous clip, used as the continuity source for this scene."""
        options = loads(project.get("settings"), {})
        if not options.get("continuity", True):
            return None
        index = int(scene.get("index_no") or 0)
        if index <= 0:
            return None
        previous = self.db.one(
            "SELECT * FROM scenes WHERE project_id=? AND index_no=?",
            (project["id"], index - 1),
        )
        if not previous or previous.get("status") != "ready" or not previous.get("clip_path"):
            return None
        clip = Path(previous["clip_path"])
        return clip if clip.exists() else None

    def _build_request(
        self,
        project: dict,
        scene: dict,
        candidate: Candidate,
        reference: Path | None,
    ) -> VideoRequest:
        world = loads(project.get("world_bible"), {})
        prompt = scene.get("prompt") or scene.get("description") or project.get("prompt", "")
        continuity = scene.get("continuity") or ""
        if continuity and continuity.lower() not in prompt.lower():
            prompt = f"{prompt} {continuity}".strip()
        if reference is not None and not candidate.spec.image_to_video and not candidate.spec.reference_frame:
            reference = None
        return VideoRequest(
            prompt=prompt[:2000],
            duration=float(candidate.duration),
            aspect_ratio=project.get("aspect_ratio") or self.settings.default_aspect_ratio,
            resolution=candidate.spec.supported_resolution(
                project.get("resolution") or self.settings.default_resolution
            )
            or "720p",
            negative_prompt=str(world.get("negative_prompt") or ""),
            reference_image=reference,
        )

    async def start_scene(self, project: dict, scene: dict, *, trial_limit: int = 4) -> dict:
        """Try candidates in ranked order until one accepts the job."""
        candidates = self.rank(project, scene)[:trial_limit]
        if not candidates:
            raise NoCandidateError(
                f"no eligible model can generate a {float(scene.get('duration_seconds') or 0):g}s clip right now"
            )

        last_error = "no candidate accepted the job"
        errors: list[dict] = []
        for candidate in candidates:
            provider = self._provider(candidate.provider)
            if provider is None:
                continue
            free_only = bool(project.get("free_only", True)) and not self.settings.allow_paid
            ok, reason = self.ledger.check(candidate.spec, candidate.key_id, free_only=free_only)
            if not ok:
                errors.append({"provider": candidate.provider, "model": candidate.model, "error": reason})
                continue

            reference = None
            clip = self.reference_image_for(project, scene)
            if clip is not None:
                reference = await prepare_continuity_reference(
                    self.media,
                    self.settings,
                    project_id=project["id"],
                    index=int(scene.get("index_no") or 0),
                    previous_clip=clip,
                )

            request = self._build_request(project, scene, candidate, reference)
            self.ledger.reserve(candidate.spec, candidate.key_id)
            self._record_attempt(project, scene, candidate, status="submitting")
            try:
                submitted = await provider.submit(
                    candidate.model, request, key_index=candidate.key_index
                )
            except ProviderError as exc:
                self.ledger.release(candidate.spec, candidate.key_id, kind=exc.kind)
                self.ledger.record_error(
                    candidate.spec,
                    candidate.key_id,
                    kind=exc.kind,
                    message=str(exc),
                    retry_after=exc.retry_after,
                    project_id=project["id"],
                    scene_id=scene["id"],
                )
                self.health.record_failure(
                    candidate.provider, str(exc), kind=exc.kind, retry_after=exc.retry_after
                )
                self._finish_attempt(project, scene, candidate, status="failed", error=str(exc))
                errors.append(
                    {"provider": candidate.provider, "model": candidate.model, "error": str(exc)}
                )
                last_error = str(exc)
                continue
            except Exception as exc:  # defensive: a broken adapter must not kill the worker
                self.ledger.release(candidate.spec, candidate.key_id, kind="transient")
                self.health.record_failure(candidate.provider, repr(exc), kind="transient")
                self._finish_attempt(project, scene, candidate, status="failed", error=repr(exc))
                errors.append(
                    {"provider": candidate.provider, "model": candidate.model, "error": repr(exc)}
                )
                last_error = repr(exc)
                continue

            job_id = uuid.uuid4().hex
            now = to_iso(utcnow())
            self.db.execute(
                """
                INSERT INTO jobs(id, project_id, scene_id, provider, model, key_id, remote_job_id,
                                 status, polls, created_at, updated_at, payload)
                VALUES(?,?,?,?,?,?,?,?,0,?,?,?)
                """,
                (
                    job_id,
                    project["id"],
                    scene["id"],
                    candidate.provider,
                    candidate.model,
                    candidate.key_id,
                    submitted.remote_id,
                    "submitted" if submitted.state != "succeeded" else "succeeded",
                    now,
                    now,
                    dumps({"video_url": submitted.video_url}),
                ),
            )
            self.db.execute(
                """
                UPDATE scenes
                   SET status=?, provider=?, model=?, remote_job_id=?, attempts=attempts+1,
                       duration_seconds=?, reference_image_path=?, last_error=NULL, updated_at=?
                 WHERE id=?
                """,
                (
                    "polling",
                    candidate.provider,
                    candidate.model,
                    submitted.remote_id,
                    float(candidate.duration),
                    str(reference) if reference else None,
                    now,
                    scene["id"],
                ),
            )
            self._finish_attempt(project, scene, candidate, status="submitted", error=None)
            return {
                "started": True,
                "provider": candidate.provider,
                "model": candidate.model,
                "duration": candidate.duration,
                "job_id": job_id,
                "remote_id": submitted.remote_id,
                "errors": errors,
            }

        raise NoCandidateError(last_error)

    def _record_attempt(self, project: dict, scene: dict, candidate: Candidate, *, status: str) -> None:
        self.db.execute(
            """
            INSERT INTO scene_attempts(project_id, scene_id, provider, model, started_at, status)
            VALUES(?,?,?,?,?,?)
            """,
            (
                project["id"],
                scene["id"],
                candidate.provider,
                candidate.model,
                to_iso(utcnow()),
                status,
            ),
        )

    def _finish_attempt(
        self,
        project: dict,
        scene: dict,
        candidate: Candidate,
        *,
        status: str,
        error: str | None,
    ) -> None:
        row = self.db.one(
            """
            SELECT id FROM scene_attempts
             WHERE scene_id=? AND provider=? AND model=? AND finished_at IS NULL
             ORDER BY id DESC LIMIT 1
            """,
            (scene["id"], candidate.provider, candidate.model),
        )
        if row is None:
            return
        self.db.execute(
            "UPDATE scene_attempts SET finished_at=?, status=?, error=? WHERE id=?",
            (to_iso(utcnow()), status, (error or "")[:600] or None, row["id"]),
        )

    def scene_attempts(self, scene_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM scene_attempts WHERE scene_id=? ORDER BY id", (scene_id,)
        )

    def _job_for(self, scene_id: str) -> dict | None:
        return self.db.one(
            "SELECT * FROM jobs WHERE scene_id=? AND status IN ('submitted','polling') "
            "ORDER BY created_at DESC LIMIT 1",
            (scene_id,),
        )

    # ------------------------------------------------------------------
    # Polling and download
    # ------------------------------------------------------------------
    async def advance_scene(self, project: dict, scene: dict) -> str:
        """Poll one in-flight scene. Returns pending|download|retry|done."""
        provider = self._provider(scene.get("provider") or "")
        remote_id = scene.get("remote_job_id")
        if provider is None or not remote_id:
            return "retry"

        job = self._job_for(scene["id"])
        key_index = 0
        if job is not None:
            try:
                key_index = max(0, int(str(job.get("key_id", "")).split(":")[-1]) - 1)
            except (TypeError, ValueError):
                key_index = 0

        try:
            result = await provider.poll(scene["model"] or "", remote_id, key_index=key_index)
        except ProviderError as exc:
            snapshot = self.registry.get(provider.id, scene.get("model") or "")
            if snapshot is not None:
                fallback_key = (self.settings.key_ids_for(provider.id) or [f"{provider.id}:1"])[0]
                self.ledger.record_error(
                    snapshot,
                    (job or {}).get("key_id") or fallback_key,
                    kind=exc.kind,
                    message=str(exc),
                    retry_after=exc.retry_after,
                    project_id=project["id"],
                    scene_id=scene["id"],
                )
            self.health.record_failure(
                provider.id, str(exc), kind=exc.kind, retry_after=exc.retry_after
            )
            self._mark_scene_failed(scene, str(exc))
            return "retry"
        except Exception as exc:  # defensive
            self.health.record_failure(provider.id, repr(exc), kind="transient")
            self._mark_scene_failed(scene, repr(exc))
            return "retry"

        if job is not None:
            self.db.execute(
                "UPDATE jobs SET polls=polls+1, last_poll_at=?, updated_at=? WHERE id=?",
                (to_iso(utcnow()), to_iso(utcnow()), job["id"]),
            )

        if result.state == "succeeded" and result.video_url:
            self.db.execute(
                "UPDATE scenes SET status='downloading', remote_url=?, last_error=NULL, updated_at=? "
                "WHERE id=?",
                (result.video_url, to_iso(utcnow()), scene["id"]),
            )
            return "download"

        if result.state == "failed":
            message = result.error or "provider reported failure"
            mark = self.registry.get(provider.id, scene.get("model") or "")
            if mark is not None and job is not None:
                self.ledger.record_error(
                    mark,
                    job["key_id"],
                    kind=result.error_kind or "transient",
                    message=message,
                    project_id=project["id"],
                    scene_id=scene["id"],
                )
            self.health.record_failure(
                provider.id, message, kind=result.error_kind or "transient"
            )
            self._mark_scene_failed(scene, message)
            return "retry"

        if result.state == "not_found":
            polls = int(job.get("polls") or 0) if job else 0
            if polls < 40:
                return "pending"
            self._mark_scene_failed(scene, "provider never acknowledged the job")
            return "retry"

        if result.progress is not None:
            self.db.execute(
                "UPDATE scenes SET updated_at=? WHERE id=?", (to_iso(utcnow()), scene["id"])
            )
        return "pending"

    def _mark_scene_failed(self, scene: dict, message: str) -> None:
        self.db.execute(
            "UPDATE scenes SET status='failed', last_error=?, updated_at=? WHERE id=?",
            (message[:600], to_iso(utcnow()), scene["id"]),
        )

    async def download_scene(self, project: dict, scene: dict) -> bool:
        """Fetch the finished clip, validate it and mark the scene ready."""
        provider = self._provider(scene.get("provider") or "")
        url = scene.get("remote_url")
        if provider is None or not url:
            self._mark_scene_failed(scene, "no downloadable clip URL")
            return False
        destination = self.settings.project_dir(project["id"]) / f"scene_{int(scene['index_no']):03d}.mp4"
        try:
            await provider.download(url, destination)
        except ProviderError as exc:
            self._mark_scene_failed(scene, f"download failed: {exc}")
            return False
        except Exception as exc:  # defensive
            self._mark_scene_failed(scene, f"download failed: {exc!r}")
            return False

        info = {"duration": None, "has_audio": None}
        if self.media.available:
            try:
                info = await self.media.probe(destination)
            except Exception:
                info = {"duration": None, "has_audio": None}

        actual = info.get("duration") or float(scene.get("duration_seconds") or 0)
        now = to_iso(utcnow())
        self.db.execute(
            """
            UPDATE scenes
               SET status='ready', clip_path=?, actual_duration=?, last_error=NULL, updated_at=?
             WHERE id=?
            """,
            (str(destination), float(actual or 0), now, scene["id"]),
        )
        spec = self.registry.get(provider.id, scene.get("model") or "")
        job = self._job_for(scene["id"])
        if spec is not None:
            key_id = (job or {}).get("key_id") or (
                self.settings.key_ids_for(provider.id)[:1] or [f"{provider.id}:1"]
            )[0]
            self.ledger.record_success(
                spec,
                key_id,
                project_id=project["id"],
                scene_id=scene["id"],
                duration_seconds=float(actual or 0),
            )
        self.health.record_success(provider.id)
        if job is not None:
            self.db.execute(
                "UPDATE jobs SET status='done', updated_at=? WHERE id=?", (now, job["id"])
            )
        return True

    # ------------------------------------------------------------------
    # Retries
    # ------------------------------------------------------------------
    def reset_scene(self, scene: dict, *, keep_clip: bool = False) -> None:
        """Put a scene back in the queue without touching other scenes."""
        now = to_iso(utcnow())
        if keep_clip:
            self.db.execute(
                "UPDATE scenes SET status='planned', remote_job_id=NULL, remote_url=NULL, "
                "last_error=NULL, updated_at=? WHERE id=?",
                (now, scene["id"]),
            )
        else:
            self.db.execute(
                """
                UPDATE scenes
                   SET status='planned', provider=NULL, model=NULL, remote_job_id=NULL,
                       remote_url=NULL, clip_path=NULL, actual_duration=NULL,
                       last_error=NULL, updated_at=?
                 WHERE id=?
                """,
                (now, scene["id"]),
            )

    def annotate_project_progress(self, project_id: str) -> dict:
        rows = self.db.query(
            "SELECT status, duration_seconds, actual_duration, provider, model FROM scenes "
            "WHERE project_id=? ORDER BY index_no",
            (project_id,),
        )
        ready = [row for row in rows if row["status"] == "ready"]
        pending = [row for row in rows if row["status"] != "ready"]
        generated = round(sum(float(row["actual_duration"] or row["duration_seconds"] or 0) for row in ready), 2)
        planned = round(sum(float(row["duration_seconds"] or 0) for row in rows), 2)
        return {
            "scenes_total": len(rows),
            "scenes_ready": len(ready),
            "scenes_pending": len(pending),
            "generated_seconds": generated,
            "planned_seconds": planned,
            "remaining_seconds": round(max(0.0, planned - generated), 2),
            "completion": round(len(ready) / len(rows), 3) if rows else 0.0,
            "duration_completion": round(min(1.0, generated / planned), 3) if planned else 0.0,
        }


def estimate_call_count(shots: Iterable[Shot]) -> int:
    return max(1, math.ceil(len(list(shots))))
