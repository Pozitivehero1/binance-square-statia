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

DEFAULT_CFG = {
    "language": "en",
    "niche": "technology, science and surprising true history",
    "mistral_model": "mistral-small-latest",
    "voice": "en-US-AvaMultilingualNeural",
    "voice_rate": "-3%",
    "voice_pitch": "+0Hz",
    "minimum_seconds": 61.5,
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "crf": 21,
    "final_crf": 19,
    "output_dir": "output",
    "caption_max_words": 6,
    "caption_max_chars": 34,
    "caption_font_size": 46,
    "caption_margin_v": 300,
    "caption_margin_lr": 105,
}

def load_json_or_default(path: Path, default):
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            print(f"WARNING: {path.name} is empty; using built-in defaults.", flush=True)
            return default.copy() if isinstance(default, dict) else list(default)
        parsed = json.loads(raw)
        if isinstance(default, dict):
            if not isinstance(parsed, dict):
                raise ValueError(f"{path.name} must contain a JSON object")
            merged = default.copy()
            merged.update(parsed)
            return merged
        if not isinstance(parsed, list):
            raise ValueError(f"{path.name} must contain a JSON array")
        return parsed
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(f"WARNING: cannot load {path.name}: {exc}; using built-in fallback.", flush=True)
        return default.copy() if isinstance(default, dict) else list(default)

CFG = load_json_or_default(ROOT / "config.json", DEFAULT_CFG)
TOPICS = load_json_or_default(ROOT / "topics.json", [])
OUT = ROOT / CFG.get("output_dir", "output")
WORK = ROOT / ".work"
OUT.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)

UA = {
    "User-Agent": os.getenv(
        "WIKIMEDIA_USER_AGENT",
        "TikTokAutoVideo/2.1 (educational-video-generator; contact: github-actions)"
    ),
    "Api-User-Agent": os.getenv(
        "WIKIMEDIA_USER_AGENT",
        "TikTokAutoVideo/2.1 (educational-video-generator; contact: github-actions)"
    ),
}
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


def _topic_terms(text: str) -> List[str]:
    stop = {"how","why","what","who","when","where","the","a","an","and","or","of","to","in","on","for","with","from","built","made","first"}
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in stop]


def wikipedia_get(params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    """Best-effort Wikimedia request. GitHub-hosted runners share IPs and can receive HTTP 429."""
    delays = [0, 2, 6]
    last_error: Exception | None = None
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params=params, headers=UA, timeout=timeout,
            )
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "")
                log(f"Wikipedia rate-limited (429), attempt {attempt}/{len(delays)}" + (f"; Retry-After={retry_after}" if retry_after else ""))
                last_error = requests.HTTPError(f"429 Too Many Requests from Wikipedia")
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < len(delays):
                log(f"Wikipedia request failed, retrying: {e}")
                continue
    raise RuntimeError(f"Wikipedia unavailable after retries: {last_error}")


def mistral_source_brief(topic: Dict[str, str]) -> Tuple[str, str, str]:
    """Fallback when Wikimedia is rate-limited. Produces a conservative fact brief for evergreen topics."""
    log("Wikipedia unavailable; switching to Mistral fact-brief fallback.")
    data = mistral_json([
        {"role": "system", "content": (
            "You are a conservative research editor for evergreen educational videos. "
            "Return JSON only. Do not guess. Omit any fact you are not highly confident about."
        )},
        {"role": "user", "content": (
            f"Build a compact fact brief for this exact topic: {topic.get('title','')}.\n"
            f"Search intent: {topic.get('search','')}.\n"
            "Use only widely established, non-controversial facts suitable for a short explainer. "
            "Avoid precise numbers, dates, named individuals, records, causal claims, or superlatives unless you are highly confident. "
            "Do not broaden into namesakes or adjacent topics. "
            "Return exactly {\"topic_match\":true/false,\"facts\":[\"atomic fact\",...],\"notes\":\"short caveat if needed\"}. "
            "Provide 10-18 atomic facts. If you cannot support the exact topic confidently, set topic_match=false."
        )}
    ], max_tokens=1200, temperature=0.0)
    facts = [clean_text(str(x)) for x in (data.get("facts") or []) if clean_text(str(x))]
    if not data.get("topic_match") or len(facts) < 8:
        raise RuntimeError("Mistral could not produce a sufficiently reliable fallback fact brief")
    brief = " ".join(f"FACT {i}: {fact}" for i, fact in enumerate(facts, 1))
    notes = clean_text(str(data.get("notes", "")))
    if notes:
        brief += " NOTES: " + notes
    return "Mistral conservative fact brief", brief, ""


