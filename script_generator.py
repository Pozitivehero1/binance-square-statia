from __future__ import annotations

import json
import re
from typing import Any

import requests

from config import Settings
from models import Scene, Script, Topic
from utils import LOG, clean_spaces, max_similarity, request_with_retry

BANNED = (
    "гарантированн", "без риска", "точно заработ", "легкие деньги", "лёгкие деньги", "100% прибыль",
    "guaranteed profit", "risk-free", "easy money", "100% profit", "guaranteed returns", "definitely pump",
)
FILLER = (
    "каждая точка на графике", "чей-то риск", "рынок не прощает", "всё может измениться за секунду",
    "every point on the chart", "someone's risk", "market never forgives", "anything can change in a second",
)
LIVE_WORDS = (
    "вчера", "сегодня", "только что", "прямо сейчас", "сейчас торгуется",
    "yesterday", "today", "just now", "right now", "live now",
)


class ScriptGenerator:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {cfg.mistral_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "CryptoShortsBot/2.4",
        })

    def create(self, topic: Topic, recent_hooks: list[str] | None = None, *, length_feedback: str = "") -> Script:
        recent_hooks = [clean_spaces(x) for x in (recent_hooks or []) if clean_spaces(x)][-12:]
        count = max(1, min(self.cfg.script_candidates, 4))
        lang = "Russian" if self.cfg.language == "ru" else "English"
        wpm = 125 if self.cfg.language == "ru" else 145
        target_words = round(self.cfg.target_seconds * wpm / 60)
        min_words = max(55, round(target_words * 0.86))
        max_words = round(target_words * 1.12)
        avoid = "\n".join(f"- {x[:120]}" for x in recent_hooks) if recent_hooks else "- none"

        prompt = f"""
You are the senior editor of a premium YouTube Shorts channel. Create {count} genuinely different packages in {lang}.
The result must feel like edited short-form video, not an AI slideshow.

FACTS/TOPIC
Title: {topic.title}
Context: {topic.context}
Editorial format: {topic.format_hint}
Target voice: {self.cfg.video_min_seconds}-{self.cfg.video_max_seconds}s; about {min_words}-{max_words} spoken words total.
{length_feedback}

RECENT HOOKS TO AVOID
{avoid}

RULES
- 8-9 scenes. Usually one compact spoken sentence per scene. Visual should change every ~4-6 seconds.
- First sentence is the hook: <=12 words and <=64 characters. `hook` must exactly equal that first sentence.
- Every sentence must add a fact, explanation or useful interpretation. No greetings, filler, generic hype or repeated conclusions.
- For market topics, tell a story: move -> position inside 24h range -> one liquidity/activity metric -> what the data can/cannot prove -> takeaway -> CTA.
- For market topics use only supplied facts. Never invent catalysts, news, forecasts, support/resistance, quotes or future prices.
- Binance ticker data is a rolling 24h snapshot. Never say yesterday/today/just now/right now/вчера/сегодня/только что.
- `visual_mode`: use `stock` for every non-final market scene. For evergreen, prefer stock unless a concept truly needs an abstract graphic. Final scene is `graphic`.
- `visual_query`: 3-7 simple English words describing real stock footage, e.g. `trader smartphone finance`, `financial trading screen`, `crypto chart monitor`. No exact numbers, split-screen, animation directions, Binance/logo, or full sentences.
- `overlay_text`: one visual punch, 1-4 words or one short metric. Never dump multiple numbers into one overlay.
- Never tell the viewer to buy/sell a specific asset. Never promise profit, bonuses, safety or superiority.
- Final scene must end exactly with: {self._required_cta()}
- Final CTA must not claim low spreads, low fees, bonuses, profit, speed, safety or superiority for Binance.
- Do not read legal/referral disclosure aloud.
- title <=70 chars; description 1-2 compact lines with no URL or hashtags; 5-8 tags including shorts.
- For evergreen topics, claims must be directly supported by Context.

Return JSON only matching the schema.
""".strip()
        payload: dict[str, Any] = {
            "model": self.cfg.mistral_model,
            "messages": [
                {"role": "system", "content": "Create accurate, original, high-retention short-form scripts. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.58,
            "max_tokens": 4800,
            "response_format": {"type": "json_schema", "json_schema": {"name": "shorts_variants", "schema": self._schema(count)}},
        }
        try:
            obj = self._request_json(payload)
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in {400, 404, 415, 422}:
                raise
            LOG.warning("Mistral schema mode unavailable (HTTP %s); using JSON mode", status)
            payload["response_format"] = {"type": "json_object"}
            obj = self._request_json(payload)

        variants = obj.get("variants") if isinstance(obj, dict) else None
        if not isinstance(variants, list) or not variants:
            raise RuntimeError("Mistral returned no script variants")
        parsed: list[Script] = []
        for raw in variants:
            try:
                script = self._parse_variant(raw, topic)
                sim = max_similarity(script.hook, recent_hooks)
                if recent_hooks and sim > 0.72:
                    raise ValueError(f"hook too similar to recent content: {sim:.2f}")
                script.quality_score = self._score(script, recent_hooks)
                parsed.append(script)
            except Exception as exc:
                LOG.warning("Discarded script candidate: %s", exc)
        if not parsed:
            raise RuntimeError("All generated script candidates failed validation")
        parsed.sort(key=lambda x: x.quality_score, reverse=True)
        LOG.info("Selected script quality %.1f/100: %s", parsed[0].quality_score, parsed[0].title)
        return parsed[0]

    def _request_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = request_with_retry(self.session, "POST", "https://api.mistral.ai/v1/chat/completions", json=payload)
        raw = resp.json()["choices"][0]["message"]["content"]
        if isinstance(raw, list):
            raw = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in raw)
        text = str(raw).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)

    @staticmethod
    def _schema(candidate_count: int) -> dict[str, Any]:
        scene = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "voiceover": {"type": "string"}, "visual_query": {"type": "string"},
                "overlay_text": {"type": "string"}, "visual_mode": {"type": "string", "enum": ["stock", "graphic"]},
            },
            "required": ["voiceover", "visual_query", "overlay_text", "visual_mode"],
        }
        variant = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "hook": {"type": "string"},
                "scenes": {"type": "array", "items": scene, "minItems": 8, "maxItems": 9},
                "title": {"type": "string"}, "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}, "minItems": 5, "maxItems": 8},
                "source_note": {"type": "string"},
            },
            "required": ["hook", "scenes", "title", "description", "tags", "source_note"],
        }
        return {
            "type": "object", "additionalProperties": False,
            "properties": {"variants": {"type": "array", "items": variant, "minItems": 1, "maxItems": candidate_count}},
            "required": ["variants"],
        }

    def _required_cta(self) -> str:
        return (
            "Хочешь посмотреть Binance? Первая ссылка — в профиле канала."
            if self.cfg.language == "ru"
            else "Want to explore Binance? The first link is in the channel profile."
        )

    def _normalize_final_voice(self, text: str) -> str:
        required = self._required_cta()
        chunks = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", clean_spaces(text)) if x.strip()]
        markers = ("binance", "ссыл", "профил", "link", "profile", "реферал", "referral")
        prefix = " ".join(x for x in chunks if not any(m in x.lower() for m in markers)).strip()
        return (prefix + " " + required).strip()

    @staticmethod
    def _normalize_visual_query(text: str, mode: str) -> str:
        q = str(text or "").lower()
        q = re.sub(r"[\"'“”‘’]", " ", q)
        q = re.sub(r"\b(split[- ]screen|highlighted?|animation|animated|showing|displaying|exact|binance|logo)\b", " ", q)
        q = re.sub(r"\b\d[\d,.]*\b", " ", q)
        q = re.sub(r"[^a-z0-9 -]+", " ", q)
        stop = {"the", "a", "an", "of", "with", "and", "on", "in"}
        words = [w for w in q.split() if w not in stop][:7]
        if mode == "stock" and not any(w in words for w in ("trader", "trading", "finance", "market", "phone", "smartphone", "computer", "screen", "security", "cyber", "chart")):
            words += ["finance", "smartphone"]
        return " ".join(words[:7]) if len(words) >= 2 else ("trader smartphone finance" if mode == "stock" else "crypto market concept")

    def _parse_variant(self, raw: Any, topic: Topic) -> Script:
        if not isinstance(raw, dict) or not isinstance(raw.get("scenes"), list):
            raise ValueError("variant/scenes missing")
        scenes: list[Scene] = []
        for item in raw["scenes"]:
            if not isinstance(item, dict):
                continue
            voice = clean_spaces(item.get("voiceover", ""))
            overlay = clean_spaces(item.get("overlay_text", ""))
            mode = clean_spaces(item.get("visual_mode", "auto")).lower()
            if mode not in {"stock", "graphic"}:
                mode = "auto"
            query = self._normalize_visual_query(item.get("visual_query", ""), "stock" if mode == "stock" else "graphic")
            if voice and query:
                scenes.append(Scene(voice, query[:140], overlay[:55], mode))
        if not scenes:
            raise ValueError("no valid scenes")
        for i, scene in enumerate(scenes):
            if topic.kind == "market" and i < len(scenes) - 1:
                scene.visual_mode = "stock"
            elif scene.visual_mode == "auto":
                scene.visual_mode = "graphic" if i == len(scenes) - 1 else ("graphic" if i in {2, 5} else "stock")
        scenes[-1].visual_mode = "graphic"
        scenes[-1].voiceover = self._normalize_final_voice(scenes[-1].voiceover)

        spoken_hook = re.split(r"(?<=[.!?…])\s+", scenes[0].voiceover, maxsplit=1)[0].strip()
        requested = clean_spaces(raw.get("hook", ""))
        hook = requested if requested.casefold() == spoken_hook.casefold() else spoken_hook
        tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
        script = Script(
            topic=topic.title, hook=hook, scenes=scenes,
            title=clean_spaces(raw.get("title", ""))[:100],
            description=clean_spaces(raw.get("description", ""))[:500],
            tags=[clean_spaces(x).lstrip("#")[:40] for x in tags if clean_spaces(x)],
            source_note=topic.source,
        )
        self._validate(script, topic)
        return script

    def _validate(self, s: Script, topic: Topic) -> None:
        if not 8 <= len(s.scenes) <= 9:
            raise ValueError("scene count must be 8-9")
        words = len(re.findall(r"\b[\w'-]+\b", s.narration, flags=re.UNICODE))
        if not 55 <= words <= 155:
            raise ValueError(f"narration word count out of bounds: {words}")
        if not s.hook or len(s.hook) > 64 or len(s.hook.split()) > 12:
            raise ValueError("bad hook")
        if not s.title or len(s.title) > 70:
            raise ValueError("title must be 1-70 characters")
        if not s.description or "http://" in s.description.lower() or "https://" in s.description.lower() or "#" in s.description:
            raise ValueError("bad description")
        if len(s.tags) < 4:
            raise ValueError("too few tags")
        if any(not x.overlay_text or len(x.overlay_text.split()) > 6 for x in s.scenes):
            raise ValueError("overlay_text must be compact")
        if any(re.search(r"[А-Яа-яЁё]", x.visual_query) for x in s.scenes):
            raise ValueError("visual_query must be English")
        letters = re.findall(r"[A-Za-zА-Яа-яЁё]", s.narration)
        if letters:
            cyr = sum(bool(re.match(r"[А-Яа-яЁё]", ch)) for ch in letters) / len(letters)
            if self.cfg.language == "ru" and cyr < 0.52:
                raise ValueError("narration is not predominantly Russian")
            if self.cfg.language == "en" and cyr > 0.08:
                raise ValueError("narration is not predominantly English")
        lower = (s.hook + " " + s.narration).lower()
        if any(x in lower for x in BANNED):
            raise ValueError("prohibited marketing claim")
        if any(x in lower for x in FILLER):
            raise ValueError("empty finance cliché/filler")
        if len({x.visual_query.lower() for x in s.scenes}) < 5:
            raise ValueError("visual queries are too repetitive")

        required = self._required_cta()
        cta = s.scenes[-1].voiceover.strip()
        if not cta.endswith(required):
            raise ValueError("final CTA missing")
        promo = ("низк", "дешев", "выгод", "лучш", "быстр", "бонус", "скидк", "гарант", "безопасн", "low spread", "lowest", "best fee", "cheapest", "bonus", "discount", "safest")
        if any(x in cta[:-len(required)].lower() for x in promo):
            raise ValueError("unsupported promotional CTA claim")

        modes = [x.visual_mode for x in s.scenes]
        if modes[-1] != "graphic" or any(x not in {"stock", "graphic"} for x in modes):
            raise ValueError("invalid visual modes")
        if topic.kind == "market" and any(x != "stock" for x in modes[:-1]):
            raise ValueError("market scenes must be stock-first")
        if topic.kind != "market" and modes.count("stock") < 3:
            raise ValueError("not enough stock scenes")
        for scene in s.scenes:
            qwords = re.findall(r"[A-Za-z0-9]+", scene.visual_query)
            if not 2 <= len(qwords) <= 10:
                raise ValueError("visual query too long/short")
            if re.search(r"\b(split[- ]screen|highlighted|animation|animated|binance|logo)\b", scene.visual_query, flags=re.I):
                raise ValueError("stock-hostile visual query")

        if topic.kind == "evergreen":
            self._validate_evergreen_claims(s, topic)
        elif topic.kind == "market":
            if any(x in lower for x in LIVE_WORDS):
                raise ValueError("calendar/live wording used for rolling 24h data")
            self._validate_market_percentages(s.narration, topic)
            self._validate_market_money_claims(s.narration, topic)

        if not any(x.lower() == "shorts" for x in s.tags):
            s.tags.append("shorts")
        s.tags[:] = list(dict.fromkeys(s.tags))[:8]
        if len(s.tags) < 5:
            raise ValueError("at least 5 unique tags required")

    @staticmethod
    def _validate_evergreen_claims(script: Script, topic: Topic) -> None:
        text, title = script.narration.lower(), topic.title.lower()
        if "spread" in title or "спред" in title:
            forbidden = ("стоимость ликвидности", "цена ликвидности", "instant cost of liquidity", "узкий спред — быстрые сделки", "narrow spread means fast trades", "гарантирует быстрое исполнение")
            if any(x in text for x in forbidden):
                raise ValueError("misleading spread simplification")
            if "комисси" in text and not re.search(r"не[^.!?]{0,32}комисси", text):
                raise ValueError("spread described as commission")
        if ("stablecoin" in title or "стейблкоин" in title) and re.search(r"(?:полностью|абсолютно) безопас|risk[- ]free|same as cash", text):
            raise ValueError("stablecoin safety overstated")
        if "2fa" in title and re.search(r"гарантирует|guarantees|100%", text):
            raise ValueError("2FA protection overstated")

    @staticmethod
    def _validate_market_percentages(text: str, topic: Topic) -> None:
        values: dict[str, float] = {}
        for key in ("change_1h", "change_24h", "change_7d", "range_24h_pct", "position_in_24h_range_pct"):
            try:
                values[key] = float(topic.data[key])
            except (KeyError, TypeError, ValueError):
                pass
        if not values:
            return
        pos_words = ("вырос", "рост", "прибав", "увелич", "rose", "rise", "gain", "increased", "up ")
        neg_words = ("сниз", "упал", "паден", "потер", "fell", "fall", "drop", "declin", "lost", "down ")
        for match in re.finditer(r"(?<![\w])([+-]?\d+(?:[.,]\d+)?)\s*%", text):
            raw = match.group(1)
            value = float(raw.replace(",", "."))
            ctx = text[max(0, match.start()-75):min(len(text), match.end()+75)].lower()
            directional = False
            if re.search(r"position|позиц|пути от|от минимума до максимума|внутри диапазона|част[ьи] диапазона", ctx):
                allowed = [values["position_in_24h_range_pct"]] if "position_in_24h_range_pct" in values else []
            elif re.search(r"range|диапазон|размах", ctx):
                allowed = [values["range_24h_pct"]] if "range_24h_pct" in values else []
            elif re.search(r"\b24\s*h\b|\b24\s*hours?\b|24\s*час|сут(?:ки|ок)|за\s+(?:день|24)", ctx):
                allowed = [values["change_24h"]] if "change_24h" in values else []
                directional = True
            elif re.search(r"\b7\s*d\b|\b7\s*days?\b|7\s*дн|недел", ctx):
                allowed = [values["change_7d"]] if "change_7d" in values else []
                directional = True
            elif re.search(r"\b1\s*h\b|\b1\s*hour\b|за\s+(?:один|1)\s+час|за\s+час\b", ctx):
                allowed = [values["change_1h"]] if "change_1h" in values else []
                directional = True
            else:
                allowed = list(values.values())
                directional = True
            if not allowed:
                raise ValueError(f"unsupported percentage metric: {raw}%")
            semantic = 1 if any(w in ctx for w in pos_words) else (-1 if any(w in ctx for w in neg_words) else 0)
            explicit = 1 if raw.startswith("+") else (-1 if raw.startswith("-") else 0)
            claim_sign = explicit or semantic
            ok = False
            for actual in allowed:
                tol = max(0.18, abs(actual) * 0.018)
                magnitude = abs(abs(value) - abs(actual)) <= tol
                direction = (not directional) or claim_sign == 0 or (actual > 0 and claim_sign > 0) or (actual < 0 and claim_sign < 0) or actual == 0
                ok |= magnitude and direction
            if not ok:
                raise ValueError(f"unsupported percentage/metric/direction: {raw}%")

    @staticmethod
    def _validate_market_money_claims(text: str, topic: Topic) -> None:
        fields: dict[str, float] = {}
        for key in ("price", "high_24h", "low_24h", "volume_24h", "weighted_avg_24h", "market_cap"):
            try:
                value = float(topic.data[key])
                if value > 0:
                    fields[key] = value
            except (KeyError, TypeError, ValueError):
                pass
        if not fields:
            return
        labels = {
            "high_24h": ("high", "maximum", "максим"), "low_24h": ("low", "minimum", "миним"),
            "volume_24h": ("volume", "объем", "объём"), "weighted_avg_24h": ("weighted average", "средневзвеш", "средняя цена"),
            "market_cap": ("market cap", "capitalization", "капитализац"), "price": ("price", "цена", "торгуется", "стоит"),
        }
        for match in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([KkMmBb]?)", text):
            raw, suffix = match.group(1), match.group(2)
            claim = float(raw.replace(",", "")) * {"k":1e3,"m":1e6,"b":1e9}.get(suffix.lower(), 1.0)
            before = text[max(0, match.start()-55):match.start()].lower()
            after = text[match.end():min(len(text), match.end()+35)].lower()
            nearest: tuple[int, str] | None = None
            for key, words in labels.items():
                pos = max((before.rfind(w) for w in words), default=-1)
                if pos >= 0 and (nearest is None or pos > nearest[0]):
                    nearest = (pos, key)
            if nearest:
                keys = [nearest[1]]
            else:
                postfix = next((k for k, words in labels.items() if any(w in after for w in words)), None)
                keys = [postfix] if postfix else list(fields)
            allowed = [fields[k] for k in keys if k in fields]
            if not allowed or not any(abs(claim-a) <= max(0.02*abs(a), 0.01) for a in allowed):
                raise ValueError(f"unsupported/mislabelled dollar value: ${raw}{suffix}")

    def _score(self, s: Script, recent_hooks: list[str]) -> float:
        score = 72.0
        hw = len(s.hook.split())
        score += 8 if 4 <= hw <= 12 else -6
        score -= max_similarity(s.hook, recent_hooks) * 35
        target = self.cfg.target_seconds * (125 if self.cfg.language == "ru" else 145) / 60
        score -= min(14, abs(len(s.narration.split()) - target) / max(target, 1) * 24)
        score += 5 if len(s.title) <= 70 else -5
        score += min(6, len({x.visual_query.lower() for x in s.scenes}))
        if all(x.visual_mode == "stock" for x in s.scenes[:-1]):
            score += 4
        return max(0.0, min(100.0, score))
