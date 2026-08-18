from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont

from config import Settings
from models import MediaClip, Scene
from utils import LOG, request_with_retry


FINANCE_QUERY_WORDS = {
    "crypto", "cryptocurrency", "bitcoin", "trading", "trader", "market", "stock", "financial",
    "chart", "exchange", "portfolio", "finance", "investment", "tablet", "phone", "smartphone",
}
GRAPHIC_TERMS = (
    "order book", "bid and ask", "bid ask", "spread", "liquidity", "market order", "limit order",
    "maker", "taker", "fees", "funding rate", "open interest", "market cap", "fdv", "slippage",
    "candlestick", "price chart", "volume chart", "liquidation", "margin", "pnl", "take-profit",
    "trailing stop", "stablecoin", "seed phrase",
)


class MediaProvider:
    """Scene-aware visual provider.

    Pexels is used for footage that stock libraries can realistically represent. Exact
    numbers, order-book mechanics, comparisons and CTA screens are rendered locally so
    the bot never pretends an unrelated stock clip shows data that are not actually there.
    """

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Authorization": cfg.pexels_api_key, "User-Agent": "CryptoShortsBot/2.3"})
        self.download_session = requests.Session()
        self.download_session.headers.update({"User-Agent": "CryptoShortsBot/2.3"})

    def collect(self, scenes: list[Scene], work_dir: Path, recent_ids: list[int] | None = None) -> list[MediaClip]:
        recent = {int(x) for x in (recent_ids or []) if str(x).isdigit()}
        selected: list[MediaClip] = []
        used: set[int] = set()
        total = len(scenes)
        for idx, scene in enumerate(scenes):
            kind = self._graphic_kind(scene, idx, total)
            if kind:
                clip = self._generated(scene, work_dir, idx, kind=kind)
                LOG.info("Scene %d media: generated %s graphic", idx + 1, kind)
                selected.append(clip)
                continue

            clip: MediaClip | None = None
            if self.cfg.pexels_api_key:
                try:
                    query = self._stock_query(scene.visual_query)
                    candidates = [c for c in self._search(query) if c.video_id not in used]
                    # Refuse visually suspicious search results instead of forcing a bad stock clip.
                    candidates = [c for c in candidates if self._relevance(c, query) >= 0.42]
                    if candidates:
                        candidates.sort(key=lambda c: self._score(c, recent, query), reverse=True)
                        clip = candidates[0]
                        out = work_dir / f"pexels_{idx+1:02d}.mp4"
                        self._download(clip.url, out)
                        clip.local_path = out
                        used.add(int(clip.video_id or 0))
                        LOG.info("Scene %d media: Pexels #%s — %s", idx + 1, clip.video_id, query)
                    else:
                        LOG.info("Scene %d: no sufficiently relevant Pexels result; using graphic", idx + 1)
                except Exception as exc:
                    LOG.warning("Pexels scene %d failed (%s); using generated visual", idx + 1, exc)
            if clip is None:
                clip = self._generated(scene, work_dir, idx, kind="ambient")
                LOG.info("Scene %d media: generated ambient fallback", idx + 1)
            selected.append(clip)
        return selected

    @staticmethod
    def _stock_query(query: str) -> str:
        """Turn an LLM visual description into something a stock search engine can satisfy."""
        q = re.sub(r"['\"“”‘’]", " ", query.lower())
        q = re.sub(r"\b(split[- ]screen|animated?|animation|highlight(?:ed)?|showing|displaying|with text|exact)\b", " ", q)
        q = re.sub(r"\b\d[\d,.]*\b", " ", q)
        q = re.sub(r"\s+", " ", q).strip(" ,.-")
        words = q.split()
        if len(words) > 9:
            words = words[:9]
        return " ".join(words) or "cryptocurrency trading smartphone"

    @staticmethod
    def _graphic_kind(scene: Scene, index: int, total: int) -> str | None:
        if index == total - 1:
            return "cta"
        q = (scene.visual_query + " " + scene.voiceover + " " + scene.overlay_text).lower()
        numeric = bool(re.search(r"(?:\$|€|₽|\b\d[\d\s,.]*\s*%|\b\d{2,}[\d\s,.]*)", scene.voiceover))
        bidask = "bid" in q and "ask" in q
        spread = "spread" in q or "спред" in q

        # Exact numbers are never delegated to stock footage, regardless of LLM mode.
        if numeric and (bidask or spread):
            return "orderbook"
        if numeric:
            return "data"

        # Respect the editor's visual plan when it is safe to do so. This prevents a
        # seven-scene explainer from turning into seven nearly identical infographics.
        if scene.visual_mode == "stock":
            return None

        if any(x in q for x in ("commission", "комисси", "depth", "глубин", "liquidity", "ликвид")):
            return "concept"
        if spread:
            return "orderbook"
        if bidask or any(x in q for x in ("buyer", "seller", "покупат", "продав")):
            return "comparison"
        if scene.visual_mode == "graphic":
            if any(x in q for x in ("security", "phishing", "2fa", "cyber")):
                return "ambient"
            return "concept"
        if any(term in q for term in GRAPHIC_TERMS):
            return "concept"
        return None

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
        usable = [f for f in files if f.get("link") and f.get("width") and f.get("height") and str(f.get("file_type", "video/mp4")).endswith("mp4")]
        if not usable:
            return None
        def score(f: dict) -> float:
            w, h = int(f["width"]), int(f["height"])
            ratio = h / max(w, 1)
            portrait = 60 if h > w else -80
            ratio_score = -abs(ratio - 16 / 9) * 34
            resolution = min(w, 1080) / 18
            oversize = -30 if w > 1920 else 0
            quality = 8 if str(f.get("quality") or "").lower() == "hd" else 0
            return portrait + ratio_score + resolution + oversize + quality
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
        """Conservative lexical sanity-check based on Pexels' human-readable URL slug."""
        q = {x for x in re.findall(r"[a-z]+", query.lower()) if len(x) > 2}
        slug = cls._slug_tokens(clip.pexels_url)
        if not slug:
            return 0.5
        direct = len(q & slug) / max(1, min(len(q), 5))
        finance_requested = bool(q & FINANCE_QUERY_WORDS)
        finance_slug = bool(slug & FINANCE_QUERY_WORDS)
        if finance_requested and not finance_slug:
            # Blocks examples such as "person browsing clothes on laptop" for a trading scene.
            return min(0.25, direct)
        semantic_bonus = 0.35 if finance_requested and finance_slug else 0.12
        device_bonus = 0.12 if slug & {"phone", "smartphone", "tablet", "laptop", "computer"} else 0.0
        return min(1.0, direct + semantic_bonus + device_bonus)

    @classmethod
    def _score(cls, clip: MediaClip, recent: set[int], query: str) -> float:
        score = cls._relevance(clip, query) * 120
        if clip.video_id in recent:
            score -= 90
        ratio = clip.height / max(clip.width, 1)
        score -= abs(ratio - 16 / 9) * 20
        score += min(clip.width, 1080) / 54
        if 4 <= clip.duration <= 25:
            score += 12
        return score + random.uniform(0, 1.5)

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

    @staticmethod
    def _font(size: int, bold: bool = False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ]
        for p in paths:
            try:
                return ImageFont.truetype(p, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, *, bold: bool = True):
        for size in range(start_size, 23, -2):
            font = MediaProvider._font(size, bold=bold)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= max_width:
                return font
        return MediaProvider._font(24, bold=bold)

    @staticmethod
    def _extract_numbers(text: str) -> list[str]:
        vals = re.findall(r"(?:[$€₽]\s*)?\d[\d\s,.]*(?:\s*%|\s*(?:доллар(?:а|ов)?|USD|USDT))?", text, flags=re.IGNORECASE)
        return [re.sub(r"\s+", " ", x).strip(" .,;:") for x in vals if re.search(r"\d", x)][:4]

    def _generated(self, scene: Scene, work_dir: Path, index: int, *, kind: str | None = None) -> MediaClip:
        kind = kind or self._graphic_kind(scene, index, index + 2) or "ambient"
        path = work_dir / f"generated_{index+1:02d}_{kind}.png"
        seed = int(hashlib.sha1((scene.visual_query + scene.voiceover + str(index)).encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        w, h = 1080, 1920
        palette = rng.choice([
            ((6, 12, 27), (15, 47, 72)), ((12, 9, 28), (55, 22, 72)),
            ((5, 22, 27), (10, 64, 61)), ((19, 11, 18), (77, 31, 33)),
        ])
        strip = Image.new("RGB", (1, h))
        px = strip.load()
        for y in range(h):
            t = y / max(1, h - 1)
            px[0, y] = tuple(int(a * (1 - t) + b * t) for a, b in zip(palette[0], palette[1]))
        img = strip.resize((w, h))
        draw = ImageDraw.Draw(img, "RGBA")

        # Premium-looking ambient depth without external assets.
        for _ in range(14):
            x = rng.randint(-120, w + 120); y = rng.randint(0, h); r = rng.randint(45, 220)
            draw.ellipse((x-r, y-r, x+r, y+r), outline=(255, 255, 255, rng.randint(7, 18)), width=rng.randint(1, 3))
        for y in range(260, 1580, 110):
            draw.line((80, y, w-80, y), fill=(255, 255, 255, 12), width=1)

        title_font = self._font(58, bold=True)
        small_font = self._font(34, bold=False)
        value_font = self._font(72, bold=True)

        if kind == "cta":
            # Text is added later by ASS so it stays perfectly readable and timed. The
            # background supplies only a clean profile/link visual to avoid duplication.
            draw.rounded_rectangle((155, 720, 925, 1510), radius=72, fill=(4, 8, 18, 155), outline=(255, 255, 255, 52), width=3)
            draw.rounded_rectangle((285, 870, 795, 1260), radius=58, fill=(255, 255, 255, 20), outline=(255, 255, 255, 68), width=3)
            draw.ellipse((475, 930, 605, 1060), outline=(255, 255, 255, 190), width=9)
            draw.arc((420, 1035, 660, 1250), 200, 340, fill=(255, 255, 255, 190), width=9)
            # Simple chain-link hint below the profile card.
            draw.arc((395, 1325, 545, 1455), 205, 25, fill=(255,255,255,145), width=10)
            draw.arc((535, 1325, 685, 1455), 155, 335, fill=(255,255,255,145), width=10)
        elif kind in {"orderbook", "comparison"}:
            left = (100, 520, 505, 1240); right = (575, 520, 980, 1240)
            draw.rounded_rectangle(left, radius=42, fill=(26, 143, 115, 70), outline=(122, 235, 207, 130), width=4)
            draw.rounded_rectangle(right, radius=42, fill=(180, 66, 76, 65), outline=(255, 145, 153, 125), width=4)
            draw.text((302, 600), "BID", font=title_font, anchor="mm", fill=(185, 255, 230, 240))
            draw.text((777, 600), "ASK", font=title_font, anchor="mm", fill=(255, 201, 205, 240))
            nums = self._extract_numbers(scene.voiceover)
            if nums:
                bid = nums[0]
                ask = nums[1] if len(nums) > 1 else ("ЛУЧШАЯ ЦЕНА" if self.cfg.language == "ru" else "BEST PRICE")
            else:
                bid = "ЛУЧШАЯ ЦЕНА" if self.cfg.language == "ru" else "BEST BID"
                ask = "ЛУЧШАЯ ЦЕНА" if self.cfg.language == "ru" else "BEST ASK"
            draw.text((302, 735), bid, font=self._fit_text(draw, bid, 330, 68), anchor="mm", fill=(255,255,255,245))
            draw.text((777, 735), ask, font=self._fit_text(draw, ask, 330, 68), anchor="mm", fill=(255,255,255,245))
            for row in range(7):
                yy = 840 + row * 52
                bw = rng.randint(100, 300)
                draw.rounded_rectangle((140, yy, 140+bw, yy+24), radius=8, fill=(126, 238, 207, 90))
                bw2 = rng.randint(100, 300)
                draw.rounded_rectangle((940-bw2, yy, 940, yy+24), radius=8, fill=(255, 145, 153, 85))
        elif kind == "data":
            nums = self._extract_numbers(scene.voiceover)
            draw.rounded_rectangle((105, 430, 975, 1390), radius=64, fill=(3, 8, 18, 175), outline=(255,255,255,55), width=3)
            label = scene.overlay_text.upper()[:32] or "DATA"
            font = self._fit_text(draw, label, 800, 60)
            draw.text((540, 560), label, font=font, anchor="mm", fill=(255,255,255,230))
            if nums:
                y = 780
                for n in nums[:3]:
                    vf = self._fit_text(draw, n, 760, 110)
                    draw.text((540, y), n, font=vf, anchor="mm", fill=(255,255,255,245))
                    y += 230
            else:
                draw.text((540, 920), "MARKET DATA", font=value_font, anchor="mm", fill=(255,255,255,235))
        elif kind == "concept":
            q = (scene.visual_query + " " + scene.overlay_text).lower()
            if any(x in q for x in ("wallet", "seed", "custody", "stablecoin")):
                draw.rounded_rectangle((170, 530, 910, 1250), radius=72, fill=(0,0,0,75), outline=(255,255,255,90), width=4)
                draw.rounded_rectangle((305, 700, 835, 1070), radius=50, fill=(255,255,255,28), outline=(255,255,255,100), width=4)
                draw.ellipse((665, 815, 755, 905), outline=(255,255,255,190), width=7)
            else:
                # Abstract market mechanism: depth bars + central axis.
                mid = 950
                draw.line((120, mid, 960, mid), fill=(255,255,255,105), width=4)
                for i in range(12):
                    yy1 = mid - 90 - i*50; yy2 = mid + 55 + i*50
                    draw.rounded_rectangle((140, yy1, rng.randint(470, 800), yy1+28), radius=9, fill=(112,225,202,rng.randint(50,100)))
                    x = rng.randint(280, 610)
                    draw.rounded_rectangle((x, yy2, 940, yy2+28), radius=9, fill=(245,132,145,rng.randint(50,95)))
        else:
            # General fallback. For security-oriented scenes draw network/lock; otherwise charts.
            q = scene.visual_query.lower()
            if any(k in q for k in ("security", "phishing", "2fa", "authentication", "cyber")):
                nodes = [(rng.randint(130, w-130), rng.randint(430, 1390)) for _ in range(18)]
                for a, b in zip(nodes, nodes[1:]): draw.line((*a, *b), fill=(255,255,255,40), width=2)
                for x, y in nodes: draw.ellipse((x-12,y-12,x+12,y+12), fill=(255,255,255,110))
                cx, cy = w//2, 920
                draw.arc((cx-145, cy-260, cx+145, cy+45), 190, 350, fill=(255,255,255,190), width=18)
                draw.rounded_rectangle((cx-190,cy-80,cx+190,cy+280), radius=42, outline=(255,255,255,190), width=10, fill=(0,0,0,45))
            else:
                last = 980; points=[]
                for i in range(22):
                    x = 90 + i*43; last += rng.randint(-70,70); last=max(520,min(1320,last)); points.append((x,last))
                    draw.line((x,last-65,x,last+65), fill=(255,255,255,75), width=3)
                    draw.rounded_rectangle((x-10,last-25,x+10,last+25), radius=4, fill=(255,255,255,95))
                draw.line(points, fill=(255,255,255,135), width=5, joint="curve")


        img.save(path, quality=94)
        return MediaClip(video_id=None, query=scene.visual_query, local_path=path, source=f"generated:{kind}", is_image=True, width=w, height=h, duration=12)
