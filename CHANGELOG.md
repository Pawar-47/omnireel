# Changelog

All notable changes to OmniReel. Format follows Keep a Changelog; versions follow SemVer.

## [1.0.0] — 2026-09-22

First release: multi-provider, free-tier-first, two-minute AI film generation.

### Added — planning
- Rule-based storyboard planner with eleven visual styles (cinematic, documentary, anime, noir, sci-fi, nature, product, explainer, music video, whimsical 3D).
- Twelve-beat narrative arc that samples evenly for short films and expands for long ones.
- Exact timeline solver: shot lengths are drawn from the models' real capability ladder, so 120 s becomes 12×10 s, 20×6 s or 24×5 s automatically.
- World bible (subject, palette, grade, lighting, negative prompt) carried into every shot prompt for cross-provider consistency.
- Editable storyboard: prompt, camera, lighting, environment and continuity per scene, plus per-scene model pinning.

### Added — providers
- Agnes AI adapter implementing the verified 2.5 protocol: `POST /v1/videos` (`mode`, `seconds` 4–12, `size`, `aspect_ratio`), REST polling with legacy `/agnesapi` fallback, `metadata.url` completion, keyframe conditioning by hosting the reference frame through the image endpoint.
- Replicate adapter (official predictions API, MiniMax Video-01 6 s clips, image-to-video first frame).
- Hugging Face Inference Providers adapter (synchronous bytes → local file, unified via the `file://` download path).
- Runware task-API adapter (async video inference + `getResponse` polling).
- Configurable gateway adapter for YepAPI-style services (base URL and paths from environment).
- Offline mock provider for tests and demos; never presented as AI footage.

### Added — scheduling and quota
- Capability registry with durations, resolutions, aspect ratios, conditioning support, audio, quality/speed scores, free-tier kind and verification date.
- Weighted ranking with four modes: `free_max`, `balanced`, `best_quality`, `preferred`.
- Soft per-model cap plus diversity penalty so a project spreads across providers when alternatives exist.
- Persistent quota ledger: daily windows, trial credit totals, promotional expiry, per-key minimum intervals, next-eligible timestamps, authoritative vs estimated labelling.
- Provider circuit breaker with exponential backoff; auth failures park a provider for hours.
- Scene-level failover that keeps every successful clip and only retries the failed scene.

### Added — recovery
- SQLite schema for projects, scenes, attempts, jobs, quota and usage events.
- Worker that resumes polling of in-flight remote jobs after a restart.
- `waiting` state for projects with no eligible provider, with automatic recovery.
- Per-scene regenerate, per-scene attempts history, and a hard attempt cap that parks a scene instead of spinning.

### Added — continuity
- Last-frame extraction (`data/frames/<project>/scene_NNN_ref.png`) fed to `image_to_video` / `reference_frame` models only.
- Prompt-level continuity instructions everywhere else.

### Added — assembly and audio
- Normalisation to a single resolution/fps/pixel format/audio layout per clip.
- Concat with stream copy, optional `xfade` crossfades, trim to the requested duration, `ffprobe` validation and copy of the final MP4.
- Optional background music with fade in/out and optional offline narration (Piper, falling back to espeak-ng).
- Per-scene clips kept forever so a single scene can be regenerated and re-assembled.

### Added — interface and tooling
- FastAPI surface: health, config, providers, project CRUD, storyboard re-plan, scene editing/assigning/regenerating, start/pause, manual assembly, video and clip downloads, library.
- React + Vite studio: prompt form, storyboard editor, live monitor with provider mix and failover signals, quota dashboard, finished-film library.
- `scripts/install.sh`, `scripts/run.sh`, `scripts/dev.sh`, `scripts/smoke_run.py`.
- Documentation set: README, MASTER_PLAN, CHECKPOINT, PROVIDER_RESEARCH, MODEL_COMPATIBILITY, INSTALLATION, API_CONFIGURATION.

### Known limitations at 1.0.0
- Only the Agnes adapter is verified against live vendor documentation; the others need one successful clip per account before being trusted.
- The backend suite and frontend build pass in GitHub Actions, but no live provider generation has been performed yet — your first real run is the remaining proof.
- No GitHub Release object is published automatically; push the `v1.0.0` tag to trigger `.github/workflows/release.yml`.
