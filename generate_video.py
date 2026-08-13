from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import edge_tts
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
TOPICS = json.loads((ROOT / "topics.json").read_text(encoding="utf-8"))
OUT = ROOT / CFG.get("output_dir", "output")
WORK = ROOT / ".work"
OUT.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)

UA = {"User-Agent": "TikTokAutoVideo/2.0 educational-video-generator"}
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: List[str]) -> None:
    log("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def ffprobe_duration(path: Path) -> float:
    p = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path)
    ], capture_output=True, text=True, check=True)
    return float(p.stdout.strip())


def words(s: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", s))


def clean_text(s: str) -> str:
    s = re.sub(r"\[[^\]]+\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_name(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-").lower()
    return s[:80] or "video"


def require_binary(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"Required binary not found: {name}")


def wikipedia_search(query: str) -> Tuple[str, str, str]:
    """Return title, extract, canonical URL."""
    s = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 1},
        headers=UA, timeout=30,
    )
    s.raise_for_status()
    hits = s.json().get("query", {}).get("search", [])
    title = hits[0]["title"] if hits else query
    r = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "format": "json", "prop": "extracts|info", "explaintext": 1,
                "redirects": 1, "inprop": "url", "titles": title},
        headers=UA, timeout=30,
    )
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    return page.get("title", title), clean_text(page.get("extract", "")), page.get("fullurl", "")


def mistral_json(messages: List[Dict[str, str]], max_tokens: int = 1800, temperature: float = 0.55) -> Dict[str, Any]:
    key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MISTRAL_API_KEY is missing. Add it to GitHub Actions secrets.")
    model = os.getenv("MISTRAL_MODEL", CFG.get("mistral_model", "mistral-small-latest"))
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload, timeout=90,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in content)
    return json.loads(content)


def choose_topic() -> Dict[str, str]:
    forced = os.getenv("TOPIC", "").strip()
    niche = os.getenv("NICHE", CFG.get("niche", "technology, science and surprising true history")).strip()
    if forced:
        return {"title": forced, "search": forced, "hook": ""}

    history_path = ROOT / "history.json"
    used: List[str] = []
    if history_path.exists():
        try:
            used = json.loads(history_path.read_text(encoding="utf-8"))[-30:]
        except Exception:
            used = []

    # Ask Mistral for a fresh topic so the generator does not feel templated.
    try:
        data = mistral_json([
            {"role": "system", "content": "You select factual evergreen topics for short educational videos. Return JSON only."},
            {"role": "user", "content": (
                f"Choose ONE high-curiosity evergreen topic for a 65-90 second English TikTok in this niche: {niche}. "
                "It must be easy to illustrate with generic stock footage, have a reliable Wikipedia page, avoid politics, tragedy exploitation, medical advice, and investment advice. "
                f"Do not repeat these recent topics: {used[-20:]}. "
                "Return JSON exactly like {\"title\":\"...\",\"search\":\"Wikipedia search phrase\",\"hook\":\"one punchy factual hook\"}."
            )}
        ], max_tokens=350, temperature=0.75)
        topic = {k: clean_text(str(data.get(k, ""))) for k in ("title", "search", "hook")}
        if topic["title"] and topic["search"]:
            used.append(topic["title"])
            history_path.write_text(json.dumps(used[-50:], ensure_ascii=False, indent=2), encoding="utf-8")
            return topic
    except Exception as e:
        log(f"Topic generation failed, using curated topic list: {e}")

    choices = [t for t in TOPICS if t.get("title") not in used] or TOPICS
    topic = random.choice(choices)
    used.append(topic["title"])
    history_path.write_text(json.dumps(used[-50:], ensure_ascii=False, indent=2), encoding="utf-8")
    return topic


