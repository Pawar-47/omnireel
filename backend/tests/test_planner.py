"""Storyboard planner: the two-minute guarantee and dynamic segment lengths."""

from __future__ import annotations

from omnireel.capabilities import CapabilityRegistry, ModelSpec
from omnireel.planner import (
    STYLE_PROFILES,
    allocate_durations,
    beats_for,
    plan_storyboard,
)


def registry_with(durations: list[float], *, provider: str = "x") -> CapabilityRegistry:
    return CapabilityRegistry(
        [
            ModelSpec(
                provider=provider,
                model="m",
                durations=durations,
                free_kind="recurring",
                enabled=True,
                verified_at="test",
            )
        ]
    )


def test_ten_second_models_need_twelve_shots():
    storyboard = plan_storyboard(
        "a quiet city at night",
        target_seconds=120,
        registry=registry_with([10]),
    )
    assert len(storyboard.shots) == 12
    assert round(sum(shot.duration for shot in storyboard.shots), 3) == 120


def test_six_second_models_need_twenty_shots():
    durations = allocate_durations(120, [6])
    assert len(durations) == 20
    assert sum(durations) == 120


def test_five_second_models_need_twenty_four():
    durations = allocate_durations(120, [5])
    assert len(durations) == 24
    assert sum(durations) == 120


def test_mixed_ladder_still_hits_the_target():
    durations = allocate_durations(120, [8, 6, 5])
    assert round(sum(durations), 3) == 120
    assert all(value in {8, 6, 5} for value in durations)


def test_ladder_prefers_the_longest_available_clip():
    durations = allocate_durations(120, [12, 10, 6, 5])
    assert durations[0] == 12
    assert len(durations) == 10


def test_unreachable_target_lands_close():
    durations = allocate_durations(100, [7])
    assert abs(sum(durations) - 100) <= 3.5


def test_storyboard_total_matches_request_for_any_target():
    for target in (30, 60, 90, 120, 180):
        storyboard = plan_storyboard(
            "a lighthouse keeper befriends a storm petrel",
            target_seconds=target,
            registry=registry_with([12, 10, 8, 6, 5, 4]),
        )
        total = sum(shot.duration for shot in storyboard.shots)
        assert abs(total - target) < 0.5, target


def test_arc_opens_and_closes():
    storyboard = plan_storyboard(
        "a journey through a rainforest",
        target_seconds=120,
        registry=registry_with([10]),
    )
    assert storyboard.shots[0].beat == "establish"
    assert storyboard.shots[-1].beat == "close"


def test_long_video_repeats_development_beats():
    beats = beats_for(20)
    assert len(beats) == 20
    assert beats[0].name == "establish"
    assert beats[-1].name == "close"


def test_shots_carry_continuity_and_style():
    storyboard = plan_storyboard(
        "a tropical island documentary at sunset",
        target_seconds=60,
        style="documentary",
        registry=registry_with([10]),
    )
    shot = storyboard.shots[0]
    assert "Continuity" in shot.prompt
    assert "documentary" in shot.prompt.lower()
    assert shot.lighting != ""
    assert storyboard.world_bible["style"] == "documentary"
    # The sunset profile resolves to golden-hour lighting rather than the word "sunset".
    assert "golden" in storyboard.world_bible["lighting"].lower()
    assert shot.lighting == storyboard.world_bible["lighting"]


def test_unknown_style_falls_back_to_cinematic():
    storyboard = plan_storyboard(
        "a robot in a field",
        target_seconds=30,
        style="not-a-style",
        registry=registry_with([10]),
    )
    assert storyboard.style == "cinematic"
    assert "cinematic" in STYLE_PROFILES


def test_vertical_video_gets_vertical_prompts():
    storyboard = plan_storyboard(
        "a dancer in the rain",
        target_seconds=30,
        aspect_ratio="9:16",
        registry=registry_with([10]),
    )
    assert "9:16" in storyboard.shots[0].prompt


def test_empty_prompt_is_rejected():
    try:
        plan_storyboard("   ", target_seconds=30)
    except ValueError as exc:
        assert "prompt" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty prompt should raise")



def test_camera_field_is_the_one_written_into_the_prompt():
    """The prompt and the storyboard must agree on the camera move.

    Every third shot takes a varied camera; this used to be drawn inside
    build_shot_prompt only, so Shot.camera kept the beat's move and the two
    disagreed - a camera edited on screen never reached the model.
    """
    storyboard = plan_storyboard(
        "a red fox in a snowy forest at dusk",
        target_seconds=60,
        registry=registry_with([10]),
    )
    assert len(storyboard.shots) >= 4  # past the index%3 == 0 variants
    for shot in storyboard.shots:
        assert f"Camera: {shot.camera}." in shot.prompt, shot.index


def test_setting_phrase_does_not_collect_verbs():
    """The location phrase must not be built from the verb tail of the prompt."""
    storyboard = plan_storyboard(
        "A red fox trots through a snowy pine forest at dusk, stops, "
        "and looks straight at the camera",
        target_seconds=20,
        registry=registry_with([12, 8]),
    )
    setting = storyboard.world_bible["setting"].lower()
    for word in ("camera", "looks", "stops", "straight"):
        assert word not in setting
    assert "forest" in setting
