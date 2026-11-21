# MODEL_COMPATIBILITY

The capability registry is the single source of truth for what the scheduler may ask of a model. It lives in `backend/omnireel/capabilities.py` and can be overridden without touching code via `config/providers.json`.

## 1. What a model declares

| Field | Meaning | Used by |
|---|---|---|
| `provider`, `model` | Identity, also the key in the registry | Scheduler, adapters |
| `durations` | Every clip length the model can actually generate, in seconds | Planner timeline solver, scheduler snapping |
| `resolutions` | Supported output heights | Scheduler downgrade logic |
| `aspect_ratios` | Supported framing | Scheduler scoring |
| `text_to_video` | Can prompt-only generation | Scheduler eligibility |
| `image_to_video` | Accepts a first-frame image | Continuity |
| `reference_frame` | Accepts a keyframe/reference image pair | Continuity |
| `native_audio` | Produces its own audio track | Assembly (kept if present) |
| `quality`, `speed` | 1–5 subjective scores | Ranking |
| `free_kind` | `recurring` / `promo` / `trial` / `none` | Free-only filter |
| `daily_free_requests`, `total_free_requests` | Real published limits, or `null` when unknown | Quota ledger |
| `min_interval_seconds` | Minimum gap between calls | Rate-limit gate |
| `enabled` | Ships disabled until confirmed | Registry |
| `docs_url`, `verified_at`, `notes` | Provenance shown in the UI | Provider dashboard |

## 2. Shipped catalogue

| Provider | Model | Durations (s) | Res | T2V | I2V | Ref | Audio | Free kind | Enabled |
|---|---|---|---|---|---|---|---|---|---|
| agnes | `agnes-video-2.5-flash` | 4, 6, 8, 10, 12 | 720p | yes | yes | yes | no | recurring | **yes** |
| agnes | `agnes-video-2.5` | 5, 10, 15, 20 *(unverified)* | 720p | yes | yes | yes | no | promo | no |
| replicate | `minimax/video-01` | 6 | 720p | yes | yes | yes | no | trial | yes |
| huggingface | `Wan-AI/Wan2.2-T2V-A14B` | 5 | 480p/720p | yes | no | no | no | none | no |
| runware | `video-default` | 5, 10 | 720p | yes | yes | no | no | trial | no |
| yepapi | `video-default` | 5, 8, 10 | 720p/1080p | yes | yes | no | no | trial | no |
| mock | `mock-video` | 4–12 | 480p/720p | yes | yes | yes | no | recurring | no (env flag) |

A provider also has to be *configured* (an API key present, or no key required) before any of its models can be ranked. With only `AGNES_API_KEY` set, the effective ladder is `[12, 10, 8, 6, 4]`.

## 3. Timeline consequences

| Effective ladder | 120 s becomes | API calls |
|---|---|---|
| `[12, 10, 8, 6, 4]` | 10 × 12 s | 10 |
| `[10, 8, 6, 5, 4]` | 12 × 10 s | 12 |
| `[6, 5]` | 20 × 6 s | 20 |
| `[5]` | 24 × 5 s | 24 |

Because calls are scarce, the solver always tries the longest clip first and only drops to shorter lengths to land exactly on the target.

## 4. Continuity support matrix

| Model | First-frame (image-to-video) | Reference/keyframe | Strategy used |
|---|---|---|---|
| Agnes 2.5 Flash | yes (`mode: keyframe`, `first_frame`) | yes | Real frame hand-off |
| Replicate MiniMax Video-01 | yes (`first_frame_image`, data URI) | — | Real frame hand-off |
| Hugging Face WAN | no | no | Prompt-only |
| Runware / gateway | declared per model | — | Prompt-only unless enabled |

When a model declares no image conditioning, `Scheduler._build_request()` drops the reference rather than sending a field the API would reject. Continuity then rests on the shared world bible in every prompt.

## 5. Adding a model

```json
{
  "models": [
    {
      "provider": "agnes",
      "model": "your-model-id",
      "label": "Human readable",
      "durations": [6, 10],
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
      "verified_at": "2026-10-01",
      "notes": "Confirmed in the console on <date>"
    }
  ]
}
```

Save as `config/providers.json`, restart the backend, and check the Providers tab. Only set `daily_free_requests` if the provider publishes it; otherwise leave it `null` and the ledger will report *estimated*.

## 6. Judgement calls worth knowing

- **Agnes Flash size enum** is `720P` (capital P). The adapter uppercases and defaults to 720p because Flash is fixed at that resolution.
- **Aspect ratios** are sent as `16:9`, `9:16`, `1:1` — the enum Agnes 2.5 accepts.
- **Negative prompts** are not part of the Agnes 2.5 contract; the adapter keeps them for other providers and drops them for Agnes instead of sending an unsupported field.
- **Crossfades reduce runtime** by `(scenes − 1) × fade`. Default is off so the delivered film keeps its full length.
- **Clip duration is always recorded twice**: what was requested (`duration_seconds`) and what `ffprobe` measured (`actual_duration`). Assembly trusts the measurement.
