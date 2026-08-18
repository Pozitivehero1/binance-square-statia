from __future__ import annotations

import argparse
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from media import MediaProvider
from models import Script, Topic
from script_generator import ScriptGenerator
from topic_source import TopicSource
from tts import TTS
from utils import LOG, read_json, require_binary, safe_slug, setup_logging, stable_id, write_json
from video_builder import VideoBuilder, write_srt
from youtube_upload import upload_video


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate original crypto-literacy YouTube Shorts")
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--topic", type=str, default=None, help="Force a custom topic")
    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--keep-work", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--seed", type=int, default=None)
    return p.parse_args()


def description_for(script: Script, topic: Topic, clips) -> str:
    credits: list[str] = []
    seen = set()
    used_pexels = False
    for c in clips:
        if c.source == "pexels":
            used_pexels = True
        if c.source == "pexels" and c.creator_name and c.creator_name not in seen:
            seen.add(c.creator_name)
            ref = c.creator_url or c.pexels_url
            credits.append(f"Video by {c.creator_name} on Pexels" + (f": {ref}" if ref else ""))
    if settings.language == "ru":
        basis = (
            f"Источник рыночных данных: {script.source_note}."
            if topic.kind == "market"
            else "Образовательный разбор механики; конкретные правила, комиссии и параметры бирж могут отличаться."
        )
        parts = [
            script.description,
            "",
            "Кликабельная ссылка — первая ссылка в профиле канала.",
            f"{settings.exchange_name}: {settings.referral_url}",
            "Реферальная ссылка: автор может получить комиссию, если вы зарегистрируетесь или воспользуетесь сервисом по этой ссылке.",
            "Криптоактивы волатильны. Материал носит образовательный характер и не является персональной финансовой рекомендацией.",
            basis,
        ]
    else:
        basis = (
            f"Market data source: {script.source_note}."
            if topic.kind == "market"
            else "Educational mechanics overview; venue-specific rules, fees and parameters may differ."
        )
        parts = [
            script.description,
            "",
            "Clickable link: use the first link in the channel profile.",
            f"{settings.exchange_name}: {settings.referral_url}",
            "Referral disclosure: the creator may receive a commission if you sign up or use the service through this link.",
            "Crypto assets are volatile. This is educational content, not personalized financial advice.",
            basis,
        ]
    if used_pexels:
        parts.extend(["Videos provided by Pexels: https://www.pexels.com", *credits[:7]])
    tags = " ".join("#" + t.replace(" ", "") for t in script.tags[:6] if t)
    parts.extend(["", tags])
    return "\n".join(x for x in parts if x is not None).strip()


def custom_topic(text: str) -> Topic:
    text = text.strip()
    return Topic(
        title=text,
        context="User-supplied topic. Keep factual claims conservative; never invent current facts, figures, news, or price causes.",
        visual_hint="cryptocurrency technology trading vertical video",
        fingerprint=stable_id("custom:" + text),
        source="custom topic",
        format_hint="explainer",
        kind="custom",
    )


def select_topic(candidates: list[Topic], state: dict, forced: str | None, excluded: set[str]) -> Topic:
    if forced:
        return custom_topic(forced)
    recent = set(state.get("recent_topics", [])) | excluded
    fresh = [x for x in candidates if x.fingerprint not in recent]
    if fresh:
        return fresh[0]
    if candidates:
        return random.choice(candidates)
    raise RuntimeError("No topics available")


def record_state(state: dict, topic: Topic, script: Script, clips, output: Path, youtube_id: str | None) -> None:
    def push_unique(key: str, value, limit: int):
        arr = [x for x in state.get(key, []) if x != value]
        arr.append(value)
        state[key] = arr[-limit:]
    push_unique("recent_topics", topic.fingerprint, settings.recent_topic_limit)
    push_unique("recent_hooks", script.hook, settings.recent_hook_limit)
    for c in clips:
        if c.video_id:
            push_unique("recent_media_ids", int(c.video_id), settings.recent_media_limit)
    history = state.get("history", [])
    history.append({
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topic": topic.title, "hook": script.hook, "title": script.title,
        "script_quality": round(script.quality_score, 1), "file": output.name, "youtube_id": youtube_id,
    })
    state["history"] = history[-300:]
    write_json(settings.state_file, state)


def self_test() -> int:
    print("Python:", sys.version.split()[0])
    ffmpeg = require_binary("ffmpeg")
    ffprobe = require_binary("ffprobe")
    print("ffmpeg:", ffmpeg)
    print("ffprobe:", ffprobe)
    import subprocess
    cp = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not re_search_filter(cp.stdout, "ass"):
        raise RuntimeError("This FFmpeg build lacks the ass subtitle filter/libass")
    print("ASS subtitle filter: OK")
    print("Configuration module: OK")
    print("Self-test passed. No external APIs were called.")
    return 0


def re_search_filter(text: str, name: str) -> bool:
    return any(line.split()[1:2] == [name] for line in text.splitlines() if line.strip() and len(line.split()) >= 2)


