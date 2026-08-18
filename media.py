from __future__ import annotations

import hashlib
import math
import random
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFilter

from config import Settings
from models import MediaClip, Scene
from utils import LOG, request_with_retry

FINANCE_WORDS = {
    "crypto", "cryptocurrency", "bitcoin", "trading", "trader", "market", "finance", "financial",
    "chart", "exchange", "portfolio", "investment", "stocks", "stock", "phone", "smartphone",
    "tablet", "computer", "laptop", "screen", "candlestick", "currency", "money",
}
SECURITY_WORDS = {"security", "cyber", "phishing", "password", "authentication", "2fa", "hacker", "data"}
BAD_SLUG_WORDS = {
    "clothes", "fashion", "shopping", "dress", "food", "cooking", "restaurant", "wedding", "makeup",
    "fitness", "gym", "garden", "car", "driving", "dog", "cat", "baby", "coffee", "beach", "vacation",
}


class MediaProvider:
    """Select cinematic stock first; use clean motion-background art only as fallback.

    Exact values are shown by the typography layer in video_builder.py. The background
    never pretends that a stock clip contains the exact price/volume being narrated.
    This keeps footage honest while avoiding slide-deck style number cards.
    """

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Authorization": cfg.pexels_api_key, "User-Agent": "CryptoShortsBot/2.4"})
        self.download_session = requests.Session()
        self.download_session.headers.update({"User-Agent": "CryptoShortsBot/2.4"})

    def collect(self, scenes: list[Scene], work_dir: Path, recent_ids: list[int] | None = None) -> list[MediaClip]:
        recent = {int(x) for x in (recent_ids or []) if str(x).isdigit()}
        used: set[int] = set()
        selected: list[MediaClip] = []
        total = len(scenes)

        for idx, scene in enumerate(scenes):
            if idx == total - 1:
                clip = self._generated(scene, work_dir, idx, kind="cta")
                selected.append(clip)
                LOG.info("Scene %d media: generated cinematic CTA background", idx + 1)
                continue

            clip: MediaClip | None = None
            if self.cfg.pexels_api_key:
                try:
                    queries = self._query_candidates(scene)
                    best: tuple[float, MediaClip] | None = None
                    for q_index, query in enumerate(queries):
                        candidates = [c for c in self._search(query) if c.video_id not in used]
                        candidates = [c for c in candidates if self._relevance(c, query) >= 0.38]
                        if candidates:
                            candidates.sort(key=lambda c: self._score(c, recent, query), reverse=True)
                            candidate = candidates[0]
                            score = self._score(candidate, recent, query)
                            if best is None or score > best[0]:
                                best = (score, candidate)
                            if score >= 78 or q_index == len(queries) - 1:
                                break
                    if best:
                        clip = best[1]
                        out = work_dir / f"pexels_{idx+1:02d}.mp4"
                        self._download(clip.url, out)
                        clip.local_path = out
                        used.add(int(clip.video_id or 0))
                        LOG.info("Scene %d media: Pexels #%s — %s", idx + 1, clip.video_id, clip.query)
                except Exception as exc:
                    LOG.warning("Pexels scene %d failed (%s); using cinematic fallback", idx + 1, exc)

            if clip is None:
                kind = "security" if self._is_security(scene) else "market"
                clip = self._generated(scene, work_dir, idx, kind=kind)
                LOG.info("Scene %d media: generated cinematic %s fallback", idx + 1, kind)
            selected.append(clip)
        return selected

    def _query_candidates(self, scene: Scene) -> list[str]:
        raw = self._stock_query(scene.visual_query)
        q = (scene.visual_query + " " + scene.voiceover + " " + scene.overlay_text).lower()
        if self._is_security(scene):
            fallbacks = ["cyber security smartphone", "data security computer screen"]
        elif any(k in q for k in ("trader", "phone", "smartphone", "app", "mobile")):
            fallbacks = ["trader smartphone finance", "cryptocurrency trading phone"]
        elif any(k in q for k in ("chart", "price", "candlestick", "volume", "market")):
            fallbacks = ["financial trading screen", "cryptocurrency market chart"]
        else:
            fallbacks = ["cryptocurrency trading screen", "finance smartphone market"]
        out: list[str] = []
        for x in [raw, *fallbacks]:
            x = re.sub(r"\s+", " ", x).strip()
            if x and x not in out:
                out.append(x)
        return out[:2]

    @staticmethod
    def _is_security(scene: Scene) -> bool:
        q = (scene.visual_query + " " + scene.voiceover).lower()
        return any(x in q for x in SECURITY_WORDS)

    @staticmethod
    def _stock_query(query: str) -> str:
        q = re.sub(r"['\"“”‘’]", " ", str(query).lower())
        q = re.sub(r"\b(split[- ]screen|animated?|animation|highlight(?:ed)?|showing|displaying|exact|binance|logo)\b", " ", q)
        q = re.sub(r"\b\d[\d,.]*\b", " ", q)
        q = re.sub(r"[^a-z0-9 -]+", " ", q)
        stop = {"the", "a", "an", "of", "with", "and", "on", "in", "show", "shows"}
        words = [w for w in q.split() if w not in stop]
        return " ".join(words[:7]) or "cryptocurrency trading screen"

    def _search(self, query: str) -> list[MediaClip]:
        resp = request_with_retry(
            self.session,
            "GET",
            "https://api.pexels.com/v1/videos/search",
            params={"query": query, "orientation": "portrait", "size": "medium", "per_page": self.cfg.pexels_per_page},
        )
        out: list[MediaClip] = []
        for video in resp.json().get("videos", []):
            file = self._best_file(video.get("video_files", []))
            if not file:
                continue
            user = video.get("user") or {}
            out.append(MediaClip(
                video_id=int(video.get("id")), query=query, url=str(file["link"]),
                duration=float(video.get("duration") or 6), width=int(file.get("width") or 720),
                height=int(file.get("height") or 1280), creator_name=str(user.get("name") or ""),
                creator_url=str(user.get("url") or ""), pexels_url=str(video.get("url") or ""), source="pexels",
            ))
        return out

    @staticmethod
    def _best_file(files: list[dict]) -> dict | None:
        usable = [
            f for f in files
            if f.get("link") and f.get("width") and f.get("height")
            and str(f.get("file_type", "video/mp4")).endswith("mp4")
        ]
        if not usable:
            return None

        def score(f: dict) -> float:
            w, h = int(f["width"]), int(f["height"])
            ratio = h / max(w, 1)
            portrait = 90 if h > w else -120
            ratio_score = -abs(ratio - 16 / 9) * 40
            resolution = min(w, 1080) / 14
            quality = 14 if str(f.get("quality") or "").lower() == "hd" else 0
            huge = -25 if w > 1920 else 0
            return portrait + ratio_score + resolution + quality + huge

        return max(usable, key=score)

    @staticmethod
    def _slug_tokens(url: str) -> set[str]:
        try:
            slug = Path(urlparse(url).path.rstrip("/")).name
        except Exception:
            slug = url
        return {x for x in re.findall(r"[a-z]+", slug.lower()) if len(x) > 2}

    @classmethod
    def _relevance(cls, clip: MediaClip, query: str) -> float:
        q = {x for x in re.findall(r"[a-z]+", query.lower()) if len(x) > 2}
        slug = cls._slug_tokens(clip.pexels_url)
        if not slug:
            return 0.45
        if slug & BAD_SLUG_WORDS:
            return 0.05
        direct = len(q & slug) / max(1, min(5, len(q)))
        finance_requested = bool(q & FINANCE_WORDS)
        finance_slug = bool(slug & FINANCE_WORDS)
        security_requested = bool(q & SECURITY_WORDS)
        security_slug = bool(slug & SECURITY_WORDS)
        if finance_requested and not finance_slug and not security_slug:
            return min(0.26, direct)
        if security_requested and not security_slug and not finance_slug:
            return min(0.28, direct)
        bonus = 0.38 if finance_slug else 0.0
        if security_slug:
            bonus += 0.28
        if slug & {"phone", "smartphone", "tablet", "laptop", "computer", "screen"}:
            bonus += 0.12
        return min(1.0, direct + bonus)

    @classmethod
    def _score(cls, clip: MediaClip, recent: set[int], query: str) -> float:
        score = cls._relevance(clip, query) * 120
        if clip.video_id in recent:
            score -= 100
        ratio = clip.height / max(clip.width, 1)
        score -= abs(ratio - 16 / 9) * 24
        score += min(clip.width, 1080) / 45
        if 5 <= clip.duration <= 24:
            score += 15
        elif clip.duration < 3:
            score -= 20
        return score + random.uniform(0, 1.2)

    def _download(self, url: str, out: Path) -> None:
        resp = request_with_retry(self.download_session, "GET", url, stream=True, timeout=90)
        try:
            expected = int(resp.headers.get("Content-Length") or 0)
            max_bytes = 180 * 1024 * 1024
            if expected > max_bytes:
                raise RuntimeError(f"Pexels asset is unexpectedly large: {expected} bytes")
            written = 0
            with out.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=512 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError("Pexels asset exceeded the 180 MiB safety limit")
                    f.write(chunk)
            if out.stat().st_size < 80_000 or (expected and out.stat().st_size < expected * 0.9):
                raise RuntimeError("Pexels download is incomplete")
        finally:
            resp.close()

    def _generated(self, scene: Scene, work_dir: Path, index: int, *, kind: str = "market") -> MediaClip:
        """Generate a text-free cinematic fallback, never a dashboard/card."""
        path = work_dir / f"generated_{index+1:02d}_{kind}.png"
        seed = int(hashlib.sha1((scene.visual_query + scene.voiceover + str(index)).encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        w, h = 1080, 1920

        accent = rng.choice([(11, 153, 166), (89, 85, 220), (193, 70, 101), (21, 122, 88)])
        sw, sh = 270, 480
        small = Image.new("RGB", (sw, sh), (5, 8, 15))
        pix = small.load()
        gx, gy = rng.randint(45, 225), rng.randint(90, 365)
        for y in range(sh):
            for x in range(sw):
                base = 5 + int(8 * y / sh)
                dx, dy = (x - gx) / sw, (y - gy) / sh
                glow = math.exp(-(dx * dx + dy * dy) / 0.08)
                pix[x, y] = tuple(min(255, int(base + c * 0.28 * glow)) for c in accent)
        img = small.resize((w, h), Image.Resampling.BICUBIC)

        draw = ImageDraw.Draw(img, "RGBA")
        for x in range(-300, w + 400, 115):
            draw.line((x, 260, x + 560, 1650), fill=(255, 255, 255, 10), width=1)
        for y in range(320, 1660, 120):
            draw.line((70, y, w - 70, y), fill=(255, 255, 255, 9), width=1)

        if kind == "security":
            nodes = [(rng.randint(80, w - 80), rng.randint(380, 1480)) for _ in range(30)]
            for i, a in enumerate(nodes):
                near = sorted(nodes, key=lambda b: (a[0]-b[0])**2 + (a[1]-b[1])**2)[1:3]
                for b in near:
                    draw.line((*a, *b), fill=(*accent, 30), width=2)
            for x, y in nodes:
                r = rng.randint(3, 8)
                draw.ellipse((x-r, y-r, x+r, y+r), fill=(*accent, rng.randint(90, 180)))
            pts = [(540, 640), (720, 720), (690, 1040), (540, 1210), (390, 1040), (360, 720)]
            draw.line(pts + [pts[0]], fill=(255, 255, 255, 105), width=8, joint="curve")
        elif kind == "cta":
            for r, alpha in [(330, 15), (250, 22), (170, 30)]:
                draw.ellipse((540-r, 880-r, 540+r, 880+r), outline=(*accent, alpha), width=5)
            points = [(200, 1320), (360, 1210), (520, 1250), (680, 1040), (870, 900)]
            draw.line(points, fill=(255, 255, 255, 105), width=9, joint="curve")
            draw.polygon([(870, 900), (815, 910), (855, 955)], fill=(255, 255, 255, 130))
        else:
            variant = index % 4
            if variant in {0, 1}:
                points = []
                y = rng.randint(760, 1180)
                for i in range(25):
                    x = 45 + i * 44
                    y += rng.randint(-95, 85)
                    y = max(500, min(1390, y))
                    points.append((x, y))
                    if variant == 0 or i % 2 == 0:
                        wick = rng.randint(40, 115)
                        body = rng.randint(24, 66)
                        up = rng.choice([True, True, False])
                        c = (52, 215, 160, 85) if up else (244, 93, 119, 85)
                        draw.line((x, y - wick, x, y + wick), fill=c, width=3)
                        draw.rounded_rectangle((x-8, y-body//2, x+8, y+body//2), radius=4, fill=c)
                glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                gd = ImageDraw.Draw(glow, "RGBA")
                gd.line(points, fill=(*accent, 125), width=18, joint="curve")
                glow = glow.filter(ImageFilter.GaussianBlur(18))
                img = Image.alpha_composite(img.convert("RGBA"), glow)
                draw = ImageDraw.Draw(img, "RGBA")
                draw.line(points, fill=(*accent, 195), width=5, joint="curve")
                for i in range(30):
                    x = 45 + i * 34
                    bh = rng.randint(30, 190)
                    draw.rounded_rectangle((x, 1500-bh, x+14, 1500), radius=5, fill=(*accent, rng.randint(25, 70)))
            elif variant == 2:
                draw.line((540, 430, 540, 1430), fill=(255,255,255,45), width=2)
                for row in range(15):
                    y = 500 + row * 62
                    left = rng.randint(80, 450)
                    right = rng.randint(630, 1000)
                    draw.line((left, y, 520, y), fill=(54, 215, 164, rng.randint(35,90)), width=rng.randint(5,12))
                    draw.line((560, y+20, right, y+20), fill=(244, 93, 119, rng.randint(35,90)), width=rng.randint(5,12))
                for r in (130, 230, 360):
                    draw.arc((540-r, 900-r, 540+r, 900+r), 205, 335, fill=(*accent, 40), width=4)
            else:
                for x in range(90, 1020, 90):
                    draw.line((x, 430, x, 1450), fill=(255,255,255,8), width=1)
                for _ in range(18):
                    x1 = rng.randint(80, 850); y1 = rng.randint(500, 1320)
                    x2 = min(1030, x1 + rng.randint(80, 260)); y2 = y1 + rng.randint(-120,120)
                    draw.line((x1,y1,x2,y2), fill=(*accent,rng.randint(35,100)), width=rng.randint(2,6))
                    draw.ellipse((x2-5,y2-5,x2+5,y2+5), fill=(255,255,255,100))

        vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vignette, "RGBA")
        vd.rectangle((0, 0, w, 260), fill=(0, 0, 0, 55))
        vd.rectangle((0, 1450, w, h), fill=(0, 0, 0, 95))
        img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")
        img.save(path, quality=95)
        return MediaClip(
            video_id=None, query=scene.visual_query, local_path=path, source=f"generated:{kind}",
            is_image=True, width=w, height=h, duration=12,
        )