def make_plan(topic: Dict[str, str], source_title: str, extract: str) -> Dict[str, Any]:
    if len(extract) < 400:
        raise RuntimeError(f"Wikipedia source is too short for: {source_title}")
    prompt = f"""
Create an original factual English short-video plan about: {source_title}.
The video is for a faceless educational TikTok aimed at US/UK viewers.
Target narration: 190-220 words, approximately 65-90 seconds.
Use ONLY facts supported by the SOURCE below. Never invent names, dates, numbers, quotes, motives, or causal claims.
Opening must create curiosity in the first sentence without clickbait that the source cannot support.
Use conversational American English, short sentences, no headings in narration, no 'follow for more', no hashtags in narration.

Return JSON with this exact shape:
{{
  "title": "short video title",
  "hook": "first narration sentence",
  "caption": "TikTok caption, max 170 chars, no more than 4 hashtags",
  "scenes": [
    {{"narration": "1-3 sentences", "pexels_queries": ["specific visual search", "fallback visual search"]}}
  ]
}}

Requirements:
- 12 to 16 scenes.
- Every scene must have narration.
- Each pexels query must describe VISUALS, not abstract concepts, and must be 2-6 English words.
- Prefer objects, places, machines, laboratories, hands, architecture, maps, nature, archival-like generic visuals.
- Avoid exact brands/logos, copyrighted film footage, celebrities, and graphic content.
- Total narration must be 190-220 words.
- Return valid JSON only.

SOURCE ({source_title}):
{extract[:12000]}
"""
    data = mistral_json([
        {"role": "system", "content": "You are a meticulous short-form documentary script editor. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ], max_tokens=2500, temperature=0.55)

    scenes = data.get("scenes") or []
    if not 10 <= len(scenes) <= 18:
        raise RuntimeError(f"Mistral returned invalid scene count: {len(scenes)}")
    narrations = [clean_text(str(s.get("narration", ""))) for s in scenes]
    total = words(" ".join(narrations))
    if not 165 <= total <= 245:
        raise RuntimeError(f"Mistral narration word count out of bounds: {total}")
    for s in scenes:
        qs = s.get("pexels_queries") or []
        if not qs:
            s["pexels_queries"] = [source_title]
        s["narration"] = clean_text(str(s["narration"]))
    data["title"] = clean_text(str(data.get("title", source_title)))
    data["hook"] = clean_text(str(data.get("hook", narrations[0])))
    data["caption"] = clean_text(str(data.get("caption", data["title"])))[:220]
    return data


async def make_tts(script: str, mp3: Path, srt: Path) -> None:
    communicate = edge_tts.Communicate(
        script,
        CFG.get("voice", "en-US-AvaMultilingualNeural"),
        rate=CFG.get("voice_rate", "-3%"),
        pitch=CFG.get("voice_pitch", "+0Hz"),
    )
    submaker = edge_tts.SubMaker()
    with open(mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    srt.write_text(submaker.get_srt(), encoding="utf-8")


def parse_srt_time(s: str) -> float:
    hh, mm, rest = s.split(":")
    ss, ms = rest.split(",")
    return int(hh)*3600 + int(mm)*60 + int(ss) + int(ms)/1000


def fmt_srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round((sec - int(sec))*1000))
    base = int(sec)
    hh, rem = divmod(base, 3600)
    mm, ss = divmod(rem, 60)
    if ms >= 1000:
        ss += 1; ms -= 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def retime_srt(path: Path, factor: float) -> None:
    txt = path.read_text(encoding="utf-8")
    pat = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})")
    txt = pat.sub(lambda m: f"{fmt_srt_time(parse_srt_time(m.group(1))*factor)} --> {fmt_srt_time(parse_srt_time(m.group(2))*factor)}", txt)
    path.write_text(txt, encoding="utf-8")


def ensure_min_duration(mp3: Path, srt: Path, minimum: float = 61.5) -> float:
    d = ffprobe_duration(mp3)
    if d >= minimum:
        return d
    # Slightly slow the narration if Mistral produced unusually brisk text.
    factor = min(1.18, minimum / max(d, 1.0))
    tempo = 1.0 / factor
    stretched = mp3.with_name("voice_stretched.mp3")
    run(["ffmpeg", "-y", "-i", str(mp3), "-filter:a", f"atempo={tempo:.5f}", "-b:a", "192k", str(stretched)])
    stretched.replace(mp3)
    retime_srt(srt, factor)
    return ffprobe_duration(mp3)


def pexels_search(query: str, per_page: int = 12) -> List[Dict[str, Any]]:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("PEXELS_API_KEY is missing. Add it to GitHub Actions secrets.")
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": key},
        params={"query": query, "per_page": min(40, per_page), "orientation": "portrait", "size": "medium"},
        timeout=40,
    )
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
        if not files:
            continue
        # Prefer vertical files close to 1080x1920 but tolerate any crop-able source.
        files.sort(key=lambda x: abs((x.get("width") or 720) - 1080) + abs((x.get("height") or 1280) - 1920))
        out.append({
            "id": v.get("id"),
            "url": v.get("url"),
            "duration": v.get("duration"),
            "creator": (v.get("user") or {}).get("name"),
            "download": files[0]["link"],
            "width": files[0].get("width"),
            "height": files[0].get("height"),
        })
    return out


