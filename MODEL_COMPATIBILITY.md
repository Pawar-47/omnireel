# MODEL_COMPATIBILITY

The capability registry is the single source of truth for what the scheduler may ask of a model. It lives in `backend/omnireel/capabilities.py` and can be overridden without touching code via `config/providers.json`, or refreshed from a provider's live registry with `scripts/discover_models.py`.

## 1. What a model declares

| Field | Meaning | Used by |
|---|---|---|
| `provider`, `model` | Identity, also the key in the registry | Scheduler, adapters |
| `durations` | Every clip length the model can actually generate, in seconds | Planner timeline solver, scheduler snapping |
| `resolutions` | Supported output heights | Scheduler downgrade logic |
| `aspect_ratios` | Supported framing | Scheduler scoring |
| `text_to_video` | Can prompt-only generation | Scheduler eligibility |
| `image_to_video` | Accepts a first-frame image | Continuity |
| `reference_frame` | Accepts a keyframe/reference image | Continuity |
| `native_audio` | Produces its own audio track | Assembly (kept if present) |
| `quality`, `speed` | 1–5 subjective scores | Ranking |
| `free_kind` | `recurring` / `promo` / `trial` / `none` | Free-only filter |
| `daily_free_requests`, `total_free_requests` | Real published limits, or `null` when unknown | Quota ledger |
| `min_interval_seconds` | Minimum gap between calls | Rate-limit gate |
| `enabled` | Ships disabled until confirmed | Registry |
| `docs_url`, `verified_at`, `notes` | Provenance shown in the UI | Provider dashboard |

## 2. Shipped catalogue

Free sources first. `I2V` / `Ref` describe what **OmniReel's adapter actually sends**, not what the model theoretically accepts — a capability the controller does not exercise is not advertised, because the scheduler uses these columns to choose a continuity strategy.

| Provider | Model | Durations (s) | Res | T2V | I2V | Ref | Audio | Free kind | Enabled |
|---|---|---|---|---|---|---|---|---|---|
| agnes | `agnes-video-2.5-flash` | 4, 6, 8, 10, 12 | 720p | yes | yes | yes | no | recurring | **yes** |
| bigmodel | `cogvideox-flash` | 5 *(service-chosen)* | 1080p | yes | yes | yes | no | recurring | **yes** |
| modelscope | `MiniMax/MiniMax-H3` | 3, 5, 10 | 720p | yes | no | no | no | recurring | **yes** |
| agnes | `agnes-video-2.5` | 5, 10, 15, 20 *(unverified)* | 720p | yes | yes | yes | no | promo | no |
| bigmodel | `cogvideox-3` | 5, 10 | 720p/1080p | yes | yes | yes | yes | none | yes |
| pollinations | `google/veo-3.1-fast` | 4, 6, 8 | 720p/1080p | yes | no | no | yes | none | yes |
| pollinations | `kwaivgi/kling-v3.0-std` | 5, 10 *(range 3–15)* | 720p | yes | no | no | yes | none | yes |
| pollinations | `lightricks/ltx-2.5-pro` | 6, 8, 10 | 720p/1080p | yes | no | no | yes | none | yes |
| pollinations | `alibaba/wan-2.6` | 5, 10, **15** | 720p | yes | no | no | yes | none | yes |
| pollinations | `minimax/minimax-h3` | 5 | 480p/720p | yes | no | no | yes | none | yes |
| pollinations | `bytedance/seedance-2.0-mini` | 5, 10 *(range 4–10)* | 480p/720p | yes | no | no | yes | none | yes |
| replicate | `minimax/video-01` | 6 | 720p | yes | yes | yes | no | trial | yes |
| huggingface | `Wan-AI/Wan2.2-T2V-A14B` | 5 | 480p/720p | yes | no | no | no | none | no |
| runware | `video-default` | 5, 10 | 720p | yes | yes | no | no | trial | no |
| yepapi | `video-default` | 5, 8, 10 | 720p/1080p | yes | yes | no | no | trial | no |
| mock | `mock-video` | 4–12 | 480p/720p | yes | yes | yes | no | recurring | no (env flag) |

A provider also has to be *configured* (an API key present, or no key required) before any of its models can be ranked, so an adapter without a key costs nothing.

