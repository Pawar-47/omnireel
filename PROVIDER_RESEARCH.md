# PROVIDER_RESEARCH

Audit performed 2026-09-22. Every claim below is tagged with where it came from. Nothing is invented: anything I could not confirm is marked **unverified** and the corresponding model ships disabled.

## 1. Reference repositories

### lcy362/agnes-video-generator — primary reference
- **Licence:** MIT. **Size:** ~751 commits, 747 stars, active.
- **What it is:** self-hosted, free, multi-scene video generator on Agnes AI models. Python + web UI, checkpoint resume, narration, subtitles, keyframes, image-to-video.
- **What I took:** the *verified Agnes HTTP contract* (read from `core/api/agnes_video.py`), the idea of checkpointed scene state, the key-ring concept (multiple keys rotate on 429), and the observed failure taxonomy (`429`, `503 video_queue_full`, `401` on domain mismatch, upstream status reported inside an HTTP 200 body).
- **What I did not take:** any source code. It is a single-provider service with a different scope; copying would drag in an unneeded web stack, i18n layer and provider-specific quirks.
- **Consequence:** OmniReel's Agnes adapter is written independently but matches that de-facto contract exactly.

### OpenMontageInc/OpenMontage — architecture reference
- **Licence: AGPL-3.0.** This is decisive: no code, no schema, no template was copied, because that licence would force OmniReel to be AGPL too.
- **What I studied:** the scored provider-selection dimensions (task fit, quality, control, reliability, cost, latency, continuity), human approval gates before expensive work, and pre/post-render validation.
- **What I adopted conceptually:** multi-dimension scoring with a mode-dependent weighting, and mandatory `ffprobe` validation of the assembled file before declaring success.
- **No reuse.** Concept-only.

### rahulkumar8360/ai-video-studio — implementation reference
- Lightweight FastAPI + React shape. OmniReel independently arrives at the same split (thin async backend, static React build served by FastAPI), which confirms the stack choice for an i3 laptop.

### AgnesAI-Labs/AgnesAI-Models and the Agnes wiki
- Source of truth for the API surface: OpenAI-compatible base `https://apihub.agnes-ai.com/v1`, `Authorization: Bearer <key>`, multimodal models for text/image/video, video modes `text`, `keyframe`, `reference`.

### backblaze-labs/awesome-video-generation
- Used as a discovery index only. Individual claims were re-checked at the provider before being trusted.

## 2. Provider audit

### Agnes AI — **verified, enabled by default**
| Item | Value | Source |
|---|---|---|
| Base URL | `https://apihub.agnes-ai.com/v1` (CN: `https://api.agnes-ai.cn`) | Vendor wiki |
| Auth | `Authorization: Bearer <key>` | Vendor wiki |
| Submit | `POST /v1/videos` → `{"video_id": …}` | Reference client |
| Payload | `model`, `prompt`, `mode` (`text`/`keyframe`/`reference`), `seconds` `"4"`–`"12"`, `size` (`720P`/`1080P`), `aspect_ratio` | Reference client |
| Poll | `GET /v1/videos/{id}` (fallback `GET /root/agnesapi?video_id=…&model_name=…`) | Reference client |
| Status values | `processing`, `completed`, `failed`; `error` is an object `{code, message}` | Reference client |
| Result | `metadata.url` (also `video_url`, `url`, `remixed_from_video_id`) | Reference client |
| Duration | 4–12 s per clip for 2.5 Flash, 720p | Wiki + reference project |
| Rate limit | ~1 video submission/minute; ~16 requests/minute overall; multiple keys scale it | Reference project's own docs |
| Queue saturation | `503 {"code":"video_queue_full"}` — no task created, no quota consumed | Reference client |
| Free status | **Recurring free** with the rate limit above; no documented hard daily cap, so OmniReel keeps an *estimated* ledger | Wiki + reference project |
| Model 2.5 (non-Flash) | Exists; max clip length reported up to 20 s by the reference project, **not** confirmed in the current wiki → **ships disabled** | Reference project |

### Replicate — **implemented, trial only**
| Item | Value |
|---|---|
| API | `POST /v1/models/{owner}/{model}/predictions`, `GET /v1/predictions/{id}` |
| Auth | `Authorization: Token <token>` |
| Model studied | `minimax/video-01`: ~6 s, 720p, text- and image-to-video |
| Free status | Limited free test runs, then per-second billing → classified `trial`, excluded from free-only mode |
| Caveat | Input field names vary per model; OmniReel sends `prompt`, optional `first_frame_image` (data URI), `negative_prompt`, `seed` |

### Hugging Face Inference Providers — **implemented, not free recurring**
| Item | Value |
|---|---|
| Routing | `https://router.huggingface.co/...` proxy over fal/Replicate/Together/others |
| Billing | Pay-as-you-go; monthly credits only on PRO/Team/Enterprise |
| Shape | Some video tasks answer with bytes synchronously → adapter writes the file locally and reports a `file://` result |
| Status | `free_kind = none`, disabled by default |

### Runware — **implemented, unverified**
| Item | Value |
|---|---|
| Endpoint | `POST https://api.runware.ai/v1` with an array of task objects |
| Shape used | `taskType: videoInference` with async delivery, `getResponse` for polling, `videoURL` in the response |
| Free status | Startup programme credits are **one-off**, not recurring → `trial` |
| Required action | Confirm the exact model id and duration set in the dashboard, then enable it in `config/providers.json` |

### YepAPI-style gateways — **configurable template**
| Item | Value |
|---|---|
| Why not hardcoded | No single stable public contract for third-party video jobs; guessing endpoints would fake functionality |
| Approach | Base URL + submit/status paths from environment; response parsed for `id`/`task_id` and `url`/`video_url` |
| Free status | One-time credits → `trial`; never counted as recurring free usage |

### Explicitly not integrated as free
Kling, Runway, Veo/Sora, Pika, Luma and similar are paid or credit-metered at the API level even when consumer sites offer trials. They are not presented as free anywhere in OmniReel.

## 3. Rules OmniReel follows

1. **Never invent a quota.** Unpublished limits stay `NULL`; the ledger says *estimated*.
2. **Never treat one-off credits as recurring free usage.** `trial`/`promo`/`recurring`/`none` are distinct, and free-only mode spends only `recurring`/`promo`.
3. **Never bypass limits.** No automated account creation, no identity rotation. Multiple keys are used only where the provider's own tooling permits it.
4. **Never claim a verified date for an unverified model.** `verified_at` is either a real date from a real source or empty.
5. **Never present mock output as AI footage.** The mock provider is labelled everywhere it appears.

## 4. Re-verification checklist

When a provider changes, re-check in this order:

1. Console → model list → exact model ids and maximum clip seconds.
2. Submit one 5-second clip with `curl`; confirm the response field carrying the job id.
3. Poll until terminal; confirm the field carrying the finished URL.
4. Trigger one deliberate error (bad key) and confirm the status code mapped to `auth`.
5. Update `capabilities.py` (or `config/providers.json`) and set `verified_at`.