def wikipedia_candidates(topic: Dict[str, str], limit: int = 8) -> List[Dict[str, str]]:
    """Find several plausible Wikipedia pages and aggressively reject disambiguation/list pages."""
    queries = []
    for q in (topic.get("search", ""), topic.get("title", "")):
        q = clean_text(q)
        if q and q not in queries:
            queries.append(q)
    hits: Dict[str, Dict[str, str]] = {}
    for query in queries:
        data = wikipedia_get({"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": limit})
        for h in data.get("query", {}).get("search", []):
            title = h.get("title", "")
            if title:
                hits[title] = {"title": title}

    terms = set(_topic_terms(" ".join([topic.get("title", ""), topic.get("search", "")])))
    out: List[Dict[str, str]] = []
    for title in list(hits)[: limit * 2]:
        low = title.lower()
        if "(disambiguation)" in low or low.startswith("list of ") or low.startswith("index of "):
            continue
        data = wikipedia_get({"action": "query", "format": "json", "prop": "extracts|info", "explaintext": 1,
                              "redirects": 1, "inprop": "url", "titles": title})
        page = next(iter(data["query"]["pages"].values()))
        extract = clean_text(page.get("extract", ""))
        final_title = page.get("title", title)
        first = extract[:500].lower()
        if len(extract) < 900:
            continue
        if "may refer to:" in first or "may refer to" in first or "can refer to:" in first:
            continue
        title_terms = set(_topic_terms(final_title))
        score = 4 * len(terms & title_terms) + len(terms & set(_topic_terms(extract[:1800])))
        out.append({"title": final_title, "extract": extract, "url": page.get("fullurl", ""), "score": str(score)})
    out.sort(key=lambda x: int(x["score"]), reverse=True)
    return out[:limit]


def validate_source(topic: Dict[str, str], candidate: Dict[str, str]) -> Tuple[bool, str]:
    data = mistral_json([
        {"role": "system", "content": "You are a strict source-selection editor. Return JSON only."},
        {"role": "user", "content": (
            "Decide whether this Wikipedia article is directly about the requested short-video topic, not merely a name match, disambiguation, adjacent subject, or broad list. "
            "A source should contain enough material to support a focused 65-90 second video. "
            f"TOPIC: {topic.get('title','')}\nSEARCH INTENT: {topic.get('search','')}\n"
            f"ARTICLE TITLE: {candidate['title']}\nARTICLE EXCERPT: {candidate['extract'][:5000]}\n"
            "Return exactly {\"pass\":true/false,\"relevance\":0-10,\"reason\":\"short reason\"}."
        )}
    ], max_tokens=220, temperature=0.0)
    ok = bool(data.get("pass")) and float(data.get("relevance", 0)) >= 8
    return ok, clean_text(str(data.get("reason", "")))


def wikipedia_search(topic: Dict[str, str]) -> Tuple[str, str, str]:
    try:
        candidates = wikipedia_candidates(topic)
    except Exception as e:
        log(f"Wikipedia lookup unavailable: {e}")
        return mistral_source_brief(topic)

    if not candidates:
        log(f"No suitable Wikipedia candidates for topic: {topic.get('title')}; using fallback fact brief.")
        return mistral_source_brief(topic)

    failures = []
    for cand in candidates[:5]:
        try:
            ok, reason = validate_source(topic, cand)
        except Exception as e:
            ok, reason = False, f"validation error: {e}"
        log(f"Source candidate: {cand['title']} | score={cand['score']} | accepted={ok} | {reason}")
        if ok:
            return cand["title"], cand["extract"], cand["url"]
        failures.append(f"{cand['title']}: {reason}")

    log("Wikipedia candidates did not pass topic validation; using conservative Mistral fact brief instead of failing the run.")
    return mistral_source_brief(topic)

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


