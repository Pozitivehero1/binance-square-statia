from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # Core
    mistral_api_key: str = os.getenv("MISTRAL_API", "").strip()
    mistral_model: str = os.getenv("MISTRAL_MODEL", "mistral-large-latest").strip()
    referral_url: str = os.getenv("REFERRAL_URL", "").strip()
    exchange_name: str = "Binance"
    language: str = os.getenv("LANGUAGE", "ru").strip().lower()

    # Content engine
    shorts_per_run: int = _int("SHORTS_PER_RUN", 1)
    generation_attempts: int = _int("GENERATION_ATTEMPTS", 3)
    script_candidates: int = _int("SCRIPT_CANDIDATES", 3)
    topic_mode: str = os.getenv("TOPIC_MODE", "mixed").strip().lower()
    video_min_seconds: int = _int("VIDEO_MIN_SECONDS", 34)
    video_max_seconds: int = _int("VIDEO_MAX_SECONDS", 52)
    recent_topic_limit: int = _int("RECENT_TOPIC_LIMIT", 60)
    recent_hook_limit: int = _int("RECENT_HOOK_LIMIT", 80)
    recent_media_limit: int = _int("RECENT_MEDIA_LIMIT", 140)

    # Stock media. Optional: generated motion backgrounds are a built-in fallback.
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "").strip()
    pexels_per_page: int = _int("PEXELS_PER_PAGE", 12)

    # Voice
    voice_provider: str = os.getenv("VOICE_PROVIDER", "edge").strip().lower()
    edge_voice: str = os.getenv("EDGE_VOICE", "auto").strip()
    edge_rate: str = os.getenv("EDGE_RATE", "+4%").strip()
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "").strip()
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    elevenlabs_model: str = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()
    tts_fallback: bool = _bool("TTS_FALLBACK", True)

    # Video
    width: int = _int("WIDTH", 1080)
    height: int = _int("HEIGHT", 1920)
    fps: int = _int("FPS", 30)
    music_volume: float = _float("MUSIC_VOLUME", 0.055)
    video_crf: int = _int("VIDEO_CRF", 18)
    video_preset: str = os.getenv("VIDEO_PRESET", "medium").strip()

    # YouTube
    auto_upload: bool = _bool("AUTO_UPLOAD", False)
    youtube_privacy: str = os.getenv("YOUTUBE_PRIVACY", "private").strip().lower()
    youtube_category_id: str = os.getenv("YOUTUBE_CATEGORY_ID", "27").strip()
    youtube_client_secrets: Path = BASE_DIR / os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secret.json")
    youtube_token_file: Path = BASE_DIR / os.getenv("YOUTUBE_TOKEN_FILE", "token.json")
    youtube_made_for_kids: bool = _bool("YOUTUBE_MADE_FOR_KIDS", False)
    youtube_paid_promotion: bool = _bool("YOUTUBE_PAID_PROMOTION", True)
    youtube_synthetic_media: bool = _bool("YOUTUBE_SYNTHETIC_MEDIA", False)

    @property
    def output_dir(self) -> Path:
        return BASE_DIR / "output"

    @property
    def work_dir(self) -> Path:
        return BASE_DIR / "work"

    @property
    def music_dir(self) -> Path:
        return BASE_DIR / "music"

    @property
    def state_file(self) -> Path:
        return BASE_DIR / "state.json"

    @property
    def target_seconds(self) -> float:
        return (self.video_min_seconds + self.video_max_seconds) / 2

    def validate(self) -> None:
        missing = []
        if not self.mistral_api_key:
            missing.append("MISTRAL_API")
        if not self.referral_url or "example.com" in self.referral_url:
            missing.append("REFERRAL_URL")
        if missing:
            raise RuntimeError("Fill required values in .env: " + ", ".join(missing))
        parsed = urlparse(self.referral_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("REFERRAL_URL must be a complete http(s) URL")
        if not self.mistral_model:
            raise RuntimeError("MISTRAL_MODEL cannot be empty")
        if self.language not in {"ru", "en"}:
            raise RuntimeError("LANGUAGE must be ru or en")
        if self.topic_mode not in {"mixed", "market", "evergreen"}:
            raise RuntimeError("TOPIC_MODE must be mixed, market or evergreen")
        if not (1 <= self.shorts_per_run <= 10):
            raise RuntimeError("SHORTS_PER_RUN must be between 1 and 10")
        if not (1 <= self.generation_attempts <= 6):
            raise RuntimeError("GENERATION_ATTEMPTS must be between 1 and 6")
        if not (1 <= self.script_candidates <= 4):
            raise RuntimeError("SCRIPT_CANDIDATES must be between 1 and 4")
        if not (1 <= self.pexels_per_page <= 80):
            raise RuntimeError("PEXELS_PER_PAGE must be between 1 and 80")
        if not (20 <= self.video_min_seconds < self.video_max_seconds <= 59):
            raise RuntimeError("Use 20 <= VIDEO_MIN_SECONDS < VIDEO_MAX_SECONDS <= 59")
        if self.voice_provider not in {"edge", "elevenlabs"}:
            raise RuntimeError("VOICE_PROVIDER must be edge or elevenlabs")
        if not self.edge_voice:
            raise RuntimeError("EDGE_VOICE cannot be empty; use auto for language-aware default")
        if self.voice_provider == "elevenlabs" and (not self.elevenlabs_api_key or not self.elevenlabs_voice_id):
            raise RuntimeError("ElevenLabs requires ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID")
        if self.youtube_privacy not in {"private", "unlisted", "public"}:
            raise RuntimeError("YOUTUBE_PRIVACY must be private, unlisted or public")
        if self.width < 360 or self.height < 640 or self.height <= self.width:
            raise RuntimeError("Video must be vertical and at least 360x640")
        if not (15 <= self.fps <= 60):
            raise RuntimeError("FPS must be between 15 and 60")
        if not (0.0 <= self.music_volume <= 0.25):
            raise RuntimeError("MUSIC_VOLUME must be between 0 and 0.25")
        if not (15 <= self.video_crf <= 30):
            raise RuntimeError("VIDEO_CRF must be between 15 and 30")


settings = Settings()
