# CHECKPOINT

Where the build actually stands. Read this first when resuming.

Updated: 2026-11-22 · Version: 1.1.0

## State of the repository

| Area | State |
|---|---|
| Backend application | Complete — API, worker, scheduler, quota ledger, planner, assembly, audio |
| Provider adapters | 9: Agnes and Zhipu BigModel verified from vendor docs; ModelScope and Pollinations verified from docs + live registry; Replicate/HF/Runware/gateway implemented, unverified live; mock for tests |
| Free video sources | **3** — Agnes (recurring), Zhipu `cogvideox-flash` (免费), ModelScope (daily 魔粒 wallet) |
| Frontend | Complete — studio, storyboard, production monitor, provider dashboard, library |
| Tests | 8 files, 75+ cases — network-free; adapter tests drive `httpx.MockTransport`. CI also gates every push on a real 20 s end-to-end film |
| Scripts | install / run / dev / smoke_run / **discover_models** |
| Documentation | README, MASTER_PLAN, CHECKPOINT, CHANGELOG, PROVIDER_RESEARCH, MODEL_COMPATIBILITY, INSTALLATION, API_CONFIGURATION |
| Git | Committed and pushed to the public repository |

## What "verified" means here

- **Verified by reading vendor sources:** Agnes endpoints, payloads, status values and rate-limit behaviour (vendor wiki + reference client). Zhipu BigModel's endpoint pair, model enum, `video_result[0].url` field, `cogvideox-flash` **免费 pricing** and its error codes. ModelScope's task API, `X-ModelScope-Task-Type` header, `outputs.url` and its duration table. Pollinations' `/video/{prompt}`, the async job flow, and its live `GET /video/models` registry.
- **Verified by execution in GitHub Actions** (commit `4782047`, both checks green): the whole backend suite — planner, quota, scheduler, worker/recovery, assembly, API — and the Vite production build. The assembly test renders twelve 10-second clips and joins them into a validated 120.0 s MP4 with real FFmpeg.
- **Verified by pure logic review:** scheduler ranking order, quota arithmetic, FFmpeg argument construction, state-machine transitions, and the three new adapters' response parsing.
- **Still NOT verified:** live generation against a provider account, and therefore the real clip length `cogvideox-flash` returns. That needs your keys.

Nothing in this repository claims otherwise, and no output is presented as AI-generated unless a real provider produced it.

## First seven commands on your machine

```bash
./scripts/install.sh                              # 1. venv + deps + frontend build + .env
.venv/bin/python -m pytest backend -q             # 2. run the test suite
.venv/bin/python scripts/smoke_run.py --seconds 20 --expect 20   # 3. a 20 s film, offline
nano .env                            # 4. add BIGMODEL_API_KEY (free) and/or AGNES_API_KEY
.venv/bin/python scripts/smoke_run.py --seconds 20 --live        # 5. a real 20 s film (2 calls)
./scripts/run.sh                     # 6. open http://127.0.0.1:8000
.venv/bin/python scripts/discover_models.py   # 7. see what the paid aggregator offers now
```

If step 2 fails, fix the tests before trusting step 3.

Free keys worth getting first, in this order: **Zhipu BigModel** (CogVideoX-Flash is free and needs no card), **Agnes AI** (free with a rate ceiling), **ModelScope** (free but requires a real-name verified Alibaba Cloud account).

## Known gaps / next actions

0. **Generate a real film.** Nothing in this repository has ever produced an AI
   frame: every video so far is the mock provider (colour cards with a slate).
   The CI artifact proves the pipeline, not a model. To get a real film without a
   local install, add `BIGMODEL_API_KEY` as a repository secret and run
   **Actions → Live 20 second film**. Two calls, ~20 s of footage.

1. **Confirm the suite locally.** It passes in CI on Ubuntu with Python 3.11 and FFmpeg; run it once on your own machine so you know your Mint setup matches.
2. **Measure `cogvideox-flash`'s real clip length.** Generate one clip, run `ffprobe` on it, and correct `durations` in `config/providers.json`. Until then 5 s is a planning assumption and the assembler trims the difference.
3. **Live run, 60 seconds first.** Six 10 s clips, so a single stuck model does not burn an hour.
4. **Confirm the optional providers.** For each of Replicate / Runware / YepAPI, read the account console, correct the model id, and set `verified_at` once it has produced one clip.
5. **Publish the release.** Release notes are in `CHANGELOG.md` and a tag-triggered workflow does the rest:

   ```bash
   git tag -a v1.1.0 -m "OmniReel 1.1.0"
   git push origin v1.1.0
   ```

   No release-creation tool is available in the authoring environment, so this final step belongs to the owner.

## How to resume after an interruption

The database is the source of truth, not this file.

- **App restart mid-project:** start the app again. The worker re-reads `projects` with status `running|waiting|assembling`, re-polls any scene that still holds a `remote_job_id`, and continues. Finished scenes are untouched.
- **All providers temporarily unavailable:** the project shows `waiting` with a reason and a `next_retry_at`. It resumes automatically; or press Start again.
- **Assembly failed (missing FFmpeg, bad music path, no speech engine):** scenes stay `ready`. Fix the cause and press "Assemble final film". Nothing regenerates.
- **A single scene is broken:** use Regenerate on that scene. Others are not touched.
- **Want to change the story:** re-plan is allowed only while no scene has been generated.

Useful inspection commands:

```bash
sqlite3 data/omnireel.sqlite "select id,status,status_message from projects order by updated_at desc limit 5;"
sqlite3 data/omnireel.sqlite "select index_no,status,provider,model,attempts,last_error from scenes order by index_no limit 40;"
sqlite3 data/omnireel.sqlite "select * from quotas;"
sqlite3 data/omnireel.sqlite "select * from scene_attempts order by id desc limit 20;"
```

## File map for fast orientation

| Need to change | Edit |
|---|---|
| Clip length, resolution, free status of a model | `backend/omnireel/capabilities.py`, `config/providers.json`, or `scripts/discover_models.py --write` |
| Storyboard wording, beats, style profiles | `backend/omnireel/planner.py` |
| Who gets picked, in what order | `backend/omnireel/scheduler.py` (`rank`, `WEIGHTS`) |
| Retry cadence, concurrency | `backend/omnireel/worker.py`, `.env` |
| A provider's HTTP calls | `backend/omnireel/providers/<name>.py` |
| Join/crossfade/audio behaviour | `backend/omnireel/assembly.py` |
| Screens and controls | `frontend/src/**` |
