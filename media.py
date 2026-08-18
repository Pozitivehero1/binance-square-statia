from __future__ import annotations

import hashlib
import random
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from config import Settings
from models import MediaClip, Scene
from utils import LOG, request_with_retry


class MediaProvider:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": cfg.pexels_api_key,
            "User-Agent": "CryptoShortsBot/2.1",
        })
        # Never send the API Authorization header to video CDN download URLs.
        self.download_session = requests.Session()
        self.download_session.headers.update({"User-Agent": "CryptoShortsBot/2.1"})

    def collect(self, scenes: list[Scene], work_dir: Path, recent_ids: list[int] | None = None) -> list[MediaClip]:
        recent = {int(x) for x in (recent_ids or []) if str(x).isdigit()}
        selected: list[MediaClip] = []
        used: set[int] = set()
        for idx, scene in enumerate(scenes):
            clip: MediaClip | None = None
            if self.cfg.pexels_api_key:
                try:
                    candidates = self._search(scene.visual_query)
                    candidates = [c for c in candidates if c.video_id not in used]
                    if candidates:
                        candidates.sort(key=lambda c: self._score(c, recent), reverse=True)
                        clip = candidates[0]
                        out = work_dir / f"pexels_{idx+1:02d}.mp4"
                        self._download(clip.url, out)
                        clip.local_path = out
                        used.add(int(clip.video_id or 0))
                        LOG.info("Scene %d media: Pexels #%s — %s", idx + 1, clip.video_id, scene.visual_query)
                except Exception as exc:
                    LOG.warning("Pexels scene %d failed (%s); using generated visual", idx + 1, exc)
            if clip is None:
                clip = self._generated(scene, work_dir, idx)
                LOG.info("Scene %d media: generated fallback", idx + 1)
            selected.append(clip)
        return selected

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
    def _score(clip: MediaClip, recent: set[int]) -> float:
        score = 100.0
        if clip.video_id in recent:
            score -= 90
        ratio = clip.height / max(clip.width, 1)
        score -= abs(ratio - 16 / 9) * 20
        score += min(clip.width, 1080) / 54
        if 4 <= clip.duration <= 25:
            score += 12
        return score + random.uniform(0, 2.5)

    def _download(self, url: str, out: Path) -> None:
        # Use a credential-free session for CDN media. request_with_retry retries only
        # transient failures and keeps 4xx/auth errors immediate.
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

    def _generated(self, scene: Scene, work_dir: Path, index: int) -> MediaClip:
        path = work_dir / f"generated_{index+1:02d}.png"
        query = scene.visual_query.lower()
        seed = int(hashlib.sha1((scene.visual_query + str(index)).encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        w, h = 720, 1280
        # Local fallbacks are deliberately illustrative/non-photorealistic. They are
        # thematic enough to support the spoken scene without pretending to be real footage.
        palette = rng.choice([
            ((7, 13, 28), (18, 48, 74)), ((12, 10, 28), (58, 24, 73)),
            ((6, 22, 28), (12, 68, 62)), ((20, 12, 18), (82, 35, 34)),
        ])
        strip = Image.new("RGB", (1, h))
        spx = strip.load()
        for y in range(h):
            t = y / max(1, h - 1)
            spx[0, y] = tuple(int(a * (1 - t) + b * t) for a, b in zip(palette[0], palette[1]))
        img = strip.resize((w, h))
        draw = ImageDraw.Draw(img, "RGBA")

        # Shared ambient geometry.
        for _ in range(10):
            x = rng.randint(-120, w + 120); y = rng.randint(0, h); r = rng.randint(30, 150)
            draw.ellipse((x-r, y-r, x+r, y+r), outline=(255, 255, 255, rng.randint(7, 20)), width=rng.randint(1, 3))

        security_words = ("security", "phishing", "seed phrase", "2fa", "authentication", "cyber")
        book_words = ("order book", "liquidity", "spread", "market order", "limit order", "fees")
        wallet_words = ("wallet", "stablecoin", "custody", "yield")
        server_words = ("server", "technology", "network", "blockchain")

        if any(k in query for k in security_words):
            # Network nodes + a simple lock silhouette.
            nodes = [(rng.randint(70, w-70), rng.randint(260, 980)) for _ in range(15)]
            for a, b in zip(nodes, nodes[1:]):
                draw.line((*a, *b), fill=(255,255,255,45), width=2)
            for x, y in nodes:
                draw.ellipse((x-10,y-10,x+10,y+10), fill=(255,255,255,110), outline=(255,255,255,150), width=2)
            cx, cy = w//2, h//2
            draw.arc((cx-95, cy-170, cx+95, cy+20), 190, 350, fill=(255,255,255,180), width=14)
            draw.rounded_rectangle((cx-125,cy-60,cx+125,cy+180), radius=28, outline=(255,255,255,180), width=8, fill=(0,0,0,35))
            draw.ellipse((cx-18,cy+25,cx+18,cy+61), fill=(255,255,255,170))
            draw.rectangle((cx-8,cy+55,cx+8,cy+110), fill=(255,255,255,150))
        elif any(k in query for k in book_words):
            # Abstract bid/ask depth bars around a center price line.
            mid = h//2
            draw.line((70, mid, w-70, mid), fill=(255,255,255,100), width=3)
            for i in range(11):
                y = mid - 55 - i*38
                bw = rng.randint(100, 420)
                draw.rounded_rectangle((80,y,min(w-90, w//2 + bw//3),y+22), radius=8, fill=(120,220,205,rng.randint(55,100)))
                y2 = mid + 35 + i*38
                draw.rounded_rectangle((max(90, w//2 - bw//3),y2,w-80,y2+22), radius=8, fill=(245,145,145,rng.randint(55,100)))
            for x in range(90, w-80, 70):
                draw.line((x,220,x,1060), fill=(255,255,255,18), width=1)
        elif any(k in query for k in wallet_words):
            # Wallet/cards/coins visual.
            draw.rounded_rectangle((105,370,615,850), radius=55, fill=(0,0,0,65), outline=(255,255,255,120), width=5)
            draw.rounded_rectangle((360,515,650,690), radius=30, fill=(255,255,255,45), outline=(255,255,255,110), width=4)
            draw.ellipse((430,565,505,640), outline=(255,255,255,170), width=6)
            for x,y,r in [(185,330,72),(555,315,55),(190,925,58),(540,930,78)]:
                draw.ellipse((x-r,y-r,x+r,y+r), outline=(255,255,255,90), width=4, fill=(255,255,255,20))
                draw.ellipse((x-r//2,y-r//2,x+r//2,y+r//2), outline=(255,255,255,70), width=2)
        elif any(k in query for k in server_words):
            # Data/network panel.
            for row in range(6):
                y = 300 + row*120
                draw.rounded_rectangle((85,y,635,y+76), radius=15, outline=(255,255,255,70), width=3, fill=(0,0,0,35))
                for col in range(5):
                    x = 125 + col*92
                    draw.ellipse((x-7,y+30,x+7,y+44), fill=(255,255,255,rng.randint(70,150)))
                draw.line((390,y+38,590,y+38), fill=(255,255,255,50), width=4)
        else:
            # Trading/volatility fallback: abstract candles + path, never labelled as real data.
            baseline = int(h * 0.72)
            last = int(h * 0.56)
            points = []
            for i in range(18):
                x = 45 + i * 38
                last += rng.randint(-55, 55)
                last = max(int(h * 0.28), min(int(h * 0.78), last))
                points.append((x, last))
                draw.line((x, last-45, x, last+45), fill=(255,255,255,80), width=2)
                top, bottom = sorted((last, last + rng.randint(-35, 35)))
                draw.rounded_rectangle((x-8, top, x+8, max(top+8, bottom)), radius=3, fill=(255,255,255,95))
            draw.line(points, fill=(255, 255, 255, 115), width=4, joint="curve")
            draw.rectangle((0, baseline, w, baseline+2), fill=(255,255,255,25))

        img.save(path, quality=92)
        return MediaClip(video_id=None, query=scene.visual_query, local_path=path, source="generated", is_image=True, width=w, height=h, duration=12)
