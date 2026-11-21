# API_CONFIGURATION

Everything OmniReel needs is configured through environment variables (a `.env` file at the repository root is read automatically) plus an optional `config/providers.json` override.

## 1. Environment variables

### Server
| Variable | Default | Meaning |
|---|---|---|
| `OMNIREEL_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only on a trusted network. |
| `OMNIREEL_PORT` | `8000` | HTTP port |
| `OMNIREEL_DATA_DIR` | `./data` | Database, clips, frames, outputs |
| `OMNIREEL_FFMPEG` | `ffmpeg` | FFmpeg binary |
| `OMNIREEL_FFPROBE` | `ffprobe` | FFprobe binary |

### Scheduling
| Variable | Default | Meaning |
|---|---|---|
| `OMNIREEL_MAX_CONCURRENT` | `2` | Simultaneous remote generations, machine-wide |
| `OMNIREEL_POLL_INTERVAL` | `6` | Seconds between polls of one remote job |
| `OMNIREEL_WORKER_INTERVAL` | `3` | Seconds between worker passes |
| `OMNIREEL_HTTP_TIMEOUT` | `180` | Per-request timeout (seconds) |
| `OMNIREEL_DOWNLOAD_TIMEOUT` | `600` | Clip download timeout |
| `OMNIREEL_FFMPEG_TIMEOUT` | `900` | Per-FFmpeg-command timeout |

### Safety and defaults
| Variable | Default | Meaning |
|---|---|---|
| `OMNIREEL_ALLOW_PAID` | `false` | **Master switch.** While false, trial/paid models are never selected. |
| `OMNIREEL_MODE` | `free_max` | `free_max`, `balanced`, `best_quality`, `preferred` |
| `OMNIREEL_TARGET_SECONDS` | `120` | Default film length |
| `OMNIREEL_ASPECT_RATIO` | `16:9` | Default framing |
| `OMNIREEL_RESOLUTION` | `720p` | Default output height |
| `OMNIREEL_ENABLE_MOCK` | `0` | Enables the offline mock provider (tests/demos) |
| `OMNIREEL_PROVIDERS_CONFIG` | `./config/providers.json` | Catalogue overrides |

### Provider credentials
| Variable | Provider | Notes |
|---|---|---|
| `AGNES_API_KEY` | Agnes AI | Free recurring allowance |
| `AGNES_API_KEY_2`, `_3`, … | Agnes AI | Contiguous numbering; rotates on 429 |
| `AGNES_BASE_URL` | Agnes AI | `https://apihub.agnes-ai.com` (global) or `https://api.agnes-ai.cn` (China). Key and site must match. |
| `BIGMODEL_API_KEY` | Zhipu BigModel | **Free** source: `cogvideox-flash` is priced 免费. `cogvideox-3` is paid and stays out of free-only mode. |
| `BIGMODEL_BASE_URL` | Zhipu BigModel | Default `https://open.bigmodel.cn/api/paas/v4` |
| `MODELSCOPE_API_TOKEN` | ModelScope | Free, but spent from a daily 魔粒 credit wallet. The account needs a bound, real-name verified Alibaba Cloud account. |
| `MODELSCOPE_BASE_URL` | ModelScope | Default `https://api-inference.modelscope.cn` |
| `POLLINATIONS_API_KEY` | Pollinations | One key, 28 video models — **every one is `paid_only`**, so it never runs in `free_max` |
| `POLLINATIONS_BASE_URL` | Pollinations | Default `https://gen.pollinations.ai` |
| `REPLICATE_API_TOKEN` | Replicate | Trial runs, then billed |
| `HF_TOKEN` | Hugging Face | Pay-as-you-go / PRO credits |
| `RUNWARE_API_KEY` | Runware | One-off startup credits |
| `RUNWARE_ENDPOINT` | Runware | Default `https://api.runware.ai/v1` |
| `YEPAPI_API_KEY` | Gateway | Plus `YEPAPI_BASE_URL`, `YEPAPI_SUBMIT_PATH`, `YEPAPI_STATUS_PATH` |
| `PIPER_VOICE` | Narration | Path to a Piper voice model; otherwise espeak-ng is used |

Multiple keys are supported only where a provider's own terms permit them (`AGNES_API_KEY_2`, …). OmniReel never creates accounts or rotates identities to dodge limits.

## 2. Provider catalogue overrides

`config/providers.json` merges over the built-in catalogue:

```json
{
  "disable": ["runware:video-default"],
  "enable":  ["agnes:agnes-video-2.5"],
  "models":  [
    {
      "provider": "agnes",
      "model": "agnes-video-2.5",
      "durations": [5, 10],
      "free_kind": "promo",
      "enabled": true,
      "verified_at": "2026-10-01"
    }
  ]
}
```

