from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

import requests

LOG = logging.getLogger("shortsbot")


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required binary '{name}' was not found. Install FFmpeg and ensure it is in PATH.")
    return path


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = 300) -> subprocess.CompletedProcess[str]:
    LOG.debug("RUN: %s", " ".join(map(str, cmd)))
    try:
        return subprocess.run(
            [str(x) for x in cmd],
            cwd=str(cwd) if cwd else None,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-4000:]
        raise RuntimeError(f"Command failed ({exc.returncode}): {' '.join(map(str, cmd[:8]))}\n{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(map(str, cmd[:8]))}") from exc


def ffprobe_json(path: Path) -> dict[str, Any]:
    cp = run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    return json.loads(cp.stdout)


def ffprobe_duration(path: Path) -> float:
    data = ffprobe_json(path)
    return float((data.get("format") or {}).get("duration") or 0.0)


def safe_slug(text: str, limit: int = 64) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9а-яё]+", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"-+", "-", text).strip("-")
    return (text[:limit].rstrip("-") or "short")


def stable_id(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def read_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def request_with_retry(session, method: str, url: str, *, retries: int = 4, timeout: int = 45, **kwargs):
    """HTTP request with bounded retries for transient failures only.

    Non-retryable 4xx responses fail immediately; rate limits, transient 5xx
    responses and network errors use exponential backoff with jitter.
    """
    last_exc: Exception | None = None
    retryable = {408, 425, 429, 500, 502, 503, 504}
    for attempt in range(1, retries + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in retryable:
                if attempt >= retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "").strip()
                try:
                    delay = min(20.0, max(0.5, float(retry_after))) if retry_after else 0.0
                except ValueError:
                    delay = 0.0
                if delay <= 0:
                    delay = min(12.0, 0.9 * (2 ** (attempt - 1)) + random.uniform(0.1, 0.8))
                LOG.warning("HTTP %s from %s; retrying in %.1fs", resp.status_code, url, delay)
                time.sleep(delay)
                continue
            # Important: schema/auth/validation errors such as 400/401/403 must
            # not be retried four times before provider-specific fallback runs.
            resp.raise_for_status()
            return resp
        except requests.HTTPError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= retries:
                break
            delay = min(12.0, 0.9 * (2 ** (attempt - 1)) + random.uniform(0.1, 0.8))
            LOG.warning("Network request failed (%s), retry %s/%s in %.1fs", exc, attempt, retries, delay)
            time.sleep(delay)
    if last_exc is None:
        raise RuntimeError(f"Request failed without a response: {method} {url}")
    raise last_exc


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", text.lower(), flags=re.IGNORECASE)


def ngrams(text: str, n: int = 2) -> set[tuple[str, ...]]:
    t = tokens(text)
    if len(t) < n:
        return {tuple(t)} if t else set()
    return {tuple(t[i:i+n]) for i in range(len(t) - n + 1)}


def similarity(a: str, b: str) -> float:
    aa, bb = ngrams(a, 2), ngrams(b, 2)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def max_similarity(text: str, others: Iterable[str]) -> float:
    return max((similarity(text, x) for x in others if x), default=0.0)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def format_money(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    av = abs(v)
    if av >= 1_000_000_000:
        return f"${v/1_000_000_000:.2f}B"
    if av >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if av >= 1_000:
        return f"${v:,.0f}"
    if av >= 1:
        return f"${v:,.2f}"
    if av >= 0.01:
        return f"${v:.4f}"
    return f"${v:.8f}".rstrip("0")


def validate_render(path: Path, expected_width: int, expected_height: int, min_seconds: float, max_seconds: float, *, expected_duration: float | None = None, min_audio_seconds: float | None = None) -> dict[str, Any]:
    # Scale the sanity threshold with resolution and requested duration so tiny CI
    # smoke renders are not flaky while production renders still get a corruption check.
    min_bytes = max(50_000, int(expected_width * expected_height * max(min_seconds, 1.0) * 0.004))
    if not path.exists() or path.stat().st_size < min_bytes:
        raise RuntimeError(f"Rendered video is missing or unexpectedly small (<{min_bytes} bytes)")
    data = ffprobe_json(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise RuntimeError("Rendered file must contain both video and audio streams")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if (width, height) != (expected_width, expected_height):
        raise RuntimeError(f"Wrong render size: {width}x{height}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if not (min_seconds - 2.5 <= duration <= max_seconds + 2.5):
        raise RuntimeError(f"Unexpected render duration: {duration:.2f}s")

    if expected_duration is not None:
        # More than a few frames of drift indicates that the video/audio track ended early.
        tolerance = max(0.14, expected_duration * 0.004)
        if abs(duration - expected_duration) > tolerance:
            raise RuntimeError(
                f"Render duration drift: expected {expected_duration:.3f}s, got {duration:.3f}s"
            )

    def stream_duration(stream: dict[str, Any]) -> float:
        try:
            value = float(stream.get("duration") or 0.0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
        return duration

    audio_duration = stream_duration(audio)
    video_duration = stream_duration(video)
    if min_audio_seconds is not None and audio_duration + 0.10 < min_audio_seconds:
        raise RuntimeError(
            f"Audio track was truncated: speech {min_audio_seconds:.3f}s, rendered audio {audio_duration:.3f}s"
        )
    if expected_duration is not None and video_duration + 0.12 < expected_duration:
        raise RuntimeError(
            f"Video track ended early: expected {expected_duration:.3f}s, rendered video {video_duration:.3f}s"
        )
    return {
        "duration": round(duration, 3),
        "audio_duration": round(audio_duration, 3),
        "video_duration": round(video_duration, 3),
        "expected_duration": round(expected_duration, 3) if expected_duration is not None else None,
        "width": width,
        "height": height,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "size_bytes": path.stat().st_size,
    }
