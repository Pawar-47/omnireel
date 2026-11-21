"""FFmpeg command construction (no FFmpeg required to run these)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from omnireel.assembly import (
    build_audio_mix_command,
    build_concat_command,
    build_crossfade_command,
    build_normalise_command,
    dimensions_for,
)


def test_dimensions_follow_aspect_ratio():
    assert dimensions_for("720p", "16:9") == (1280, 720)
    assert dimensions_for("720p", "9:16") == (720, 1280)
    assert dimensions_for("720p", "1:1") == (720, 720)
    assert dimensions_for("1080p", "16:9") == (1920, 1080)


def test_normalise_command_pads_and_converts():
    args = build_normalise_command(
        "ffmpeg",
        Path("/tmp/in.mp4"),
        Path("/tmp/out.mp4"),
        width=1280,
        height=720,
        fps=24,
        duration=6.5,
        has_audio=False,
    )
    joined = " ".join(args)
    assert "scale=1280:720" in joined
    assert "pad=1280:720" in joined
    assert "fps=24" in joined
    assert "anullsrc" in joined, "silent clips must get a silent audio track"
    assert args[-1] == "/tmp/out.mp4"


def test_normalise_keeps_existing_audio():
    args = build_normalise_command(
        "ffmpeg",
        Path("/tmp/in.mp4"),
        Path("/tmp/out.mp4"),
        width=854,
        height=480,
        fps=24,
        duration=5,
        has_audio=True,
    )
    assert "anullsrc" not in " ".join(args)


def test_concat_uses_stream_copy():
    args = build_concat_command("ffmpeg", Path("/tmp/list.txt"), Path("/tmp/out.mp4"), duration=120)
    joined = " ".join(args)
    assert "-c copy" in joined
    assert "-t 120.000" in joined
    assert "+faststart" in joined


def test_crossfade_offsets_overlap_correctly():
    args = build_crossfade_command(
        "ffmpeg",
        [Path("/tmp/a.mp4"), Path("/tmp/b.mp4")],
        Path("/tmp/out.mp4"),
        durations=[6.0, 6.0],
        fade=0.5,
        width=1280,
        height=720,
        fps=24,
    )
    joined = " ".join(args)
    assert "xfade=transition=fade:duration=0.500" in joined
    assert "offset=5.500" in joined
    assert "concat=n=2:v=0:a=1" in joined


def test_audio_mix_with_music_and_narration():
    args = build_audio_mix_command(
        "ffmpeg",
        Path("/tmp/video.mp4"),
        Path("/tmp/out.mp4"),
        duration=120,
        music=Path("/tmp/score.mp3"),
        narration=Path("/tmp/voice.wav"),
        music_volume=0.25,
        keep_scene_audio=True,
    )
    joined = " ".join(args)
    assert "amix=inputs=3" in joined
    assert "afade=t=out" in joined
    assert "volume=0.25" in joined
    assert "-c:v copy" in joined


def test_audio_mix_without_scene_audio():
    args = build_audio_mix_command(
        "ffmpeg",
        Path("/tmp/video.mp4"),
        Path("/tmp/out.mp4"),
        duration=60,
        music=Path("/tmp/score.mp3"),
        keep_scene_audio=False,
    )
    joined = " ".join(args)
    assert "[0:a]" not in joined, "scene audio must be dropped when the user asked for it"
    assert "[music]" in joined


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg is not installed",
)
def test_assembler_builds_a_real_two_minute_film(settings, db):
    """End to end with FFmpeg: 12 clips of 10s become one 120 second MP4."""
    import asyncio

    from omnireel.assembly import Assembler
    from omnireel.continuity import MediaTools
    from omnireel.db import Database  # noqa: F401  (documentation of the fixture shape)

    media = MediaTools(settings)
    assembler = Assembler(db, settings, media)

    project_id = "assemble-test"
    work = settings.project_dir(project_id)
    work.mkdir(parents=True, exist_ok=True)

    ffmpeg = media.ffmpeg_path()
    assert ffmpeg is not None

    scenes = []
    for index in range(12):
        clip = work / f"src_{index:03d}.mp4"
        code, _, _ = asyncio.run(
            media._run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x{index * 20:06x}:s=320x180:r=24:d=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000:d=10",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(clip),
                ]
            )
        )
        assert code == 0
        scenes.append(
            {
                "id": f"s{index}",
                "index_no": index,
                "status": "ready",
                "clip_path": str(clip),
                "duration_seconds": 10.0,
                "actual_duration": 10.0,
            }
        )

    project = {
        "id": project_id,
        "target_seconds": 120.0,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "settings": '{"fps":24,"keep_scene_audio":true,"continuity":true}',
    }

    result = asyncio.run(assembler.assemble(project, scenes))
    assert result.output.exists()
    assert result.scenes == 12
    assert result.duration is not None
    assert abs(result.duration - 120.0) < 1.0