def make_plan(topic: Dict[str, str], source_title: str, extract: str, feedback: str = "") -> Dict[str, Any]:
    if len(extract) < 700:
        raise RuntimeError(f"Wikipedia source is too short for: {source_title}")
    prompt = f"""
Create an original factual English short-video plan about THIS EXACT TOPIC: {topic.get('title','')}.
The factual source/brief is: {source_title}.
The video is for a faceless educational TikTok aimed at US/UK viewers.
Target narration: 185-215 words, approximately 65-85 seconds.
Use ONLY facts explicitly supported by the SOURCE/FACT BRIEF below. Never invent names, dates, numbers, quotes, motives, causal claims, or extra people merely because they share a name.
DO NOT broaden the topic into unrelated people, places, namesakes, disambiguation entries, or trivia that is not central to the requested topic.
Opening must create strong curiosity in the first 1-2 seconds while remaining factual.
Use conversational American English, short sentences, and a clear narrative arc: hook -> setup -> escalation/explanation -> payoff.
No headings in narration, no 'follow for more', no hashtags in narration.

Return JSON with this exact shape:
{{
  "title": "short video title directly about the requested topic",
  "hook": "first narration sentence",
  "caption": "TikTok caption, max 150 chars, no more than 4 hashtags",
  "scenes": [
    {{"narration": "1-2 short sentences", "pexels_queries": ["specific stock visual search", "specific fallback search", "generic but relevant fallback"]}}
  ]
}}

Requirements:
- 14 to 18 scenes so visuals change frequently.
- Every scene must advance the SAME topic.
- Each pexels query must describe concrete visible things, 2-6 English words.
- Prefer visually literal stock footage: objects, machines, workshops, landscapes, maps, hands, tools, laboratories, architecture.
- When an exact historic object is unlikely on Pexels, request a truthful generic visual (for example 'vintage bicycle workshop'), not a misleading modern substitute pretending to be the historic object.
- Do not request portraits of named historical people unless generic portraits are only atmospheric and narration does not imply identity.
- Avoid logos, movie footage, celebrities, graphic content, modern visuals that falsely represent a historical event, and text-heavy footage.
- Total narration must be 185-215 words.
- Never use the word 'disambiguation' in title, caption, or narration.
- Return valid JSON only.

Previous QA feedback, if any: {feedback or 'none'}

SOURCE/FACT BRIEF ({source_title}):
{extract[:14000]}
"""
    data = mistral_json([
        {"role": "system", "content": "You are a meticulous short-form documentary script editor. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ], max_tokens=2800, temperature=0.42)

    scenes = data.get("scenes") or []
    if not 12 <= len(scenes) <= 20:
        raise RuntimeError(f"Mistral returned invalid scene count: {len(scenes)}")
    narrations = [clean_text(str(s.get("narration", ""))) for s in scenes]
    total = words(" ".join(narrations))
    if not 170 <= total <= 230:
        raise RuntimeError(f"Mistral narration word count out of bounds: {total}")
    for s in scenes:
        qs = [clean_text(str(q)) for q in (s.get("pexels_queries") or []) if clean_text(str(q))]
        s["pexels_queries"] = qs[:3] or [topic.get("search") or source_title]
        s["narration"] = clean_text(str(s["narration"]))
    data["title"] = clean_text(str(data.get("title", topic.get("title") or source_title)))
    data["hook"] = clean_text(str(data.get("hook", narrations[0])))
    data["caption"] = clean_text(str(data.get("caption", data["title"])))[:180]
    if "disambiguation" in (data["title"] + " " + data["caption"]).lower():
        raise RuntimeError("Plan drifted into disambiguation content")
    return data


def qa_plan(topic: Dict[str, str], source_title: str, extract: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    script = clean_text(" ".join(s.get("narration", "") for s in plan.get("scenes", [])))
    data = mistral_json([
        {"role": "system", "content": "You are an adversarial fact-checking and retention editor. Return JSON only."},
        {"role": "user", "content": (
            "Audit this TikTok plan against the exact requested topic and supplied source/fact brief. Fail it for topic drift, unsupported facts, misleading visual framing, weak first sentence, unrelated namesakes, or repetitive filler. "
            f"REQUESTED TOPIC: {topic.get('title','')}\nSOURCE TITLE: {source_title}\nSOURCE: {extract[:9000]}\n"
            f"PLAN TITLE: {plan.get('title','')}\nHOOK: {plan.get('hook','')}\nSCRIPT: {script}\n"
            "Return JSON exactly like {\"pass\":true/false,\"topic_relevance\":0-10,\"factual_grounding\":0-10,\"hook_strength\":0-10,\"visual_coherence\":0-10,\"issues\":[\"...\"]}."
        )}
    ], max_tokens=500, temperature=0.0)
    scores = [float(data.get(k, 0)) for k in ("topic_relevance", "factual_grounding", "hook_strength", "visual_coherence")]
    data["pass"] = bool(data.get("pass")) and scores[0] >= 8 and scores[1] >= 8 and scores[2] >= 7 and scores[3] >= 7
    return data


def build_validated_plan(topic: Dict[str, str], source_title: str, extract: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    feedback = ""
    last_qa: Dict[str, Any] = {}
    for attempt in range(1, 4):
        plan = make_plan(topic, source_title, extract, feedback=feedback)
        last_qa = qa_plan(topic, source_title, extract, plan)
        log(f"Plan QA attempt {attempt}: {json.dumps(last_qa, ensure_ascii=False)}")
        if last_qa.get("pass"):
            return plan, last_qa
        feedback = "; ".join(str(x) for x in (last_qa.get("issues") or []))[:1200]
    raise RuntimeError(f"Plan failed QA after 3 attempts: {last_qa}")


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



def parse_srt_entries(path: Path) -> List[Tuple[float, float, str]]:
    txt = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    entries: List[Tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", txt.strip()):
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        a, b = lines[1].split(" --> ", 1)
        entries.append((parse_srt_time(a), parse_srt_time(b), clean_text(" ".join(lines[2:]))))
    return entries


def compact_captions(path: Path, max_words: int = 6, max_chars: int = 34) -> None:
    """Split long TTS sentence cues into TikTok-friendly bites that never overflow the frame."""
    entries = parse_srt_entries(path)
    out: List[Tuple[float, float, str]] = []
    for start, end, text in entries:
        toks = text.split()
        chunks: List[List[str]] = []
        cur: List[str] = []
        for tok in toks:
            candidate = " ".join(cur + [tok])
            if cur and (len(cur) >= max_words or len(candidate) > max_chars):
                chunks.append(cur)
                cur = [tok]
            else:
                cur.append(tok)
        if cur:
            chunks.append(cur)
        if len(chunks) >= 2 and len(chunks[-1]) <= 2:
            merged = chunks[-2] + chunks[-1]
            if len(merged) <= max_words + 2 and len(" ".join(merged)) <= max_chars + 12:
                chunks[-2:] = [merged]
        if not chunks:
            continue
        weights = [max(1, sum(len(w.strip(".,!?;:'\"—-")) for w in c)) for c in chunks]
        total_w = sum(weights)
        duration = max(0.01, end - start)
        cursor = start
        elapsed_weight = 0
        for i, (chunk, weight) in enumerate(zip(chunks, weights)):
            elapsed_weight += weight
            stop = end if i == len(chunks) - 1 else start + duration * elapsed_weight / total_w
            out.append((cursor, stop, " ".join(chunk)))
            cursor = stop

    lines: List[str] = []
    for i, (a, b, text) in enumerate(out, 1):
        lines.extend([str(i), f"{fmt_srt_time(a)} --> {fmt_srt_time(b)}", text, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def wrap_caption(text: str, width: int = 22) -> str:
    parts = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
    if len(parts) <= 2:
        return r"\N".join(parts)
    # compact_captions should make this rare; keep only two balanced lines rather than overflowing.
    words_ = text.split()
    mid = max(1, len(words_) // 2)
    return " ".join(words_[:mid]) + r"\N" + " ".join(words_[mid:])


def make_ass_from_srt(srt: Path, ass: Path) -> None:
    entries = parse_srt_entries(srt)
    font_size = int(CFG.get("caption_font_size", 46))
    margin_v = int(CFG.get("caption_margin_v", 300))
    margin_lr = int(CFG.get("caption_margin_lr", 105))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: TikTok,DejaVu Sans,{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H60000000,-1,0,0,0,100,100,0,0,1,4,1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [header.rstrip()]
    for start, end, text in entries:
        wrapped = wrap_caption(text, width=22)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},TikTok,,0,0,0,,{ass_escape(wrapped)}")
    ass.write_text("\n".join(lines) + "\n", encoding="utf-8")

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
                choice = fresh[0]
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
    # Preserve footage direction; horizontal flipping can make text, tools and human actions look wrong.
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},eq=contrast=1.03:saturation=0.97:brightness=-0.008"
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


def render_final(base_video: Path, voice: Path, ass: Path, out: Path) -> None:
    sub = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = f"ass='{sub}'"
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

    forced_topic = bool(os.getenv("TOPIC", "").strip())
    prep_error: Exception | None = None
    for prep_attempt in range(1, 4):
        topic = choose_topic()
        log(f"Selected topic (attempt {prep_attempt}): {topic['title']}")
        try:
            source_title, extract, source_url = wikipedia_search(topic)
            log(f"Factual source: {source_title}")
            plan, plan_qa = build_validated_plan(topic, source_title, extract)
            break
        except Exception as e:
            prep_error = e
            log(f"Pre-production rejected topic/source/plan: {e}")
            if forced_topic or prep_attempt == 3:
                raise
    else:
        raise RuntimeError(f"Pre-production failed: {prep_error}")

    script = clean_text(" ".join(s["narration"] for s in plan["scenes"]))
    log(f"Narration word count: {words(script)}")

    voice = WORK / "voice.mp3"
    srt = WORK / "captions.srt"
    asyncio.run(make_tts(script, voice, srt))
    total = ensure_min_duration(voice, srt, minimum=float(CFG.get("minimum_seconds", 61.5)))
    compact_captions(srt, max_words=int(CFG.get("caption_max_words", 6)), max_chars=int(CFG.get("caption_max_chars", 34)))
    ass = WORK / "captions.ass"
    make_ass_from_srt(srt, ass)
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
    render_final(base_video, voice, ass, final)
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
        "plan_qa": plan_qa,
        "pexels_sources": pexels_sources,
        "generated_unix": int(time.time()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(srt, OUT / "captions.srt")
    shutil.copy2(ass, OUT / "captions.ass")

    log(f"DONE: {final}")
    log(f"Duration: {ffprobe_duration(final):.2f}s | size: {final.stat().st_size/1024/1024:.1f} MB")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR: {e}")
        raise
