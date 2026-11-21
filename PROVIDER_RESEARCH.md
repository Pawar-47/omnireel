# PROVIDER_RESEARCH

Two audits so far. Every claim is tagged with where it came from; anything I could not confirm is marked **unverified** and ships disabled or flagged. Nothing here is invented.

- **Audit 1 — 2026-09-22:** Agnes AI and the original provider set.
- **Audit 2 — 2026-11-22:** the free-video hunt this file is mostly about now: Zhipu BigModel, ModelScope, Pollinations, plus a licence review of existing multi-provider repositories.

## 1. Reference repositories

### Repositories that already wire several models together

Audit 2 searched specifically for projects doing what OmniReel does. Real findings, with the licence as GitHub reports it — the licence decides whether code may be reused at all.

| Repository | Licence | What it is | Verdict |
|---|---|---|---|
| [AngeVox/AngeMedia-gateway](https://github.com/AngeVox/AngeMedia-gateway) | **Apache-2.0** | FastAPI media gateway, OpenAI-shaped `POST /v1/videos` + async task polling, `POST /v1/media/route` prompt router, SQLite/Redis queue. Same provider family OmniReel uses (Agnes, ModelScope, SiliconFlow, Pollinations). **Video support is Agnes-only**; other providers are documented adapter contracts. | **Closest match in existence.** Reusable under Apache-2.0, but its video path is narrower than ours, so nothing was copied — see below. |
| [samagra14/mediagateway](https://github.com/samagra14/mediagateway) | **MIT** | Unified `POST /v1/video/generations`, per-provider `VideoProvider` interface, cost tracking. Sora 2 / Runway / Kling only — **no free providers at all**. No failover: you pick the model. | Interface shape confirms ours; no code needed. |
| [umairs759/HIGGSFIELD-AI-OPEN-SOURCE](https://github.com/umairs759/HIGGSFIELD-AI-OPEN-SOURCE) | **none detected** | Next.js BYOK studio, "Smart Model Router" that only offers models whose key you hold. Would have been a good routing reference. | **Unusable:** README claims MIT but no LICENSE file exists, so default copyright applies. |
| [eliotus992/videogateway](https://github.com/eliotus992/videogateway) | **none** | "Unified AI video generation gateway, multi-provider, cost-optimized routing, async queue" (TypeScript, 0 stars). | Unlicensed — concept only. |
| [sakshi-infraxai/veltria-genmedia](https://github.com/sakshi-infraxai/veltria-genmedia) | **NOASSERTION** | Multi-provider media behind OpenAI-compatible gateways, exposed as MCP. Description claims Apache-2.0. | Treated as unlicensed until a LICENSE file exists. |
| [agmmnn/sub2gen](https://github.com/agmmnn/sub2gen) | MIT | Unified local API in front of **browser-scraped** providers (Google Flow for video). | **Rejected on policy**, not licence: scraping a consumer site is not an API allowance and would break our own rule 3. |
| [fal-ai-community/video-starter-kit](https://github.com/fal-ai-community/video-starter-kit) | MIT | Queues MiniMax / Hunyuan / LTX through fal's proxy. | Single vendor → nothing multi-provider to adopt. |
| [hao-ai-lab/FastVideo](https://github.com/hao-ai-lab/FastVideo) | Apache-2.0 | Unified **local** inference API for Wan/Hunyuan. | Rejected: requires a GPU. OmniReel never runs local inference. |
| [SaladTechnologies/comfyui-api](https://github.com/SaladTechnologies/comfyui-api) | MIT | Stateless REST wrapper over a local ComfyUI. | Rejected: local GPU, same reason. |
| [lcy362/agnes-video-generator](https://github.com/lcy362/agnes-video-generator) | MIT | Multi-scene video on the free Agnes API, checkpoint resume. | Reference for the Agnes contract (audit 1). |

**What was actually adopted from them.** One idea, and it is a good one: *do not hand-maintain a model table; read the capability registry the service publishes.* Every gateway above that stays current does this, and Pollinations exposes exactly such a registry, so audit 2 added [`scripts/discover_models.py`](scripts/discover_models.py), which turns `GET /video/models` into `config/providers.json` overrides. No source file was copied from any of these repositories. Our provider contract (`submit` / `poll` / `download`) independently converged on the same three-method shape the MIT repo uses — that convergence is the confirmation, not a copy.

### Audit 1 references

- **OpenMontageInc/OpenMontage — AGPL-3.0.** Decisive: no code, schema or template was copied, because that licence would force OmniReel to be AGPL. Concepts studied and adopted: multi-dimension provider scoring, approval gates before expensive work, mandatory `ffprobe` validation before declaring success.
- **rahulkumar8360/ai-video-studio** — lightweight FastAPI + React shape; confirms the stack choice for an i3 laptop.
- **AgnesAI-Labs/AgnesAI-Models** and the Agnes wiki — source of truth for the Agnes API surface.
- **backblaze-labs/awesome-video-generation** — discovery index only; every claim re-checked at the provider.

## 2. Provider audit

### Agnes AI — verified, enabled by default
| Item | Value | Source |
|---|---|---|
| Base URL | `https://apihub.agnes-ai.com/v1` (CN: `https://api.agnes-ai.cn`) | Vendor wiki |
| Auth | `Authorization: Bearer <key>` | Vendor wiki |
| Submit | `POST /v1/videos` → `{"video_id": …}` | Reference client |
| Payload | `model`, `prompt`, `mode` (`text`/`keyframe`/`reference`), `seconds` `"4"`–`"12"`, `size` (`720P`/`1080P`), `aspect_ratio` | Reference client |
| Poll | `GET /v1/videos/{id}` (fallback `GET /root/agnesapi?video_id=…`) | Reference client |
| Status | `processing`, `completed`, `failed`; `error` is `{code, message}` | Reference client |
| Result | `metadata.url` | Reference client |
| Duration | 4–12 s per clip, 720p | Wiki + reference project |
| Rate limit | ~1 video/minute; ~16 requests/minute overall | Reference project docs |
| Queue saturation | `503 {"code":"video_queue_full"}` — no task created | Reference client |
| Free status | **Recurring free**; no documented hard daily cap → *estimated* ledger | Wiki + reference project |

### Zhipu BigModel (智谱) — **verified free source, added in audit 2**
| Item | Value | Source |
|---|---|---|
| Base URL | `https://open.bigmodel.cn/api/paas/v4` | API reference (OpenAPI embedded in docs) |
| Auth | `Authorization: Bearer <api key>` | API reference |
| Submit | `POST /videos/generations` → `{"id", "request_id", "task_status"}` | API reference |
| Body | `model`, `prompt` (≤512 chars), `image_url` (URL **or Base64**), `size`, `fps` (30/60), `quality` (`speed`/`quality`) | API reference body schema |
| Poll | `GET /async-result/{id}` → `task_status` `PROCESSING`/`SUCCESS`/`FAIL` | API reference |
| Result | `video_result[0].url` (+ `cover_image_url`) | API reference response schema |
| **Price of `cogvideox-flash`** | **免费 (free)** — the plain word, not 限时免费 | `docs.bigmodel.cn/cn/guide/start/pricing`, 多模态生成 table |
| Neighbours | `CogVideoX-3` 1 元/次, `CogVideoX-2` 0.5 元/次, ViduQ1 2.5 元/次, Vidu2 1.25 元/次 | Same pricing table |
| `cogvideox-flash` duration | **Not documented at all.** The request schema for flash/2 has no `duration` field; the service picks the length | API reference body schema (CogVideoX tab) |
| `cogvideox-3` duration | 5 s or 10 s, up to 4K, first+last frame, native audio | `.../video-generation/cogvideox-3` |
| Model enum | `cogvideox-2`, `cogvideox-flash` (CogVideoX tab); `cogvideox-3` (its own tab) | API reference body schema |
| Rate limit | Per-account **concurrency**, not RPM. Error `1302` = too many in flight, `1305` = platform overload. Numbers live in the console only; CogVideoX-3 publishes V0–V3 = 5/10/15/20 | `docs.bigmodel.cn/cn/api/rate-limit` |

*Consequence for OmniReel:* flash is catalogued at 5 s as a **planning assumption**, flagged in `notes`; the delivered length is measured by `ffprobe` and trimmed, exactly like every other clip. This is the only model in the catalogue whose duration is a guess, and the UI shows it.

### ModelScope API-Inference — **verified free source, added in audit 2**
| Item | Value | Source |
|---|---|---|
| Base URL | `https://api-inference.modelscope.cn` | API-Inference intro page |
| Auth | `Authorization: Bearer <access token>`; account must bind and real-name verify an Alibaba Cloud account | Intro + limits page |
| Submit | `POST /v1/videos/generations` → `task_id` | Intro page (视频推理) |
| Poll | `GET /v1/tasks/{task_id}` with header `X-ModelScope-Task-Type: video_generation` | Intro page |
| Status | `PENDING` / `RUNNING` / `SUCCEED` / `FAILED` | Intro page |
| Result | `outputs.url` | Intro page |
| Duration | MiniMax family **3 / 5 / 10 s**; LTX-2.3 1–5 and 10; other models 1–5 | Intro page `duration` table |
| Free status | **Free but credit-metered** by a 魔粒 (magicube) wallet: 200/day for signing in + 50/day for binding Alibaba Cloud; short-term credits expire in 24 h | `docs/magicube/intro` |
| Per-call cost | 0.5 / 1 / 2 magicubes by model tier; image/video/music are priced on the model page (one example: 8 magicubes/call → ~30 videos/day) | Magicube intro + model page card |
| Historical "2000 calls/day" | **Third-party only** — the official limits page no longer states it | Not used |
| Verified video ids | `MiniMax/MiniMax-H3` (the doc's own example) and `muse/Wan2.2-TI2V-5B-FP8` | Model pages carry the ⚡ API-Inference badge |
| Not served | `Wan-AI/Wan2.1-T2V-1.3B`, `Wan-AI/Wan2.2-T2V-A14B`, `ZhipuAI/CogVideoX-5b` — weights only, no API-Inference badge | Model pages |
| Terms | Non-commercial, non-profit use | Limits page |

### Pollinations — **verified paid aggregator, added in audit 2**
| Item | Value | Source |
|---|---|---|
| Base URL | `https://gen.pollinations.ai` | API docs |
| Auth | `Authorization: Bearer sk_…` (secret key) | API docs |
| Submit | `GET /video/{prompt}?model=…&duration=…` → MP4 bytes, or `202` + job id with `Prefer: respond-async` | API docs |
| Poll | `GET /v1/jobs/{id}` → `status`, `result_url` (409 while pending/not ready) | API docs |
| Catalogue | `GET /video/models` — machine-readable durations, resolutions, frame controls, price, `paid_only` | Live registry |
| Video models | 28, incl. Veo 3.1 Fast, Kling 3.0, Seedance 2.0/2.5, WAN 2.2–3.0, LTX 2.5 Pro, MiniMax H3, Grok Imagine, FLUX 3, HeyGen | Live registry |
| **Free status** | **Every video model is `paid_only: true`** and billed in "pollen" (the Quest balance cannot pay for video) | Live registry |
| Verified durations | veo-3.1-fast `allowed_durations` 4/6/8; ltx-2.5-pro 6/8/10; wan-2.6 5/10/15; minimax-h3 fixed 5; kling 3–15 range; seedance-2.0-mini 4–10 | Live registry |

*Why it is here despite being paid:* one key reaches every major hosted model, including the only 15-second clip in the catalogue. It is a gap-filler for a project whose free sources are all resting, never a free source.

### Replicate — implemented, trial only
`POST /v1/models/{owner}/{model}/predictions`, `GET /v1/predictions/{id}`, `Authorization: Token <token>`. Model studied: `minimax/video-01` — ~6 s, 720p, text- and image-to-video. Limited free test runs, then per-second billing → `trial`, excluded from free-only mode.

### Hugging Face Inference Providers — implemented, not free recurring
Pay-as-you-go; included monthly credits are **PRO/Team/Enterprise only** (`$2.00`/month; free accounts: *"None"* — re-confirmed on the pricing page in audit 2). Some video tasks answer with bytes synchronously → the adapter writes the file locally and reports a `file://` result. `free_kind = none`, disabled.

### Runware — implemented, unverified
`POST https://api.runware.ai/v1` with an array of task objects; `taskType: videoInference` with async delivery, `getResponse` for polling, `videoURL` in the response. Startup credits are **one-off** → `trial`. Confirm the model id in the dashboard before enabling.

### YepAPI-style gateways — configurable template
No single stable public contract for third-party video jobs, so guessing endpoints would fake functionality. Base URL + submit/status paths come from the environment. One-time credits → `trial`.

## 3. Evaluated and deliberately not integrated

Checked at the API level, not the marketing page. This list is the honest answer to "why only three new providers".

| Candidate | Finding | Verdict |
|---|---|---|
| **SiliconFlow** | $1 in free credits (one-off, not recurring); `Wan2.2-T2V-A14B` / `I2V` at **$0.29 per video** | `trial` — exhausted by ~3 clips; not worth an adapter before one is confirmed |
| **WaveSpeed** | $1 one-off, no card; video from $0.02/s, and "some premium models may not be available with trial credits" | `trial`, endpoint shape unverified → not integrated |
| **fal.ai** | Pure pay-per-use, $0.015–0.14/s, **no free credits on the pricing page** | paid; already reachable via Pollinations |
| **Novita** | Pay-as-you-go; "free to start" marketing with no documented free video allowance | not integrated |
| **Segmind** | Plans from $10 pay-as-you-go; no daily/monthly free credit tier | not integrated |
| **DeepInfra** | "You have to add a card or pre-pay" — has a T2V category, zero free allowance | not integrated |
| **Together AI** | Video billed **per video** (Seedance 2.5 $0.115, Veo 3.1 $0.08, Wan 2.7 $0.10); only one free *text* model | not integrated |
| **Google Gemini / Veo** | Veo 3.1 Preview was shut down 22 Oct 2026; video is now `gemini-omni-1.2-flash`, marked **"Free Tier: Not available"** | not free at the API level |
| **Kie.ai** | Free *playground* testing; production needs credits | not integrated |
| **JSON2Video** | 600 free rendering seconds — but it is **assembly**, not text-to-video diffusion | out of scope (FFmpeg already does this locally) |
| **AI Horde** | Genuinely free and crowdsourced, but its columns are **Image / Text / Alchemy — no video** | not applicable |
| **Cloudflare Workers AI** | 10,000 Neurons/day free, but the price list contains **no video-generation model** | not applicable |
| **Kling / Runway / Pika / Luma / Vidu** | Paid or credit-metered at the API level even where the consumer site has a trial | never presented as free |

## 4. Rules OmniReel follows

1. **Never invent a quota.** Unpublished limits stay `NULL`; the ledger says *estimated*.
2. **Never treat one-off credits as recurring free usage.** `recurring` / `promo` / `trial` / `none` are distinct; free-only mode spends only `recurring` and `promo`.
3. **Never bypass limits.** No automated account creation, no identity rotation, no scraping a consumer site to fake an API.
4. **Never reuse code a licence does not allow.** AGPL and no-licence repositories are read for concepts only.
5. **Never claim a verified date for an unverified number.** `verified_at` is either a real date from a real source or empty — and a model whose *duration* is unpublished says so in `notes`.
6. **Never present mock output as AI footage.**

## 5. Re-verification checklist

1. Console → model list → exact model ids and maximum clip seconds.
2. Submit one 5-second clip with `curl`; confirm the field carrying the job id.
3. Poll to terminal; confirm the field carrying the finished URL.
4. Trigger one deliberate error (bad key) and confirm it maps to `auth`.
5. Update `capabilities.py` (or run `scripts/discover_models.py`) and set `verified_at`.
