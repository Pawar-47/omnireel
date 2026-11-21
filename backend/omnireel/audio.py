"""Optional narration synthesis using free, local-first tooling.

Paid TTS APIs are never required. The order is deliberate:

1. ``piper`` - offline neural voice, best quality, needs ``PIPER_VOICE`` set to a
   model file you already have;
2. ``espeak-ng`` - offline, tiny, always available on most Linux desktops;
3. ``espeak`` - the older binary.

If none is installed the caller gets a clear, actionable error instead of a
silently silent video.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .config import Settings


class NarrationUnavailable(RuntimeError):
    pass


def available_engines() -> list[str]:
    engines = []
    if shutil.which("piper"):
        engines.append("piper")
    if shutil.which("espeak-ng"):
        engines.append("espeak-ng")
    if shutil.which("espeak"):
        engines.append("espeak")
    return engines


async def _run(args: list[str], input_text: bytes | None = None, timeout: float = 300.0) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(input_text), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise NarrationUnavailable("speech synthesis timed out") from None
    if process.returncode not in (0, None):
        raise NarrationUnavailable(stderr.decode("utf-8", "replace")[-400:])


async def synthesize_narration(text: str, destination: Path, settings: Settings) -> Path:
    """Render *text* to a 48 kHz mono/stereo wav at *destination*."""
    text = (text or "").strip()
    if not text:
        raise NarrationUnavailable("narration script is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    engines = available_engines()
    if not engines:
        raise NarrationUnavailable(
            "no offline speech engine found - install espeak-ng (sudo apt install espeak-ng) "
            "or piper, or clear the narration script to render a silent project"
        )

    if engines[0] == "piper":
        voice = __import__("os").environ.get("PIPER_VOICE", "").strip()
        if voice:
            await _run(["piper", "--model", voice, "--output_file", str(destination)], text.encode())
            if destination.exists() and destination.stat().st_size > 0:
                return destination

    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if binary is None:
        raise NarrationUnavailable(
            "piper is installed but PIPER_VOICE is not set; set it or install espeak-ng"
        )
    await _run(
        [binary, "-w", str(destination), "-s", "150", "-v", "en-us", text],
    )
    if not destination.exists() or destination.stat().st_size == 0:
        raise NarrationUnavailable("speech engine produced no audio")
    return destination
