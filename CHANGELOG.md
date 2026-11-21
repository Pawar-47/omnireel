# Changelog

All notable changes to OmniReel. Format follows Keep a Changelog; versions follow SemVer.

## [1.1.0] — 2026-11-22

Two more genuinely free video sources, one paid aggregator, and a licence review of the multi-provider projects that already exist.

### Added — providers
- **Zhipu BigModel adapter** (`providers/bigmodel.py`): `POST /api/paas/v4/videos/generations`, poll `GET /api/paas/v4/async-result/{id}`, result in `video_result[0].url`. `cogvideox-flash` is priced **免费 (free)** on BigModel's own pricing page, which makes it the second recurring free video source. Business errors arrive inside HTTP 200 bodies, so `1302` (concurrency) maps to `rate_limit`, `1305` (overload) to `unavailable`, `1113` (balance) to `quota`. `cogvideox-3` (5 s/10 s, 4K, first+last frame, native audio, 1 CNY/call) ships as paid.
- **ModelScope API-Inference adapter** (`providers/modelscope.py`): `POST /v1/videos/generations` → `task_id`, poll `GET /v1/tasks/{id}` with `X-ModelScope-Task-Type: video_generation`, result at `outputs.url`. Free but credit-metered by a daily 魔粒 wallet; `MiniMax/MiniMax-H3` gives 3/5/10 s clips.
- **Pollinations adapter** (`providers/pollinations.py`): one key, 28 video models (Veo 3.1, Kling 3.0, Seedance, WAN, LTX, MiniMax, Grok). Async job flow via `Prefer: respond-async` + `GET /v1/jobs/{id}`. Every video model there is `paid_only`, so it is a gap-filler, never a free source.
- Six Pollinations models catalogued with durations read from the live registry, including the only 15 s length in the catalogue.

### Added — tooling
- `scripts/discover_models.py`: refreshes `config/providers.json` from a provider's machine-readable capability registry instead of a hand-copied table. This is the one design idea adopted from the existing gateway projects.
- `backend/tests/test_providers.py`: 14 network-free adapter tests using `httpx.MockTransport` — response parsing, error mapping, the 409-while-pending case, inline versus remote media, and catalogue invariants including "the free ladder still solves an exact 120 s timeline".

### Changed
- Free ladder is now `[12, 10, 8, 6, 5, 4, 3]` with three enabled free providers, so a project survives one provider resting.
- `.env.example`, `PROVIDER_RESEARCH.md`, `MODEL_COMPATIBILITY.md`, `README.md` updated with the new sources and the audit's publishable findings.

### Known limitations at 1.1.0
- `cogvideox-flash`'s clip length is **not** published by BigModel; 5 s is a planning assumption, measured and trimmed at assembly.
- ModelScope and Pollinations image conditioning is not wired (both need a public image URL).
- SiliconFlow, WaveSpeed, fal, Novita, Segmind, DeepInfra, Together, Kie, AI Horde and Cloudflare Workers AI were evaluated and **not** integrated as free — reasons recorded in `PROVIDER_RESEARCH.md` §3.
- Still no live generation against any account: that needs the owner's keys.

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
