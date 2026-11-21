# MASTER_PLAN

The persistent plan for OmniReel. Status is honest: everything marked **done** exists in this repository; everything marked **needs verification** has not been exercised against a live provider from the authoring environment.

## 1. The problem, stated precisely

Free AI video models emit 4–12 second clips. A user asking for a two-minute video cannot get one from a single model: the allowance is small, the clip is short, and one provider failing kills the whole render.

**Therefore:** the unit of work is a *scene*, not a video. A two-minute film is ~10–24 scenes; each scene is scheduled independently onto whichever model can make it right now, and FFmpeg joins the results.

Two failure-handling mechanisms, deliberately distinct:

| Mechanism | Purpose | Where |
|---|---|---|
| **Segment distribution** | Spread scenes across independent providers so no single allowance is exhausted | `scheduler.rank()` + soft per-model cap + diversity penalty |
| **Provider failover** | A single scene that fails moves to another compatible model; nothing else restarts | `scheduler.start_scene()` candidate loop, `worker._advance()` |

## 2. Architecture

```
HTTP API (FastAPI)  ──►  store.py (SQLite)  ◄──  GenerationWorker (asyncio)
                              │                        │
                              │                        ├─ Scheduler.rank()
                              │                        ├─ provider.submit/poll/download
                              │                        ├─ QuotaLedger / ProviderHealth
                              │                        └─ Assembler (FFmpeg)
                              ▼
                     Planner (pure)  ◄──  CapabilityRegistry
```

Design choices and why:

1. **SQLite, not in-memory queues.** Resumption after a crash is a hard requirement; SQLite gives it for free with zero services to run on an 8 GB laptop.
2. **The provider contract is submit/poll/download.** Every cloud video API is asynchronous in shape; adapters normalise error kinds (`quota`, `rate_limit`, `auth`, `transient`, `unavailable`, `invalid_request`, `paid_required`) so scheduling policy never inspects raw HTTP.
3. **Pure planner, pure FFmpeg command builders.** Both are unit-testable without network, FFmpeg or keys — the only way to trust an app you cannot run in CI here.
4. **The scheduler owns policy; the worker owns time.** Ranking is stateless and repeatable; the worker only decides *when* to call it. This keeps concurrency bugs out of the ranking code.
5. **No local model inference.** No PyTorch, no CUDA, no model downloads. The dependency list is FastAPI + httpx + pydantic.

## 3. Milestones

### MILESTONE 1 — Research reference repositories and providers — **done**
Audited `lcy362/agnes-video-generator` (MIT, 751 commits, single-provider multi-scene pipeline), `OpenMontageInc/OpenMontage` (AGPL-3.0, agentic production system) and the Agnes wiki/reference client. Findings and licence consequences: `PROVIDER_RESEARCH.md`.
Outcome: adopt Agnes' verified HTTP contract and its checkpoint/resume *concept*; adopt OpenMontage's multi-dimension provider scoring *concept*; copy **no** AGPL code so OmniReel stays MIT.

### MILESTONE 2 — Repository and architecture — **done**
New public repository, MIT licence, backend/frontend/scripts/docs split, SQLite schema, capability registry, provider contract.

### MILESTONE 3 — One genuine cloud provider — **done (needs live key)**
`providers/agnes.py` implements the verified 2.5 protocol: `POST /v1/videos` with `mode`/`seconds`/`size`/`aspect_ratio`, keyframe conditioning via a hosted reference frame, polling through `/v1/videos/{id}` with `/v1/agnesapi` fallback, `metadata.url` extraction, queue-full and 429 handling.

### MILESTONE 4 — Storyboard planner and variable durations — **done**
Eleven style profiles, twelve-beat arc with sampling/expansion, badge-free timeline solver (breadth-first, longest-clip-first, exact when reachable).

### MILESTONE 5 — Additional independent providers — **done (needs verification)**
Replicate, Hugging Face Inference Providers, Runware, configurable gateway, plus a deterministic mock provider for tests.

### MILESTONE 6 — Intelligent scheduler — **done**
Capability filter → free-eligibility filter → health → quota → weighted score (mode-dependent) → soft cap → submit → fail over the same scene.

### MILESTONE 7 — Quota management and failover — **done**
`quota.py` distinguishes rate limiting from exhaustion, tracks daily windows and credit grants, labels estimates, and backs off per provider. Failover is immediate for that scene only.

### MILESTONE 8 — Persistent recovery and scene retries — **done**
Jobs, attempts, usage events and quota all persist; worker reload reads state from SQLite on every pass; `waiting` projects auto-resume; per-scene regenerate endpoint.

### MILESTONE 9 — Continuity — **done**
World bible in every prompt; last-frame extraction to `data/frames/<project>/scene_NNN_ref.png`; reference passed only to models declaring `image_to_video`/`reference_frame`.

### MILESTONE 10 — FFmpeg assembly — **done (unit-tested command construction; real render needs FFmpeg)**
Normalise → concat (stream copy) → optional crossfade → optional audio mix → trim → `ffprobe` validation → copy to `data/outputs/<project>.mp4`.

### MILESTONE 11 — Frontend, library, installation — **done**
React studio: prompt form, editable storyboard, live production monitor, provider/quota dashboard, finished-film library. Installer script, smoke run, docs.

### MILESTONE 12 — End-to-end tests and release — **partially done — see CHECKPOINT.md**
Test suite written (planner, quota, scheduler, worker, assembly, API). It could not be executed in the authoring environment (no Python interpreter, no FFmpeg). A live Agnes generation run also requires the owner's API key. Release notes and instructions are prepared; publishing a GitHub Release needs the owner's confirm.

## 4. What makes the two-minute target robust

| Risk | Mitigation |
|---|---|
| Planner chooses a duration no model supports | Durations come from the registry ladder, and a solver fallback keeps the total as close as possible |
| A provider returns shorter clips than requested | `ffprobe` records `actual_duration` per scene; assembly joins what exists, trims to target, reports shortfall |
| Free allowance exhausted mid-project | Scene stays `planned`/`failed`; project becomes `waiting`; nothing is lost; auto-resume on recovery |
| Provider changes model ids | Catalogue is data; edit `capabilities.py` or `config/providers.json` without touching logic |
| Application crash | Statuses + remote job ids in SQLite; restart resumes polling |
| Assembly fails after all scenes exist | Statuses stay `ready`; fix FFmpeg/audio and re-assemble — no regeneration |
| Crossfades shorten the film | Option is opt-in and documented, default is hard cuts at exact duration |

## 5. Deliberate non-goals

- Real-time generation, streaming, or local diffusion.
- Timeline compositing beyond cuts/crossfades (no NLE).
- Automatic account creation or key rotation to dodge limits — explicitly out of scope and contrary to provider terms.
- Paid generation of any kind unless `OMNIREEL_ALLOW_PAID=true` is set by the owner.

## 6. Roadmap after 1.0

1. Live verification matrix per provider, updating `verified_at` as each account is proven.
2. Optional LLM-assisted storyboarding (OpenAI-compatible endpoint) while keeping the rule-based planner as the offline default.
3. Sub-shot continuity hints via a shared first-frame image across a scene group.
4. Per-scene re-planning that keeps neighbours' prompts consistent.
5. Export of a shot list (CSV/JSON) for external editors.
