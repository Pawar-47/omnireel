# CHECKPOINT

Where the build actually stands. Read this first when resuming.

Updated: 2026-09-22 · Version: 1.0.0

## State of the repository

| Area | State |
|---|---|
| Backend application | Complete — API, worker, scheduler, quota ledger, planner, assembly, audio |
| Provider adapters | Agnes verified from vendor docs; Replicate/HF/Runware/gateway implemented, unverified live |
| Frontend | Complete — studio, storyboard, production monitor, provider dashboard, library |
| Tests | Written (7 files, ~60 cases). **Not executed in the authoring environment.** |
| Scripts | install / run / dev / smoke_run |
| Documentation | README, MASTER_PLAN, CHECKPOINT, CHANGELOG, PROVIDER_RESEARCH, MODEL_COMPATIBILITY, INSTALLATION, API_CONFIGURATION |
| Git | Committed and pushed to the public repository |

## What "verified" means here

Environment used to author this code had **no Python interpreter, no Node, no FFmpeg and no network access to provider APIs**. Therefore:

- **Verified by reading vendor sources:** Agnes endpoints, payload fields, response shapes, status values, rate-limit and queue-full behaviour (vendor wiki + published reference client). All encoded in `providers/agnes.py`.
- **Verified by pure logic review:** planner timeline maths, scheduler ranking order, quota arithmetic, FFmpeg argument construction, state machine transitions.
- **NOT verified by execution:** `pytest`, `npm run build`, an actual MP4 assembly, a real Agnes generation.

Nothing in this repository claims otherwise, and no output is presented as AI-generated unless a real provider produced it.

## First five commands on your machine

```bash
./scripts/install.sh                 # 1. venv + deps + frontend build + .env
.venv/bin/python -m pytest backend -q   # 2. run the test suite
.venv/bin/python scripts/smoke_run.py --minutes 2   # 3. full offline pipeline + FFmpeg
nano .env                            # 4. add AGNES_API_KEY
./scripts/run.sh                     # 5. open http://127.0.0.1:8000
```

If step 2 fails, fix the tests before trusting step 3.

## Known gaps / next actions

1. **Run the test suite.** Highest priority; it is the fastest way to surface a typo in the ~6k lines written blind.
2. **Live Agnes run.** Create a 60-second project (6 × 10 s clips) before attempting 120 s, so a single stuck model does not burn an hour.
3. **Confirm the optional providers.** For each of Replicate / Runware / YepAPI, read the account console, correct the model id in `config/providers.json`, and set `verified_at` once it has produced one clip.
4. **Reality check on durations.** Agnes Flash is catalogued as 4/6/8/10/12 s. If the console disagrees, edit `capabilities.py` — the planner and scheduler adapt automatically.
5. **Publish the GitHub release.** Release notes are in `CHANGELOG.md` and a tag-triggered workflow (`.github/workflows/release.yml`) does the rest:

   ```bash
   git tag -a v1.0.0 -m "OmniReel 1.0.0"
   git push origin v1.0.0
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
| Clip length, resolution, free status of a model | `backend/omnireel/capabilities.py` or `config/providers.json` |
| Storyboard wording, beats, style profiles | `backend/omnireel/planner.py` |
| Who gets picked, in what order | `backend/omnireel/scheduler.py` (`rank`, `WEIGHTS`) |
| Retry cadence, concurrency | `backend/omnireel/worker.py`, `.env` |
| A provider's HTTP calls | `backend/omnireel/providers/<name>.py` |
| Join/crossfade/audio behaviour | `backend/omnireel/assembly.py` |
| Screens and controls | `frontend/src/**` |
