"""FFmpeg assembly of the finished two-minute film.

Design rules:

* every clip is normalised first (same resolution, fps, pixel format, audio
  layout) so the join is deterministic no matter which provider produced it;
* hard cuts use the concat demuxer with stream copy - no needless re-encoding;
* crossfades use an ``xfade`` chain and are opt-in, because overlapping clips
  genuinely shorten the film;
* the finished file is trimmed to the requested duration and validated with
  ffprobe before the project is marked complete;
* the per-scene clips are never deleted - you can re-generate one scene and
  re-assemble without touching the others.

``build_*`` functions are pure so they can be unit tested without FFmpeg.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from .audio import NarrationUnavailable, synthesize_narration
from .config import Settings
from .continuity import MediaError, MediaTools

RESOLUTIONS = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
    "360p": (640, 360),
}
FPS_DEFAULT = 24


class AssemblyError(RuntimeError):
    pass


@dataclass
class AssemblyResult:
    output: Path
    duration: float | None
    target_seconds: float
    scenes: int
    crossfade: bool
    trimmed: bool
    audio: str
    commands: int

    def public_dict(self) -> dict:
        return {
            "output": str(self.output),
            "duration": self.duration,
            "target_seconds": self.target_seconds,
            "scenes": self.scenes,
            "crossfade": self.crossfade,
            "trimmed": self.trimmed,
            "audio": self.audio,
            "commands": self.commands,
        }


def dimensions_for(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    width, height = RESOLUTIONS.get((resolution or "720p").lower(), RESOLUTIONS["720p"])
    if aspect_ratio == "9:16":
        return height, width
    if aspect_ratio == "1:1":
        side = min(width, height)
        return side, side
    return width, height


# --------------------------------------------------------------------------
# Pure command builders
# --------------------------------------------------------------------------


def build_normalise_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration: float,
    has_audio: bool,
    crf: int = 20,
    preset: str = "veryfast",
) -> list[str]:
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if not has_audio:
        args += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={max(duration, 0.1):.3f}",
        ]
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    args += [
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-t",
        f"{max(duration, 0.1):.3f}",
        "-shortest",
        str(destination),
    ]
    return args


def build_concat_command(ffmpeg: str, list_file: Path, destination: Path, *, duration: float | None) -> list[str]:
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
    ]
    if duration and duration > 0:
        args += ["-t", f"{duration:.3f}"]
    args += ["-movflags", "+faststart", str(destination)]
    return args


def build_crossfade_command(
    ffmpeg: str,
    clips: list[Path],
    destination: Path,
    *,
    durations: list[float],
    fade: float,
    width: int,
    height: int,
    fps: int,
    crf: int = 20,
    preset: str = "veryfast",
) -> list[str]:
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for clip in clips:
        args += ["-i", str(clip)]

    chains: list[str] = []
    for index in range(len(clips)):
        chains.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{index}]"
        )

    current = "v0"
    offset = durations[0] - fade
    for index in range(1, len(clips)):
        label = f"x{index}"
        chains.append(
            f"[{current}][v{index}]xfade=transition=fade:duration={fade:.3f}:"
            f"offset={max(offset, 0.0):.3f}[{label}]"
        )
        current = label
        offset += durations[index] - fade

    audio_inputs = "".join(f"[{index}:a]" for index in range(len(clips)))
    chains.append(f"{audio_inputs}concat=n={len(clips)}:v=0:a=1[aout]")

    args += [
        "-filter_complex",
        ";".join(chains),
        "-map",
        f"[{current}]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    return args


def build_audio_mix_command(
    ffmpeg: str,
    video: Path,
    destination: Path,
    *,
    duration: float,
    music: Path | None = None,
    narration: Path | None = None,
    music_volume: float = 0.25,
    fade_seconds: float = 2.0,
    keep_scene_audio: bool = True,
) -> list[str]:
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    filters: list[str] = []
    labels: list[str] = []

    if keep_scene_audio:
        filters.append(f"[0:a]volume=1.0[scene]")
        labels.append("[scene]")

    next_index = 1
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music)]
        fade_out_start = max(0.0, duration - fade_seconds)
        filters.append(
            f"[{next_index}:a]volume={music_volume},"
            f"atrim=0:{duration:.3f},afade=t=in:st=0:d={fade_seconds:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_seconds:.3f}[music]"
        )
        labels.append("[music]")
        next_index += 1

    if narration is not None:
        args += ["-i", str(narration)]
        filters.append(f"[{next_index}:a]volume=1.0,apad[voice]")
        labels.append("[voice]")
        next_index += 1

    if not labels:
        raise AssemblyError("no audio sources to mix")

    if len(labels) == 1:
        filters.append(f"{labels[0]}anull[aout]")
    else:
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:"
            "dropout_transition=0:normalize=0[aout]"
        )

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-t",
        f"{duration:.3f}",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    return args


# --------------------------------------------------------------------------
# Assembler
# --------------------------------------------------------------------------


class Assembler:
    def __init__(self, db, settings: Settings, media: MediaTools) -> None:  # noqa: ANN001
        self.db = db
        self.settings = settings
        self.media = media

    def ffmpeg(self) -> str:
        binary = self.media.ffmpeg_path()
        if binary is None:
            raise AssemblyError(
                "FFmpeg is not installed - run `sudo apt install ffmpeg` and restart OmniReel"
            )
        return binary

    async def _run(self, args: list[str]) -> None:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.settings.ffmpeg_timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise AssemblyError("FFmpeg timed out") from None
        if process.returncode != 0:
            raise AssemblyError(stderr.decode("utf-8", "replace")[-800:] or "FFmpeg failed")

    async def assemble(self, project: dict, scenes: list[dict]) -> AssemblyResult:
        ffmpeg = self.ffmpeg()
        options = _load_settings(project)
        target = float(project.get("target_seconds") or 120.0)
        width, height = dimensions_for(
            project.get("resolution") or "720p", project.get("aspect_ratio") or "16:9"
        )
        fps = int(options.get("fps") or FPS_DEFAULT)
        crf = int(options.get("crf") or 20)
        preset = str(options.get("preset") or "veryfast")

        ready = [
            scene
            for scene in sorted(scenes, key=lambda row: int(row.get("index_no") or 0))
            if scene.get("status") == "ready" and scene.get("clip_path")
        ]
        if not ready:
            raise AssemblyError("no finished clips to assemble")
        if len(ready) != len(scenes):
            raise AssemblyError(
                f"{len(scenes) - len(ready)} scene(s) are not finished yet; refusing to assemble "
                "a partial film"
            )

        work = Path(self.settings.output_dir) / project["id"]
        normalised_dir = work / "scenes"
        normalised_dir.mkdir(parents=True, exist_ok=True)

        clips: list[Path] = []
        durations: list[float] = []
        commands = 0
        for scene in ready:
            source = Path(scene["clip_path"])
            if not source.exists():
                raise AssemblyError(f"missing clip for scene {scene['index_no'] + 1}")
            info = await self.media.probe(source)
            has_audio = bool(info.get("has_audio"))
            clip_duration = float(info.get("duration") or scene.get("actual_duration") or scene.get("duration_seconds") or 0)
            if clip_duration <= 0:
                raise AssemblyError(f"scene {scene['index_no'] + 1} has zero length")
            destination = normalised_dir / f"scene_{int(scene['index_no']):03d}.mp4"
            await self._run(
                build_normalise_command(
                    ffmpeg,
                    source,
                    destination,
                    width=width,
                    height=height,
                    fps=fps,
                    duration=clip_duration,
                    has_audio=has_audio,
                    crf=crf,
                    preset=preset,
                )
            )
            commands += 1
            clips.append(destination)
            durations.append(clip_duration)

        crossfade = bool(options.get("crossfade"))
        fade = float(options.get("crossfade_seconds") or 0.5)
        joined = work / "joined.mp4"
        trimmed = False

        if crossfade and len(clips) > 1 and fade > 0:
            await self._run(
                build_crossfade_command(
                    ffmpeg,
                    clips,
                    joined,
                    durations=durations,
                    fade=fade,
                    width=width,
                    height=height,
                    fps=fps,
                    crf=crf,
                    preset=preset,
                )
            )
            commands += 1
            expected = max(0.0, sum(durations) - fade * (len(clips) - 1))
        else:
            list_file = work / "concat.txt"
            list_file.write_text(
                "\n".join(f"file '{clip.as_posix()}'" for clip in clips) + "\n",
                encoding="utf-8",
            )
            expected = sum(durations)
            trimmed = expected > target + 0.25
            trim = target if trimmed else None
            await self._run(build_concat_command(ffmpeg, list_file, joined, duration=trim))
            commands += 1
            if trim is not None:
                expected = target

        keep_audio = bool(options.get("keep_scene_audio", True))
        music = _existing_path(options.get("music_path"))
        narration_path: Path | None = None
        narration_script = (options.get("narration_script") or "").strip()
        if narration_script:
            narration_target = Path(self.settings.audio_dir) / project["id"] / "narration.wav"
            try:
                narration_path = await synthesize_narration(
                    narration_script, narration_target, self.settings
                )
            except NarrationUnavailable as exc:
                raise AssemblyError(str(exc)) from exc

        final = work / "final.mp4"
        audio_mode = "scene audio"
        if music is not None or narration_path is not None:
            await self._run(
                build_audio_mix_command(
                    ffmpeg,
                    joined,
                    final,
                    duration=target,
                    music=music,
                    narration=narration_path,
                    music_volume=float(options.get("music_volume") or 0.25),
                    keep_scene_audio=keep_audio,
                )
            )
            commands += 1
            audio_mode = "mixed" + (" + music" if music else "") + (" + narration" if narration_path else "")
        else:
            shutil.copyfile(joined, final)

        info = await self.media.probe(final)
        duration = info.get("duration")
        if duration is None:
            raise AssemblyError("could not validate the assembled video with ffprobe")

        final_target = Path(self.settings.output_dir) / f"{project['id']}.mp4"
        shutil.copyfile(final, final_target)

        return AssemblyResult(
            output=final_target,
            duration=float(duration),
            target_seconds=target,
            scenes=len(clips),
            crossfade=bool(crossfade and len(clips) > 1),
            trimmed=trimmed,
            audio=audio_mode,
            commands=commands,
        )


def _load_settings(project: dict) -> dict:
    from .db import loads

    return loads(project.get("settings"), {})


def _existing_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    return path if path.exists() else None
