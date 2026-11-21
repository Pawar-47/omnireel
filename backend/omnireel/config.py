"""Environment-driven configuration.

Only the standard library is used here so the backend keeps a small dependency
surface, which matters on the low-end Linux machines this project targets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = BACKEND_ROOT.parent

DEFAULT_TARGET_SECONDS = 120
MIN_TARGET_SECONDS = 20
MAX_TARGET_SECONDS = 600


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from *path* without overriding the real environment."""
    if not path.is_file():
        return
    try:
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def collect_keys(prefix: str) -> list[str]:
    """Collect PREFIX, PREFIX_2, PREFIX_3 ... (contiguous, like Agnes' own tooling)."""
    keys: list[str] = []
    first = os.environ.get(prefix, "").strip()
    if first:
        keys.append(first)
    index = 2
    while True:
        value = os.environ.get(f"{prefix}_{index}", "").strip()
        if not value:
            break
        keys.append(value)
        index += 1
    return keys


#: Environment variable prefix per provider id.
KEY_ENV_PREFIX = {
    "agnes": "AGNES_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "huggingface": "HF_TOKEN",
    "runware": "RUNWARE_API_KEY",
    "yepapi": "YEPAPI_API_KEY",
}


@dataclass
class Settings:
    """All mutable knobs for one running instance."""

    host: str = "127.0.0.1"
    port: int = 8000

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    # Scheduling
    worker_interval_seconds: float = 3.0
    poll_interval_seconds: float = 6.0
    max_concurrent_generations: int = 2
    http_timeout_seconds: float = 180.0
    download_timeout_seconds: float = 600.0
    ffmpeg_timeout_seconds: float = 900.0

    # Creative defaults
    default_target_seconds: int = DEFAULT_TARGET_SECONDS
    default_mode: str = "free_max"
    default_aspect_ratio: str = "16:9"
    default_resolution: str = "720p"

    # Safety: paid generation never happens unless this is explicitly enabled.
    allow_paid: bool = False
    free_only: bool = True

    # Providers
    keys: dict[str, list[str]] = field(default_factory=dict)
    extra_config_path: Path | None = None

    #: Where the built frontend lives, when it has been built.
    frontend_dist: Path = field(default_factory=lambda: REPO_ROOT / "frontend" / "dist")

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        load_env_file(env_file or REPO_ROOT / ".env")
        load_env_file(Path.cwd() / ".env")

        data_dir = Path(env_str("OMNIREEL_DATA_DIR", str(REPO_ROOT / "data"))).expanduser()
        extra_raw = os.environ.get("OMNIREEL_PROVIDERS_CONFIG", "").strip()
        settings = cls(
            host=env_str("OMNIREEL_HOST", "127.0.0.1"),
            port=env_int("OMNIREEL_PORT", 8000),
            data_dir=data_dir,
            ffmpeg=env_str("OMNIREEL_FFMPEG", "ffmpeg"),
            ffprobe=env_str("OMNIREEL_FFPROBE", "ffprobe"),
            worker_interval_seconds=env_float("OMNIREEL_WORKER_INTERVAL", 3.0),
            poll_interval_seconds=env_float("OMNIREEL_POLL_INTERVAL", 6.0),
            max_concurrent_generations=max(1, env_int("OMNIREEL_MAX_CONCURRENT", 2)),
            http_timeout_seconds=env_float("OMNIREEL_HTTP_TIMEOUT", 180.0),
            download_timeout_seconds=env_float("OMNIREEL_DOWNLOAD_TIMEOUT", 600.0),
            ffmpeg_timeout_seconds=env_float("OMNIREEL_FFMPEG_TIMEOUT", 900.0),
            default_target_seconds=env_int("OMNIREEL_TARGET_SECONDS", DEFAULT_TARGET_SECONDS),
            default_mode=env_str("OMNIREEL_MODE", "free_max"),
            default_aspect_ratio=env_str("OMNIREEL_ASPECT_RATIO", "16:9"),
            default_resolution=env_str("OMNIREEL_RESOLUTION", "720p"),
            allow_paid=env_bool("OMNIREEL_ALLOW_PAID", False),
            free_only=not env_bool("OMNIREEL_ALLOW_PAID", False),
            extra_config_path=Path(extra_raw).expanduser() if extra_raw else None,
            keys={
                provider: collect_keys(prefix)
                for provider, prefix in KEY_ENV_PREFIX.items()
            },
        )
        return settings

    # -- convenience -----------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "omnireel.sqlite"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "frames"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.clips_dir,
            self.output_dir,
            self.frames_dir,
            self.audio_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def keys_for(self, provider: str) -> list[str]:
        return list(self.keys.get(provider, []))

    def key_ids_for(self, provider: str) -> list[str]:
        return [f"{provider}:{index + 1}" for index in range(len(self.keys_for(provider)))]

    def project_dir(self, project_id: str) -> Path:
        return self.clips_dir / project_id