def select_pexels_for_scene(scene: Dict[str, Any], used_ids: set) -> Dict[str, Any] | None:
    queries = [clean_text(str(q)) for q in (scene.get("pexels_queries") or []) if clean_text(str(q))]
    for query in queries[:3]:
        try:
            candidates = pexels_search(query, per_page=15)
            fresh = [c for c in candidates if c.get("id") not in used_ids]
            if fresh:
                choice = random.choice(fresh[:6])
                choice["query"] = query
                used_ids.add(choice.get("id"))
                return choice
        except Exception as e:
            log(f"Pexels search failed for '{query}': {e}")
    return None


def download(url: str, path: Path) -> None:
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for ch in r.iter_content(1024 * 1024):
                if ch:
                    f.write(ch)


def normalize_clip(src: Path, dst: Path, seconds: float, idx: int) -> None:
    w, h, fps = CFG["width"], CFG["height"], CFG["fps"]
    # Alternate subtle motion/crop so stock footage feels less repetitive.
    if idx % 2 == 0:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},eq=contrast=1.05:saturation=0.94:brightness=-0.01"
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},hflip,fps={fps},eq=contrast=1.04:saturation=0.96:brightness=-0.015"
    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-t", f"{seconds:.3f}", "-an", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CFG.get("crf", 21)), "-pix_fmt", "yuv420p", str(dst)
    ])


def font_path() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return "DejaVuSans-Bold.ttf"


def make_fallback_card(text: str, idx: int) -> Path:
    w, h = CFG["width"], CFG["height"]
    img = Image.new("RGB", (w, h), (17, 21, 28))
    d = ImageDraw.Draw(img)
    rng = random.Random(1009 + idx * 991)
    for _ in range(26):
        x, y = rng.randint(-220, 980), rng.randint(-220, 1850)
        r = rng.randint(90, 360)
        shade = rng.randint(25, 68)
        d.ellipse((x, y, x+r, y+r), fill=(shade, min(90, shade+8), min(100, shade+18)))
    fnt = ImageFont.truetype(font_path(), 58)
    small = ImageFont.truetype(font_path(), 30)
    wrapped = "\n".join(textwrap.wrap(text, width=30)[:5])
    d.rounded_rectangle((76, 500, 1004, 1420), radius=46, fill=(0, 0, 0))
    d.multiline_text((128, 620), wrapped, font=fnt, fill="white", spacing=18)
    d.text((128, 1328), "ORIGINAL EXPLAINER", font=small, fill=(218, 218, 218))
    p = WORK / f"fallback_{idx:02d}.png"
    img.save(p)
    return p


def normalize_card(img: Path, dst: Path, seconds: float) -> None:
    frames = max(1, int(seconds * CFG["fps"]))
    vf = f"zoompan=z='min(zoom+0.0007,1.10)':d={frames}:s={CFG['width']}x{CFG['height']}:fps={CFG['fps']}"
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-t", f"{seconds:.3f}", "-vf", vf,
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(dst)])


def concat_video(clips: List[Path], out: Path) -> None:
    manifest = WORK / "concat.txt"
    manifest.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(out)])


def ass_style_path(srt: Path) -> str:
    # ffmpeg subtitles filter supports force_style; escape path for filter syntax.
    return str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_final(base_video: Path, voice: Path, srt: Path, out: Path) -> None:
    sub = ass_style_path(srt)
    # Large centered captions with outline, placed above TikTok's lower UI.
    vf = (
        f"subtitles='{sub}':force_style='FontName=DejaVu Sans,FontSize=18,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=1,"
        "Alignment=2,MarginV=245'"
    )
    run([
        "ffmpeg", "-y", "-i", str(base_video), "-i", str(voice),
        "-map", "0:v:0", "-map", "1:a:0", "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(CFG.get("final_crf", 19)),
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)
    ])