**`cogvideox-flash` is the one model whose duration is a planning assumption.** BigModel's request schema for it has no duration field — the service chooses the clip length — so OmniReel plans 5 s, measures what arrives with `ffprobe`, and trims. It is flagged in `notes` and visible on the provider dashboard.

## 3. Timeline consequences

| Effective free ladder | 120 s becomes | API calls |
|---|---|---|
| `[12, 10, 8, 6, 5, 4, 3]` (default: Agnes + BigModel + ModelScope) | 10 × 12 s | 10 |
| `[12, 10, 8, 6, 4]` (Agnes only) | 10 × 12 s | 10 |
| `[10, 6, 5, 3]` (BigModel + ModelScope) | 12 × 10 s | 12 |
| `[6, 5, 4, 3]` | 20 × 6 s | 20 |
| `[5]` | 24 × 5 s | 24 |

Because calls are scarce, the solver always tries the longest clip first and only drops to shorter lengths to land exactly on the target. The 15 s length exists only on a paid model, so free-only mode never plans around it.

## 4. Continuity support matrix

| Model | First-frame (image-to-video) | Reference/keyframe | Strategy used |
|---|---|---|---|
| Agnes 2.5 Flash | yes (`mode: keyframe`) | yes | Real frame hand-off |
| Zhipu `cogvideox-flash` / `-3` | yes (Base64 `image_url`) | first frame only | Real frame hand-off |
| Replicate MiniMax Video-01 | yes (`first_frame_image`, data URI) | — | Real frame hand-off |
| ModelScope MiniMax H3 | no — `media` needs a public URL | no | Prompt-only |
| Pollinations models | no — start frame needs a public URL | no | Prompt-only |
| Hugging Face WAN | no | no | Prompt-only |
| Runware / gateway | declared per model | — | Prompt-only unless enabled |

When a model declares no image conditioning, `Scheduler._build_request()` drops the reference rather than sending a field the API would reject. Continuity then rests on the shared world bible in every prompt.

The two "needs a public URL" cases are a real limitation, not an oversight: continuity frames are extracted locally, and uploading them to a third-party host to satisfy an API parameter would leak the project's footage to a service nobody asked for. Prompt-level consistency is the honest fallback.

## 5. Adding or refreshing a model

From a provider that publishes a capability registry:

```bash
.venv/bin/python scripts/discover_models.py            # what the registry says right now
.venv/bin/python scripts/discover_models.py --write    # create config/providers.json
```

By hand:

```json
{
  "models": [
    {
      "provider": "bigmodel",
      "model": "your-model-id",
      "label": "Human readable",
      "durations": [5, 10],
      "resolutions": ["720p"],
      "aspect_ratios": ["16:9", "9:16", "1:1"],
      "text_to_video": true,
      "image_to_video": true,
      "reference_frame": true,
      "quality": 4,
      "speed": 3,
      "free_kind": "recurring",
      "daily_free_requests": 40,
      "min_interval_seconds": 4,
      "enabled": true,
      "verified_at": "2026-11-22",
      "notes": "Confirmed in the console on <date>"
    }
  ]
}
```

Save as `config/providers.json`, restart the backend, and check the Providers tab. Only set `daily_free_requests` if the provider publishes it; otherwise leave it `null` and the ledger will report *estimated*.

## 6. Judgement calls worth knowing

- **Agnes Flash size enum** is `720P` (capital P). The adapter uppercases and defaults to 720p because Flash is fixed at that resolution.
- **Zhipu `size`** is an explicit pixel enum with no 1280×720 entry, so 16:9 maps to `1920x1080`, 9:16 to `1080x1920`, 1:1 to `1024x1024`.
- **ModelScope `size`** is `WxH` (`1280x720` / `720x1280` / `1024x1024`), and its poll needs the `X-ModelScope-Task-Type: video_generation` header or it reports the wrong task family.
- **Pollinations puts the prompt in the URL path**, percent-encoded; duration is a query parameter, and the async job flow needs `Prefer: respond-async`.
- **Negative prompts** are not part of the Agnes or BigModel contract; the adapter keeps them for the providers that accept them instead of sending an unsupported field.
- **Crossfades reduce runtime** by `(scenes − 1) × fade`. Default is off so the delivered film keeps its full length.
- **Clip duration is always recorded twice**: what was requested (`duration_seconds`) and what `ffprobe` measured (`actual_duration`). Assembly trusts the measurement.