`disable` / `enable` take `provider:model` keys. `models` entries are merged onto an existing entry or added as new. See `config/providers.json.example`.

### Refreshing from a live registry

Pollinations publishes its capability registry, so the catalogue can be re-derived instead of hand-copied:

```bash
.venv/bin/python scripts/discover_models.py            # table: model, access, durations, resolutions
.venv/bin/python scripts/discover_models.py --json      # the config fragment
.venv/bin/python scripts/discover_models.py --write     # create config/providers.json
```

It refuses to overwrite an existing file without `--force`, and it never guesses: a model with no published duration is skipped rather than invented.

## 3. HTTP API

Base URL `http://127.0.0.1:8000`. All request and response bodies are JSON unless noted.

### Meta
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Version, FFmpeg presence, configured providers, counts |
| GET | `/api/config` | Styles, modes, aspect ratios, resolutions, duration limits, available clip lengths |

### Providers
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/providers` | Every provider: configured, healthy, models, quota rows, recent usage |
| POST | `/api/providers/{provider}/reset` | Clear cooldown / failure counter |

### Projects
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/projects` | Plan a film (returns the storyboard and a provisional assignment) |
| GET | `/api/projects` | List projects |
| GET | `/api/projects/{id}` | One project with scenes, progress, assignments |
| DELETE | `/api/projects/{id}` | Remove a project record |
| POST | `/api/projects/{id}/plan` | Re-plan the storyboard (only before generation) |
| PATCH | `/api/projects/{id}/scenes/{scene}` | Edit a scene's prompt/description/duration/camera/lighting |
| POST | `/api/projects/{id}/scenes/{scene}/assign` | Pin a scene to a specific model |
| POST | `/api/projects/{id}/scenes/{scene}/regenerate` | Regenerate one scene, keeping the rest |
| POST | `/api/projects/{id}/settings` | Continuity, crossfade, music, narration, audio retention |
| POST | `/api/projects/{id}/start` | Begin or resume generation |
| POST | `/api/projects/{id}/pause` | Pause |
| POST | `/api/projects/{id}/assemble` | Force assembly when every scene is ready |
| GET | `/api/projects/{id}/video` | Final MP4 |
| GET | `/api/projects/{id}/scenes/{scene}/clip` | One raw clip |
| GET | `/api/library` | Finished films |

### Example: the whole flow with curl

```bash
# 1. Plan a two-minute film
curl -s -X POST http://127.0.0.1:8000/api/projects \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "A cinematic documentary about exploring a mysterious tropical island",
        "target_seconds": 120,
        "style": "cinematic",
        "aspect_ratio": "16:9",
        "mode": "free_max"
      }' | tee project.json

PID=$(python3 -c "import json;print(json.load(open('project.json'))['id'])")

# 2. Start generation
curl -s -X POST http://127.0.0.1:8000/api/projects/$PID/start >/dev/null

# 3. Watch progress
watch -n 5 "curl -s http://127.0.0.1:8000/api/projects/$PID \
  | python3 -c 'import json,sys;p=json.load(sys.stdin);print(p[\"status\"],p[\"progress\"])'"

# 4. Download when finished
curl -L -o film.mp4 http://127.0.0.1:8000/api/projects/$PID/video
ffprobe -v error -show_entries format=duration -of default=nw=1 film.mp4
```

### Create-project body
| Field | Type | Default | Notes |
|---|---|---|---|
| `prompt` | string | required | 3–4000 chars |
| `target_seconds` | number | 120 | 20–600 |
| `style` | string | `cinematic` | See `/api/config` |
| `aspect_ratio` | string | `16:9` | `16:9`, `9:16`, `1:1` |
| `resolution` | string | `720p` | Advisory; models downgrade if needed |
| `mode` | string | `free_max` | Generation mode |
| `free_only` | bool | `true` | Forced true unless `OMNIREEL_ALLOW_PAID` is set |
| `preferred_provider` | string? | `null` | Used in `preferred` mode |
| `continuity` | bool | `true` | Extract previous final frame |
| `crossfade` | bool | `false` | Shortens the film by `(scenes−1) × 0.5 s` |
| `keep_scene_audio` | bool | `true` | Keep model audio when present |
| `music_path` | string? | `null` | Local audio file |
| `narration_script` | string? | `null` | Rendered offline with Piper/espeak-ng |

## 4. Interactive API docs

FastAPI serves its own documentation at `/docs` (OpenAPI) and `/redoc` while the app is running.

## 5. Security notes

- The server binds to `127.0.0.1` by default. Do not expose it publicly: there is no authentication and it can spend your API quota.
- Keys live only in `.env` (git-ignored). They are never written into the database, logs, projects or generated files.
- The API returns provider ids and quota states, never key material.
- The final video and clips are served from disk paths resolved inside your data directory.
