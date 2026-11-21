"""Storyboard planner.

One creative prompt becomes a complete, timed shot list that fills the requested
duration exactly. The planner never hardcodes ten seconds: it asks the
capability registry which clip lengths the enabled models can actually produce
and solves the timeline as a small dynamic program, so a pool of 5 s and 6 s
models yields 24 or 20 shots instead of 12.

The plan is deliberately provider agnostic - the scheduler assigns models later,
and re-assigns them on failover, without ever touching the creative direction.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Iterable

from .capabilities import CapabilityRegistry

# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------

STYLE_PROFILES: dict[str, dict[str, str]] = {
    "cinematic": {
        "label": "Cinematic",
        "visual": "cinematic 35mm film look, anamorphic lens, shallow depth of field, "
        "subtle grain, rich contrast",
        "grade": "teal and amber colour grade",
        "motion": "deliberate, fluid camera movement",
        "negative": "text, watermark, logo, subtitles, distorted faces, extra limbs, "
        "flicker, low quality, VHS artifacts",
    },
    "documentary": {
        "label": "Documentary",
        "visual": "documentary photography, natural light, 50mm lens, realistic texture, "
        "observational framing",
        "grade": "neutral, slightly desaturated grade",
        "motion": "patient handheld observation",
        "negative": "text, watermark, logo, obvious CGI, plastic skin, oversaturation",
    },
    "anime": {
        "label": "Anime",
        "visual": "hand-drawn anime key art, clean linework, cel shading, painted backgrounds",
        "grade": "vivid anime palette with bloom",
        "motion": "snappy anime timing with held poses",
        "negative": "photo realism, text, watermark, extra fingers, melting faces",
    },
    "noir": {
        "label": "Film noir",
        "visual": "high-contrast black and white, hard key light, deep shadows, venetian blind patterns",
        "grade": "monochrome with silver highlights",
        "motion": "slow, deliberate camera creep",
        "negative": "colour, text, watermark, modern devices, flat lighting",
    },
    "scifi": {
        "label": "Sci-fi",
        "visual": "high-end sci-fi production design, volumetric light, practical lens flares, "
        "detailed surfaces",
        "grade": "cool cyan and magenta grade",
        "motion": "smooth mechanised camera moves",
        "negative": "text, watermark, cheap CGI, warped geometry, low detail",
    },
    "nature": {
        "label": "Nature",
        "visual": "wildlife documentary footage, long lens, crisp detail, natural textures",
        "grade": "warm natural grade",
        "motion": "telephoto tracking and gentle drift",
        "negative": "text, watermark, humans in frame, unnatural colours",
    },
    "product": {
        "label": "Product film",
        "visual": "studio product photography, controlled reflections, clean seamless background, "
        "macro detail",
        "grade": "clean high-key grade",
        "motion": "precise motorised slider and turntable moves",
        "negative": "text, watermark, messy background, fingerprints, distorted logo",
    },
    "explainer": {
        "label": "Explainer",
        "visual": "clean motion-graphics styling, soft studio lighting, minimal sets, clear silhouettes",
        "grade": "bright neutral grade",
        "motion": "clear, readable camera moves",
        "negative": "text, watermark, clutter, illegible shapes",
    },
    "music_video": {
        "label": "Music video",
        "visual": "stylish music-video lighting, saturated practicals, rhythmic editing, bold wardrobe",
        "grade": "punchy high-saturation grade",
        "motion": "beat-driven camera energy",
        "negative": "text, watermark, dull lighting, static framing",
    },
    "whimsical": {
        "label": "Whimsical 3D",
        "visual": "stylised 3D animation, soft shapes, tactile materials, storybook colour",
        "grade": "pastel grade with gentle bloom",
        "motion": "playful, buoyant camera moves",
        "negative": "photorealism, text, watermark, harsh shadows, broken anatomy",
    },
}

DEFAULT_STYLE = "cinematic"


# --------------------------------------------------------------------------
# Narrative beats
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Beat:
    name: str
    shot: str
    camera: str
    motion: str


#: A reusable twelve-beat narrative spine. Longer videos repeat the middle beats
#: with escalating detail; shorter videos sample this list evenly.
BEATS: tuple[Beat, ...] = (
    Beat(
        "establish",
        "Wide establishing shot revealing the whole location and its scale",
        "slow aerial drift forward",
        "atmosphere moves through the frame - haze, water, foliage, dust",
    ),
    Beat(
        "approach",
        "Medium-wide shot as the viewpoint moves toward the main subject",
        "steady push in at eye level",
        "the subject is introduced in silhouette before detail resolves",
    ),
    Beat(
        "detail",
        "Close detail shot of a texture that belongs to the location",
        "macro slider move with shallow focus",
        "small motion only - particles, moisture, breeze, flicker",
    ),
    Beat(
        "action",
        "The first concrete action of the story, framed clearly",
        "handheld follow keeping the subject centred",
        "the subject commits to a purposeful movement",
    ),
    Beat(
        "pivot",
        "A turn in the story: something unexpected enters the frame",
        "slow orbit around the point of interest",
        "the new element interrupts the established rhythm",
    ),
    Beat(
        "encounter",
        "The subject meets the central element of the story face to face",
        "two-shot profile framing at subject height",
        "both subject and encounter element react to each other",
    ),
    Beat(
        "texture",
        "Rhythmic insert shot that deepens the world",
        "lateral tracking past foreground elements",
        "layered movement in foreground and background",
    ),
    Beat(
        "escalation",
        "The stakes rise: a larger version of the earlier action",
        "fast dolly-in with rising energy",
        "the action repeats bigger, faster and louder",
    ),
    Beat(
        "climax",
        "The single most striking image of the piece",
        "heroic low-angle crane up",
        "everything in frame moves at once",
    ),
    Beat(
        "aftermath",
        "Quiet aftermath once the peak has passed",
        "gentle pull back, static horizon",
        "residual motion only - settling, drifting, cooling",
    ),
    Beat(
        "return",
        "Wide return to the opening location, now changed",
        "slow aerial retreat",
        "the location is calm again but visibly transformed",
    ),
    Beat(
        "close",
        "Final cinematic frame that holds and fades",
        "locked-off wide with a very slow drift",
        "the subject holds still as light shifts across the frame",
    ),
)

CAMERA_VARIANTS: tuple[str, ...] = (
    "slow dolly forward",
    "smooth lateral tracking shot",
    "gentle pull back revealing the wider scene",
    "handheld follow at shoulder height",
    "orbiting arc around the subject",
    "low-angle tilt up",
    "high-angle top-down drift",
    "static locked-off frame with subject motion",
)

LIGHTING_LIBRARY: dict[str, str] = {
    "night": "moonlit blue key with warm practical highlights, deep shadows",
    "sunset": "low golden-hour sun, long shadows, warm rim light, hazy highlights",
    "sunrise": "soft dawn light, cool shadows, gentle haze",
    "dawn": "pale first light, mist, low contrast shadows",
    "golden": "warm directional light with atmospheric glow",
    "rain": "diffused overcast light with wet reflective surfaces",
    "storm": "dramatic storm light, flashes, heavy atmosphere",
    "underwater": "caustic blue-green light with drifting particulate",
    "neon": "saturated neon practicals with coloured reflections",
    "candle": "warm flickering point light, deep falloff",
    "snow": "cool high-key snow light with soft shadows",
    "fog": "diffuse fog light, layered depth, visible beams",
    "interior": "soft window light with practical lamps filling the background",
}
DEFAULT_LIGHTING = "natural directional light with believable shadows and atmospheric depth"

TIME_OF_DAY_WORDS = {
    "night": ("night", "midnight", "dark", "starry"),
    "sunset": ("sunset", "dusk", "golden hour", "evening"),
    "sunrise": ("sunrise", "first light"),
    "dawn": ("dawn", "early morning"),
    "rain": ("rain", "monsoon", "drizzle", "storm"),
    "underwater": ("underwater", "reef", "lagoon", "submerged"),
    "neon": ("neon", "cyberpunk", "hologram"),
    "snow": ("snow", "arctic", "glacier", "winter", "ice"),
    "fog": ("fog", "mist", "haze", "cloud"),
}

STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "about",
    "with",
    "and",
    "in",
    "on",
    "at",
    "to",
    "for",
    "that",
    "this",
    "cinematic",
    "documentary",
    "video",
    "film",
    "movie",
    "clip",
    "trailer",
    "exploring",
    "explore",
    "following",
    "showing",
    "make",
    "create",
    "generate",
}


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class Shot:
    index: int
    duration: float
    description: str
    prompt: str
    camera: str
    subject_motion: str
    lighting: str
    environment: str
    continuity: str
    beat: str = ""

    def to_dict(self) -> dict:
        return {
            "index_no": self.index,
            "duration_seconds": self.duration,
            "description": self.description,
            "prompt": self.prompt,
            "camera": self.camera,
            "subject_motion": self.subject_motion,
            "lighting": self.lighting,
            "environment": self.environment,
            "continuity": self.continuity,
            "beat": self.beat,
        }


@dataclass
class Storyboard:
    title: str
    prompt: str
    style: str
    target_seconds: float
    world_bible: dict
    shots: list[Shot] = field(default_factory=list)

    @property
    def planned_seconds(self) -> float:
        return round(sum(shot.duration for shot in self.shots), 3)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "prompt": self.prompt,
            "style": self.style,
            "target_seconds": self.target_seconds,
            "planned_seconds": self.planned_seconds,
            "world_bible": self.world_bible,
            "shots": [shot.to_dict() for shot in self.shots],
        }


# --------------------------------------------------------------------------
# Timeline solving
# --------------------------------------------------------------------------


def allocate_durations(
    target_seconds: float,
    ladder: Iterable[float],
    *,
    max_shots: int = 60,
    tolerance: float = 0.75,
) -> list[float]:
    """Split *target_seconds* into clip lengths the models can actually produce.

    Returns the shot durations, longest first-compatible, summing to the target
    (or within ``tolerance`` when the model set cannot hit it exactly).
    """
    target = max(1.0, float(target_seconds))
    values = sorted({round(float(v), 3) for v in ladder if 0 < float(v) <= target}, reverse=True)
    if not values:
        values = [round(min(10.0, target), 3)]

    scale = 100
    goal = int(round(target * scale))
    steps = sorted({int(round(v * scale)) for v in values}, reverse=True)
    slack = int(round(max(tolerance, 0.0) * scale)) + max(steps)
    limit = min(goal + slack, goal + max(steps) * 2)

    # Breadth-first over reachable totals; the first combo found for a total uses
    # the longest clips first, which keeps the number of paid/free API calls low.
    combos: dict[int, tuple[int, ...]] = {0: ()}
    frontier = [0]
    while frontier:
        total = frontier.pop(0)
        combo = combos[total]
        if len(combo) >= max_shots:
            continue
        for step in steps:
            nxt = total + step
            if nxt > limit or nxt in combos:
                continue
            combos[nxt] = combo + (step,)
            frontier.append(nxt)

    best_total = min(
        combos,
        key=lambda total: (abs(total - goal), len(combos[total]), -total),
    )
    durations = [step / scale for step in combos[best_total]]

    if not durations:
        return [round(target, 3)]
    return [round(value, 3) for value in durations]


# --------------------------------------------------------------------------
# Prompt analysis
# --------------------------------------------------------------------------


def clean_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", (prompt or "").strip())


def derive_title(prompt: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", clean_prompt(prompt))]
    meaningful = [w for w in words if w.lower() not in STOPWORDS]
    picked = meaningful[:4] or words[:4] or ["Untitled"]
    title = " ".join(picked).title()
    return title[:64] or "Untitled Project"


def detect_lighting(prompt: str) -> str:
    lowered = prompt.lower()
    for key, words in TIME_OF_DAY_WORDS.items():
        if any(word in lowered for word in words):
            return LIGHTING_LIBRARY.get(key, DEFAULT_LIGHTING)
    return DEFAULT_LIGHTING


def analyse_prompt(prompt: str, style: str) -> dict:
    """Extract the reusable creative facts that every shot must honour."""
    text = clean_prompt(prompt)
    lowered = text.lower()
    profile = STYLE_PROFILES.get(style, STYLE_PROFILES[DEFAULT_STYLE])
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", text) if w.lower() not in STOPWORDS]
    subject = " ".join(words[:6]) or text or "the subject"
    setting_words = [
        w
        for w in re.findall(r"[A-Za-z0-9'-]+", text)
        if w.lower() not in STOPWORDS and len(w) > 3
    ][-6:]
    setting = " ".join(setting_words) if setting_words else subject

    lighting = detect_lighting(text)
    return {
        "subject": subject,
        "setting": setting,
        "lighting": lighting,
        "style_visual": profile["visual"],
        "grade": profile["grade"],
        "motion_bias": profile["motion"],
        "negative_prompt": profile["negative"],
        "palette": _palette_hint(lowered),
        "aspect_hint": "vertical 9:16" if "vertical" in lowered else "widescreen 16:9",
    }


def _palette_hint(lowered_prompt: str) -> str:
    for words, palette in (
        (("jungle", "forest", "island", "tropical", "leaf"), "deep greens, wet stone grey, humid amber light"),
        (("desert", "sand", "dune"), "ochre, rust and bleached bone white"),
        (("city", "urban", "street", "neon"), "cool concrete greys with saturated neon accents"),
        (("space", "orbit", "planet", "star"), "deep navy, instrument white and cold cyan glow"),
        (("ocean", "sea", "reef", "wave"), "turquoise water, foam white and sunlit sand"),
        (("snow", "arctic", "ice", "glacier"), "pale blue shadow, brilliant white and grey rock"),
        (("fire", "volcano", "lava"), "molten orange against charcoal black"),
    ):
        if any(word in lowered_prompt for word in words):
            return palette
    return "cohesive naturalistic palette with one accent colour"


# --------------------------------------------------------------------------
# Beat selection
# --------------------------------------------------------------------------


def beats_for(count: int) -> list[Beat]:
    """Choose *count* beats that still tell a complete arc."""
    count = max(1, count)
    if count == 1:
        return [BEATS[0]]
    if count <= len(BEATS):
        if count == 2:
            return [BEATS[0], BEATS[-1]]
        step = (len(BEATS) - 1) / (count - 1)
        indices = [round(i * step) for i in range(count)]
        deduped: list[int] = []
        for index in indices:
            while index in deduped:
                index += 1
            deduped.append(min(index, len(BEATS) - 1))
        return [BEATS[index] for index in deduped]

    selected = list(BEATS[:-1])
    extra = count - len(BEATS)
    development = BEATS[3:9]
    for step in range(extra):
        template = development[step % len(development)]
        position = min(4 + step, len(selected))
        selected.insert(
            position,
            Beat(
                f"{template.name}-{step + 1}",
                f"{template.shot}, continued development of the same action",
                template.camera,
                template.motion,
            ),
        )
    # The closing shot always stays last, however many development beats were added.
    selected.append(BEATS[-1])
    return selected[:count]


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


def build_shot_prompt(
    *,
    index: int,
    beat: Beat,
    duration: float,
    world: dict,
    style: str,
    aspect_ratio: str,
) -> str:
    rng = random.Random(f"{world['subject']}|{style}|{index}".lower())
    camera = beat.camera if index % 3 else rng.choice(CAMERA_VARIANTS)
    continuity = (
        f"Continuity: keep the same subject ({world['subject']}), the same wardrobe and "
        f"props, the same location, the same colour palette ({world['palette']}) and the "
        f"same {world['grade']} as every other shot in this sequence."
    )
    parts = [
        f"Shot {index + 1}: {beat.shot}.",
        f"Subject: {world['subject']}.",
        f"Setting: {world['setting']}.",
        f"Action: {beat.motion}.",
        f"Camera: {camera}.",
        f"Lighting: {world['lighting']}.",
        f"Look: {world['style_visual']}, {world['grade']}.",
        f"Duration: about {duration:g} seconds, one continuous take, no cuts.",
        continuity,
    ]
    prompt = " ".join(parts)
    if aspect_ratio == "9:16":
        prompt += " Vertical 9:16 composition with headroom for a centred subject."
    return prompt[:1200]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def plan_storyboard(
    prompt: str,
    *,
    target_seconds: float = 120.0,
    style: str = DEFAULT_STYLE,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    registry: CapabilityRegistry | None = None,
    free_only: bool = True,
    max_shot_seconds: float | None = None,
) -> Storyboard:
    """Turn one prompt into a fully timed storyboard."""
    text = clean_prompt(prompt)
    if not text:
        raise ValueError("prompt must not be empty")

    style_key = style if style in STYLE_PROFILES else DEFAULT_STYLE
    world = analyse_prompt(text, style_key)

    ladder: list[float] = []
    if registry is not None:
        ladder = [
            value
            for value in registry.durations_ladder(free_only=free_only)
            if value <= (max_shot_seconds or value)
        ]
    if not ladder:
        ladder = [10.0, 8.0, 6.0, 5.0, 4.0]

    durations = allocate_durations(target_seconds, ladder)
    beats = beats_for(len(durations))

    shots: list[Shot] = []
    for index, (duration, beat) in enumerate(zip(durations, beats)):
        shot_prompt = build_shot_prompt(
            index=index,
            beat=beat,
            duration=duration,
            world=world,
            style=style_key,
            aspect_ratio=aspect_ratio,
        )
        shots.append(
            Shot(
                index=index,
                duration=duration,
                description=f"{beat.shot} - {beat.name}",
                prompt=shot_prompt,
                camera=beat.camera,
                subject_motion=beat.motion,
                lighting=world["lighting"],
                environment=world["setting"],
                continuity=(
                    "Inherit the previous shot's final frame as the visual reference: "
                    "same subject, wardrobe, palette, location and grade."
                ),
                beat=beat.name,
            )
        )

    return Storyboard(
        title=derive_title(text),
        prompt=text,
        style=style_key,
        target_seconds=float(target_seconds),
        world_bible={
            **world,
            "style": style_key,
            "style_label": STYLE_PROFILES[style_key]["label"],
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "target_seconds": float(target_seconds),
            "ladder": sorted({float(v) for v in ladder}, reverse=True),
        },
        shots=shots,
    )


def shots_per_minute(shots: Iterable[Shot]) -> float:
    total = sum(shot.duration for shot in shots)
    return (len(list(shots)) / total * 60.0) if total else 0.0


def expected_call_count(target_seconds: float, ladder: Iterable[float]) -> int:
    """How many API calls a target duration needs with this ladder."""
    return len(allocate_durations(target_seconds, ladder)) or math.ceil(target_seconds / 10)