def generate_one(index: int, args, state: dict, components: dict) -> bool:
    topic_source: TopicSource = components["topic_source"]
    script_gen: ScriptGenerator = components["script_gen"]
    tts: TTS = components["tts"]
    media: MediaProvider = components["media"]
    builder: VideoBuilder = components["builder"]

    excluded: set[str] = set()
    last_error: Exception | None = None
    for attempt in range(1, settings.generation_attempts + 1):
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{index+1:02d}_a{attempt}"
        work = settings.work_dir / run_id
        work.mkdir(parents=True, exist_ok=True)
        try:
            candidates = topic_source.get_candidates()
            topic = select_topic(candidates, state, args.topic, excluded)
            excluded.add(topic.fingerprint)
            LOG.info("Video %d attempt %d/%d | topic: %s", index + 1, attempt, settings.generation_attempts, topic.title)
            script = script_gen.create(topic, state.get("recent_hooks", []))

            speech = tts.synthesize(script.narration, work / "voice.mp3")
            # One editorial retry if the actual voice duration missed the requested range materially.
            if speech.duration < settings.video_min_seconds - 2 or speech.duration > settings.video_max_seconds + 2:
                direction = "longer" if speech.duration < settings.video_min_seconds else "shorter"
                feedback = (
                    f"LENGTH CORRECTION: the previous narration rendered at {speech.duration:.1f}s. "
                    f"Write a {direction} version that should land near {settings.target_seconds:.0f}s."
                )
                LOG.warning("Voice duration %.1fs outside target; regenerating script once", speech.duration)
                script = script_gen.create(topic, state.get("recent_hooks", []), length_feedback=feedback)
                speech = tts.synthesize(script.narration, work / "voice.mp3")
            if not (settings.video_min_seconds - 2 <= speech.duration <= settings.video_max_seconds + 2):
                raise RuntimeError(f"Narration still outside acceptable duration: {speech.duration:.1f}s")

            clips = media.collect(script.scenes, work, state.get("recent_media_ids", []))
            slug = safe_slug(script.title)
            output = settings.output_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slug}.mp4"
            duration, qa = builder.build(script, speech, clips, work, output)
            srt_out = output.with_suffix(".srt")
            write_srt(speech.words, script.narration, duration, srt_out)
            description = description_for(script, topic, clips)
            meta = {
                "topic": topic.title, "topic_kind": topic.kind, "topic_source": topic.source, "topic_data": topic.data,
                "title": script.title, "description": description, "tags": script.tags,
                "hook": script.hook, "scenes": [s.__dict__ for s in script.scenes], "narration": script.narration,
                "source_note": script.source_note, "script_quality_score": round(script.quality_score, 1),
                "voice_provider": speech.provider, "voice_duration_seconds": round(speech.duration, 3),
                "timed_words": len(speech.words), "duration_seconds": round(duration, 3),
                "media": [{"source": c.source, "pexels_id": c.video_id, "query": c.query, "creator": c.creator_name, "url": c.pexels_url} for c in clips],
                "qa": qa, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "referral_disclosure_included": True, "cta_destination": "channel_profile",
            }
            meta_path = output.with_suffix(".json")
            write_json(meta_path, meta)

            youtube_id = None
            if settings.auto_upload and not args.no_upload:
                try:
                    youtube_id = upload_video(settings, output, script.title, description, script.tags)
                    meta["youtube_id"] = youtube_id
                except Exception as exc:
                    # A network/OAuth upload problem must not destroy a successfully generated expensive render.
                    LOG.exception("YouTube upload failed; keeping generated files")
                    meta["youtube_upload_error"] = str(exc)
                write_json(meta_path, meta)

            record_state(state, topic, script, clips, output, youtube_id)
            print(f"READY: {output}")
            print(f"META : {meta_path}")
            print(f"SRT  : {srt_out}")
            if youtube_id:
                print(f"YOUTUBE ID: {youtube_id}")
            return True
        except Exception as exc:
            last_error = exc
            LOG.exception("Attempt %d failed", attempt)
        finally:
            if not args.keep_work and work.exists():
                shutil.rmtree(work, ignore_errors=True)
    LOG.error("Video %d failed after %d attempts: %s", index + 1, settings.generation_attempts, last_error)
    return False


def main() -> int:
    setup_logging()
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    if args.self_test:
        return self_test()
    require_binary("ffmpeg")
    require_binary("ffprobe")
    settings.validate()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    state = read_json(settings.state_file, {"recent_topics": [], "recent_hooks": [], "recent_media_ids": [], "history": []})
    components = {
        "topic_source": TopicSource(settings), "script_gen": ScriptGenerator(settings),
        "tts": TTS(settings), "media": MediaProvider(settings), "builder": VideoBuilder(settings),
    }
    count = max(1, min(args.count or settings.shorts_per_run, 10))
    successes = 0
    for i in range(count):
        successes += int(generate_one(i, args, state, components))
        if i + 1 < count:
            time.sleep(1)
    LOG.info("Run finished: %d/%d videos generated", successes, count)
    return 0 if successes > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