def build_scene_durations(scenes: List[Dict[str, Any]], total: float) -> List[float]:
    counts = [max(1, words(s["narration"])) for s in scenes]
    denom = sum(counts)
    durations = [total * c / denom for c in counts]
    # Ensure a minimum visual beat length and then re-normalize.
    durations = [max(2.6, d) for d in durations]
    scale = total / sum(durations)
    return [d * scale for d in durations]


def validate_output(video: Path, voice: Path) -> None:
    vd = ffprobe_duration(video)
    ad = ffprobe_duration(voice)
    if vd < 60.5:
        raise RuntimeError(f"Final video is too short: {vd:.2f}s")
    if abs(vd - ad) > 2.0:
        log(f"Warning: video/audio duration mismatch: video={vd:.2f}, audio={ad:.2f}")
    if video.stat().st_size < 1_000_000:
        raise RuntimeError("Final video file is suspiciously small")


def main() -> None:
    require_binary("ffmpeg")
    require_binary("ffprobe")
    strict = os.getenv("STRICT_APIS", "1") != "0"
    if strict:
        if not os.getenv("MISTRAL_API_KEY", "").strip():
            raise RuntimeError("MISTRAL_API_KEY is required")
        if not os.getenv("PEXELS_API_KEY", "").strip():
            raise RuntimeError("PEXELS_API_KEY is required")

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    topic = choose_topic()
    log(f"Selected topic: {topic['title']}")
    source_title, extract, source_url = wikipedia_search(topic.get("search") or topic["title"])
    log(f"Wikipedia source: {source_title}")
    plan = make_plan(topic, source_title, extract)
    script = clean_text(" ".join(s["narration"] for s in plan["scenes"]))
    log(f"Narration word count: {words(script)}")

    voice = WORK / "voice.mp3"
    srt = WORK / "captions.srt"
    asyncio.run(make_tts(script, voice, srt))
    total = ensure_min_duration(voice, srt, minimum=float(CFG.get("minimum_seconds", 61.5)))
    log(f"Narration duration: {total:.2f}s")

    durations = build_scene_durations(plan["scenes"], total + 0.25)
    used_ids: set = set()
    clips: List[Path] = []
    pexels_sources: List[Dict[str, Any]] = []

    for idx, (scene, dur) in enumerate(zip(plan["scenes"], durations), 1):
        chosen = select_pexels_for_scene(scene, used_ids)
        dst = WORK / f"scene_{idx:02d}.mp4"
        if chosen:
            raw = WORK / f"raw_{idx:02d}.mp4"
            try:
                download(chosen["download"], raw)
                normalize_clip(raw, dst, dur, idx)
                pexels_sources.append({
                    "scene": idx,
                    "query": chosen.get("query"),
                    "pexels_id": chosen.get("id"),
                    "page_url": chosen.get("url"),
                    "creator": chosen.get("creator"),
                })
            except Exception as e:
                log(f"Download/render failed for scene {idx}; using generated fallback card: {e}")
                card = make_fallback_card(scene["narration"], idx)
                normalize_card(card, dst, dur)
        else:
            if strict:
                log(f"No Pexels result for scene {idx}; using generated fallback card.")
            card = make_fallback_card(scene["narration"], idx)
            normalize_card(card, dst, dur)
        clips.append(dst)

    base_video = WORK / "visuals.mp4"
    concat_video(clips, base_video)

    slug = safe_name(plan["title"])
    final = OUT / "video.mp4"
    render_final(base_video, voice, srt, final)
    validate_output(final, voice)

    (OUT / "script.txt").write_text(script + "\n", encoding="utf-8")
    (OUT / "caption.txt").write_text(plan["caption"] + "\n", encoding="utf-8")
    (OUT / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "metadata.json").write_text(json.dumps({
        "topic": topic,
        "title": plan["title"],
        "slug": slug,
        "source_title": source_title,
        "source_url": source_url,
        "narration_words": words(script),
        "duration_seconds": round(ffprobe_duration(final), 3),
        "voice": CFG.get("voice"),
        "mistral_model": os.getenv("MISTRAL_MODEL", CFG.get("mistral_model")),
        "pexels_sources": pexels_sources,
        "generated_unix": int(time.time()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(srt, OUT / "captions.srt")

    log(f"DONE: {final}")
    log(f"Duration: {ffprobe_duration(final):.2f}s | size: {final.stat().st_size/1024/1024:.1f} MB")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR: {e}")
        raise
