from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path

import requests

from config import Settings
from models import SpeechResult, WordTiming
from utils import LOG, ffprobe_duration, request_with_retry

TICKS_PER_SECOND = 10_000_000.0


class TTS:
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        providers = [self.cfg.voice_provider]
        if self.cfg.tts_fallback:
            alternate = "edge" if self.cfg.voice_provider == "elevenlabs" else "elevenlabs"
            if alternate == "edge" or (self.cfg.elevenlabs_api_key and self.cfg.elevenlabs_voice_id):
                providers.append(alternate)

        errors: list[str] = []
        for provider in providers:
            try:
                if out_path.exists():
                    out_path.unlink()
                words = self._elevenlabs(text, out_path) if provider == "elevenlabs" else self._edge(text, out_path)
                if not out_path.exists() or out_path.stat().st_size < 1024:
                    raise RuntimeError("TTS did not produce valid audio")
                duration = ffprobe_duration(out_path)
                if duration <= 0:
                    raise RuntimeError("Could not determine TTS duration")
                words = [w for w in words if 0 <= w.start < w.end <= duration + 0.75]
                LOG.info("Voice: %s, %.1fs, %d timed words", provider, duration, len(words))
                return SpeechResult(path=out_path, provider=provider, duration=duration, words=words)
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                LOG.warning("TTS provider %s failed: %s", provider, exc)
        raise RuntimeError("All configured TTS providers failed: " + " | ".join(errors))

    def _edge(self, text: str, out_path: Path) -> list[WordTiming]:
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("edge-tts is not installed. Run pip install -r requirements.txt") from exc

        async def _run() -> list[WordTiming]:
            result: list[WordTiming] = []
            voice = self.cfg.edge_voice
            if voice.lower() == "auto":
                voice = "ru-RU-DmitryNeural" if self.cfg.language == "ru" else "en-US-GuyNeural"
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=self.cfg.edge_rate,
                pitch="+0Hz",
                volume="+0%",
                boundary="WordBoundary",
            )
            with out_path.open("wb") as audio:
                async for chunk in communicate.stream():
                    ctype = chunk.get("type")
                    if ctype == "audio":
                        audio.write(chunk["data"])
                    elif ctype == "WordBoundary":
                        start = float(chunk.get("offset") or 0) / TICKS_PER_SECOND
                        dur = float(chunk.get("duration") or 0) / TICKS_PER_SECOND
                        token = str(chunk.get("text") or "").strip()
                        if token and dur > 0:
                            result.append(WordTiming(token, start, start + dur))
            return result

        return asyncio.run(_run())

    def _elevenlabs(self, text: str, out_path: Path) -> list[WordTiming]:
        if not self.cfg.elevenlabs_api_key or not self.cfg.elevenlabs_voice_id:
            raise RuntimeError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are required")
        session = requests.Session()
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.cfg.elevenlabs_voice_id}/with-timestamps"
        resp = request_with_retry(
            session,
            "POST",
            url,
            headers={"xi-api-key": self.cfg.elevenlabs_api_key, "Content-Type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": self.cfg.elevenlabs_model,
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.78, "style": 0.22, "use_speaker_boost": True},
            },
        )
        data = resp.json()
        out_path.write_bytes(base64.b64decode(data["audio_base64"]))
        alignment = data.get("normalized_alignment") or data.get("alignment") or {}
        return alignment_to_words(alignment)


def alignment_to_words(alignment: dict) -> list[WordTiming]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    n = min(len(chars), len(starts), len(ends))
    if n == 0:
        return []
    words: list[WordTiming] = []
    buf: list[str] = []
    start: float | None = None
    end = 0.0
    for i in range(n):
        ch = str(chars[i])
        if ch.isspace():
            if buf and start is not None:
                token = "".join(buf).strip()
                if token:
                    words.append(WordTiming(token, start, end))
            buf, start = [], None
            continue
        if start is None:
            start = float(starts[i])
        buf.append(ch)
        end = float(ends[i])
    if buf and start is not None:
        token = "".join(buf).strip()
        if token:
            words.append(WordTiming(token, start, end))
    return words
