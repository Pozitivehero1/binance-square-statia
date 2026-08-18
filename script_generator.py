from __future__ import annotations

import json
import re
from typing import Any

import requests

from config import Settings
from models import Scene, Script, Topic
from utils import LOG, clean_spaces, max_similarity, request_with_retry

BANNED_RU = ["гарантированн", "без риска", "точно заработ", "легкие деньги", "лёгкие деньги", "100% прибыль", "иксы гарант"]
BANNED_EN = ["guaranteed profit", "risk-free", "easy money", "100% profit", "guaranteed returns", "definitely pump"]


class ScriptGenerator:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {cfg.mistral_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "CryptoShortsBot/2.3",
        })

    def create(self, topic: Topic, recent_hooks: list[str] | None = None, *, length_feedback: str = "") -> Script:
        recent_hooks = [clean_spaces(x) for x in (recent_hooks or []) if clean_spaces(x)][-12:]
        candidate_count = max(1, min(self.cfg.script_candidates, 4))
        lang = "Russian" if self.cfg.language == "ru" else "English"
        target = self.cfg.target_seconds
        wpm = 150 if self.cfg.language == "ru" else 160
        target_words = round(target * wpm / 60)
        min_words = max(55, round(target_words * 0.86))
        max_words = round(target_words * 1.12)
        avoid = "\n".join(f"- {h[:120]}" for h in recent_hooks) if recent_hooks else "- none"

        prompt = f"""
You are a senior short-form editor. Produce {candidate_count} genuinely different YouTube Shorts packages in {lang}.
The channel teaches crypto mechanics and market literacy, then uses a soft referral CTA to an exchange.

FACTS/TOPIC
Title: {topic.title}
Context: {topic.context}
Editorial format: {topic.format_hint}
Exchange brand: {self.cfg.exchange_name}
Target voice duration: {self.cfg.video_min_seconds}-{self.cfg.video_max_seconds} seconds, ideally {target:.0f}s.
Narration target: {min_words}-{max_words} spoken words TOTAL across scenes.
{length_feedback}

RECENT HOOKS TO AVOID COPYING
{avoid}

NON-NEGOTIABLE EDITORIAL RULES
- First spoken sentence must create a specific information gap in <= 12 words and <= 64 characters; no fake urgency.
- hook MUST be an exact copy of the first spoken sentence in scenes[0].voiceover.
- Every scene advances the idea. No greetings, filler, "today we will", generic motivation, or repeated conclusions.
- Use 5-7 scenes. Each scene has 1-3 short spoken sentences, one matching English visual_query and visual_mode.
- visual_mode must be "stock" for real-world footage a stock library can plausibly show (hands using phone, cybersecurity, trader at desk), or "graphic" for mechanisms, comparisons, exact numbers, order books and data. Use a healthy mix: normally at least 2 stock scenes and at least 2 graphic scenes; final scene is graphic.
- overlay_text is a punchy 1-5 word phrase in the narration language, not a full subtitle.
- For market topics, only use the supplied numbers. Never invent a catalyst, news story, quote, support/resistance level, or future price.
- Market data is a retrieval snapshot: use rolling-window wording (1h/24h/7d), not "today", "right now", or claims that it is live at publication time.
- Never instruct the viewer to buy/sell a specific asset and never promise returns, bonuses or profit.
- Final scene: useful takeaway first, then end with this exact neutral CTA sentence: "Хочешь посмотреть Binance? Первая ссылка — в профиле канала." for Russian, or "Want to explore Binance? The first link is in the channel profile." for English. Do not attach performance, fee, spread, bonus, profit, safety or superiority claims to Binance.
- Do not read legal/referral disclosure aloud; the app adds it to the description.
- title <= 70 characters, accurate but curiosity-driven.
- description: 1-2 compact lines, no URL, no hashtags.
- tags: 5-8 plain tags, one must be shorts.
- source_note must name only the factual basis provided above.
- visual_query is a concise English visual search/brief phrase: 3-9 simple words. For stock mode, good: "trader hands smartphone market app". Never request split-screen, highlighted text, animation instructions, Binance/logo footage, or full sentences. For graphic mode, describe the concept (for example "bid ask order book spread"); exact numeric values come from voiceover and are rendered locally.
- For evergreen topics, every factual claim must be directly supported by Context. Paraphrase the supplied mechanics; do not invent a new definition, benefit or causal claim.
- Make the variants structurally different, not paraphrases.

Return JSON only, matching the requested schema.
""".strip()

        schema = self._schema(candidate_count)
        payload: dict[str, Any] = {
            "model": self.cfg.mistral_model,
            "messages": [
                {"role": "system", "content": "You create accurate, original, high-retention short-form scripts. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.62,
            "max_tokens": 4300,
            "response_format": {"type": "json_schema", "json_schema": {"name": "shorts_variants", "schema": schema}},
        }
        try:
            obj = self._request_json(payload)
        except requests.HTTPError as exc:
            # Fallback only when the server rejected the schema/payload shape. Auth,
            # rate-limit and server errors should not trigger a duplicate paid request.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in {400, 404, 415, 422}:
                raise
            LOG.warning("Mistral JSON-schema mode unavailable (HTTP %s); falling back to JSON mode", status)
            payload["response_format"] = {"type": "json_object"}
            obj = self._request_json(payload)

        variants = obj.get("variants") if isinstance(obj, dict) else None
        if not isinstance(variants, list) or not variants:
            raise RuntimeError("Mistral returned no script variants")

        parsed: list[Script] = []
        for raw in variants:
            try:
                script = self._parse_variant(raw, topic)
                hook_similarity = max_similarity(script.hook, recent_hooks)
                if recent_hooks and hook_similarity > 0.72:
                    raise ValueError(f"hook is too similar to recent content: {hook_similarity:.2f}")
                script.quality_score = self._score(script, recent_hooks)
                parsed.append(script)
            except Exception as exc:
                LOG.warning("Discarded invalid script candidate: %s", exc)
        if not parsed:
            raise RuntimeError("All generated script candidates failed validation")
        parsed.sort(key=lambda s: s.quality_score, reverse=True)
        best = parsed[0]
        LOG.info("Selected script quality %.1f/100: %s", best.quality_score, best.title)
        return best

    def _request_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = request_with_retry(self.session, "POST", "https://api.mistral.ai/v1/chat/completions", json=payload)
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        if isinstance(raw, list):
            raw = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in raw)
        return json.loads(str(raw).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())

    @staticmethod
    def _schema(candidate_count: int) -> dict[str, Any]:
        scene = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "voiceover": {"type": "string"},
                "visual_query": {"type": "string"},
                "overlay_text": {"type": "string"},
                "visual_mode": {"type": "string", "enum": ["stock", "graphic"]},
            },
            "required": ["voiceover", "visual_query", "overlay_text", "visual_mode"],
        }
        variant = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "hook": {"type": "string"},
                "scenes": {"type": "array", "items": scene, "minItems": 5, "maxItems": 7},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}, "minItems": 5, "maxItems": 8},
                "source_note": {"type": "string"},
            },
            "required": ["hook", "scenes", "title", "description", "tags", "source_note"],
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "variants": {"type": "array", "items": variant, "minItems": 1, "maxItems": candidate_count},
            },
            "required": ["variants"],
        }

    def _required_cta(self) -> str:
        return (
            "Хочешь посмотреть Binance? Первая ссылка — в профиле канала."
            if self.cfg.language == "ru"
            else "Want to explore Binance? The first link is in the channel profile."
        )

    def _normalize_final_voice(self, text: str) -> str:
        """Strip model-written referral sales copy and append the neutral CTA deterministically."""
        required = self._required_cta()
        chunks = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", clean_spaces(text)) if x.strip()]
        referral_markers = ("binance", "ссыл", "профил", "link", "profile", "реферал", "referral")
        kept = [x for x in chunks if not any(m in x.lower() for m in referral_markers)]
        prefix = " ".join(kept).strip()
        return (prefix + " " + required).strip()

    @staticmethod
    def _normalize_visual_query(text: str, mode: str) -> str:
        q = str(text or "").lower()
        q = re.sub(r"[\"'“”‘’]", " ", q)
        q = re.sub(r"\b(split[- ]screen|highlighted?|animation|animated|showing|displaying|exact|binance|logo)\b", " ", q)
        q = re.sub(r"\b\d[\d,.]*\b", " ", q)
        q = re.sub(r"[^a-z0-9 -]+", " ", q)
        words = [w for w in q.split() if w not in {"the", "a", "an", "of", "with", "and"}]
        if mode == "stock" and not any(w in words for w in ("trader", "trading", "finance", "market", "phone", "smartphone", "tablet", "computer", "security", "cyber")):
            words.extend(["finance", "smartphone"])
        words = words[:9]
        if len(words) < 2:
            return "trader smartphone finance" if mode == "stock" else "crypto market concept"
        return " ".join(words)

    def _parse_variant(self, raw: Any, topic: Topic) -> Script:
        if not isinstance(raw, dict):
            raise ValueError("variant is not an object")
        scenes_raw = raw.get("scenes")
        if not isinstance(scenes_raw, list):
            raise ValueError("scenes missing")
        scenes: list[Scene] = []
        for x in scenes_raw:
            if not isinstance(x, dict):
                continue
            voice = clean_spaces(x.get("voiceover", ""))
            overlay = clean_spaces(x.get("overlay_text", ""))
            mode = clean_spaces(x.get("visual_mode", "auto")).lower()
            if mode not in {"stock", "graphic"}:
                mode = "auto"
            query = self._normalize_visual_query(x.get("visual_query", ""), "stock" if mode == "stock" else "graphic")
            if voice and query:
                scenes.append(Scene(voiceover=voice, visual_query=query[:160], overlay_text=overlay[:60], visual_mode=mode))
        if not scenes:
            raise ValueError("no valid scenes")
        # Structured output normally supplies modes, but a deterministic fallback is
        # safer than throwing away an otherwise excellent paid generation.
        for i, scene in enumerate(scenes):
            if scene.visual_mode == "auto":
                scene.visual_mode = "graphic" if i == len(scenes) - 1 or i % 2 == 0 else "stock"
        scenes[-1].visual_mode = "graphic"
        scenes[-1].voiceover = self._normalize_final_voice(scenes[-1].voiceover)

        # The visible hook must be the sentence the viewer actually hears first.
        spoken_hook = re.split(r"(?<=[.!?…])\s+", scenes[0].voiceover, maxsplit=1)[0].strip()
        requested_hook = clean_spaces(raw.get("hook", ""))
        hook = requested_hook if requested_hook and clean_spaces(requested_hook).casefold() == clean_spaces(spoken_hook).casefold() else spoken_hook

        raw_tags = raw.get("tags", [])
        if not isinstance(raw_tags, list):
            raw_tags = []
        script = Script(
            topic=topic.title,
            hook=hook,
            scenes=scenes,
            title=clean_spaces(raw.get("title", ""))[:100],
            description=clean_spaces(raw.get("description", ""))[:500],
            tags=[clean_spaces(t).lstrip("#")[:40] for t in raw_tags if clean_spaces(t)],
            # Never trust the LLM to self-report provenance; provenance comes from the data source.
            source_note=topic.source,
        )
        self._validate(script, topic)
        return script

    def _validate(self, s: Script, topic: Topic) -> None:
        if not (5 <= len(s.scenes) <= 7):
            raise ValueError("scene count must be 5-7")
        words = len(re.findall(r"\b[\w'-]+\b", s.narration, flags=re.UNICODE))
        if words < 55 or words > 170:
            raise ValueError(f"narration word count out of bounds: {words}")
        if not s.hook or len(s.hook) > 64 or len(s.hook.split()) > 12:
            raise ValueError("bad hook")
        if not s.title or len(s.title) > 70:
            raise ValueError("title must be 1-70 characters")
        if not s.description or "http://" in s.description.lower() or "https://" in s.description.lower() or "#" in s.description:
            raise ValueError("description must be non-empty and contain no URL/hashtags")
        if len(s.tags) < 4:
            raise ValueError("too few tags")
        if any(not x.overlay_text or len(x.overlay_text.split()) > 6 for x in s.scenes):
            raise ValueError("overlay_text must be present and compact")
        if any(re.search(r"[А-Яа-яЁё]", x.visual_query) for x in s.scenes):
            raise ValueError("visual_query must be English for stock search")
        letters = re.findall(r"[A-Za-zА-Яа-яЁё]", s.narration)
        if letters:
            cyr = sum(1 for ch in letters if re.match(r"[А-Яа-яЁё]", ch))
            cyr_ratio = cyr / len(letters)
            if self.cfg.language == "ru" and cyr_ratio < 0.52:
                raise ValueError("narration is not predominantly Russian")
            if self.cfg.language == "en" and cyr_ratio > 0.08:
                raise ValueError("narration is not predominantly English")
        full = (s.hook + " " + s.narration).lower()
        banned = BANNED_RU + BANNED_EN
        if any(x in full for x in banned):
            raise ValueError("contains prohibited guarantee/marketing claim")
        if len({x.visual_query.lower() for x in s.scenes}) < 4:
            raise ValueError("visual queries are too repetitive")

        # The last CTA is intentionally fixed and neutral. This prevents the model from
        # inventing exchange advantages such as "low spreads", "best fees" or bonuses.
        cta = s.scenes[-1].voiceover.strip()
        required_cta = self._required_cta()
        if not cta.endswith(required_cta):
            raise ValueError("final scene must end with the exact neutral Binance profile CTA")
        promo_terms = (
            "низк", "дешев", "выгод", "лучш", "быстр", "бонус", "скидк", "гарант", "безопасн",
            "low spread", "lowest", "best fee", "cheapest", "bonus", "discount", "guaranteed", "safest",
        )
        cta_prefix = cta[:-len(required_cta)].lower()
        if any(term in cta_prefix for term in promo_terms):
            raise ValueError("final scene contains an unsupported promotional Binance claim")

        modes = [scene.visual_mode for scene in s.scenes]
        if any(mode not in {"stock", "graphic"} for mode in modes):
            raise ValueError("every generated scene must choose stock or graphic visual_mode")
        if modes[-1] != "graphic":
            raise ValueError("final CTA scene must use graphic visual_mode")
        if len(s.scenes) >= 5 and modes.count("graphic") < 2:
            raise ValueError("visual plan needs at least two controlled graphic scenes")

        for scene in s.scenes:
            query_words = re.findall(r"[A-Za-z0-9]+", scene.visual_query)
            if not (2 <= len(query_words) <= 12):
                raise ValueError("visual_query must be a compact stock-search phrase")
            if re.search(r"\b(split[- ]screen|highlighted|animation|animated|binance|logo)\b", scene.visual_query, flags=re.I):
                raise ValueError("visual_query contains stock-search-hostile shot directions or branding")

        if topic.kind == "evergreen":
            self._validate_evergreen_claims(s, topic)
        if topic.kind == "market":
            self._validate_market_percentages(s.narration, topic)
            self._validate_market_money_claims(s.narration, topic)

        if not any(t.lower() == "shorts" for t in s.tags):
            s.tags.append("shorts")
        s.tags[:] = list(dict.fromkeys(s.tags))[:8]
        if len(s.tags) < 5:
            raise ValueError("at least 5 unique tags are required after normalization")

    @staticmethod
    def _validate_evergreen_claims(script: Script, topic: Topic) -> None:
        """Local guards for high-risk simplifications that frequently sound plausible."""
        text = script.narration.lower()
        title = topic.title.lower()
        if "spread" in title or "спред" in title:
            forbidden = (
                "стоимость ликвидности", "цена ликвидности", "instant cost of liquidity",
                "узкий спред — быстрые сделки", "узкий спред — это быстрые сделки",
                "narrow spread means fast trades", "гарантирует быстрое исполнение",
            )
            if any(x in text for x in forbidden):
                raise ValueError("spread explanation contains an unsupported/misleading simplification")
            if "комисси" in text and not re.search(r"не[^.!?]{0,32}комисси", text):
                raise ValueError("spread must not be described as an exchange commission")
        if "stablecoin" in title or "стейблкоин" in title:
            if re.search(r"(?:полностью|абсолютно) безопас|risk[- ]free|same as cash", text):
                raise ValueError("stablecoin explanation overstates safety")
        if "2fa" in title and re.search(r"гарантирует|guarantees|100%", text):
            raise ValueError("2FA explanation overstates protection")
        if ("leverage" in title or "плеч" in title) and re.search(r"ликвидац[^.]{0,25}ровно|liquidat[^.]{0,25}exactly", text):
            raise ValueError("leverage explanation invents an exact liquidation rule")

    @staticmethod
    def _validate_market_percentages(text: str, topic: Topic) -> None:
        values: dict[str, float] = {}
        for key in ("change_1h", "change_24h", "change_7d"):
            try:
                values[key] = float(topic.data[key])
            except (KeyError, TypeError, ValueError):
                pass
        if not values:
            return

        positive_words = ("вырос", "рост", "прибав", "увелич", "поднял", "rose", "rise", "rising", "gain", "gained", "increased", "up ")
        negative_words = ("сниз", "упал", "паден", "потер", "fell", "fall", "drop", "declin", "lost", "down ")

        for match in re.finditer(r"(?<![\w])([+-]?\d+(?:[.,]\d+)?)\s*%", text):
            raw = match.group(1)
            value = float(raw.replace(",", "."))
            context = text[max(0, match.start()-55): min(len(text), match.end()+55)].lower()

            # If the narration names a rolling window, the number must come from that exact field.
            # Check longer windows first so "24 часа" is never mistaken for a generic one-hour phrase.
            if re.search(r"(?:\b24\s*h\b|\b24\s*hours?\b|24\s*час|сут(?:ки|ок)|за\s+(?:день|24))", context):
                allowed = [values["change_24h"]] if "change_24h" in values else []
            elif re.search(r"(?:\b7\s*d\b|\b7\s*days?\b|7\s*дн|недел)", context):
                allowed = [values["change_7d"]] if "change_7d" in values else []
            elif re.search(r"(?:\b1\s*h\b|\b1\s*hour\b|за\s+(?:один|1)\s+час|за\s+час\b)", context):
                allowed = [values["change_1h"]] if "change_1h" in values else []
            else:
                allowed = list(values.values())
            if not allowed:
                raise ValueError(f"market script used a percentage for an unavailable time window: {raw}%")

            semantic_sign = 0
            if any(w in context for w in positive_words):
                semantic_sign = 1
            if any(w in context for w in negative_words):
                semantic_sign = -1 if semantic_sign == 0 else semantic_sign
            explicit_sign = 1 if raw.startswith("+") else (-1 if raw.startswith("-") else 0)
            claim_sign = explicit_sign or semantic_sign

            matches = []
            for actual in allowed:
                tol = max(0.16, abs(actual) * 0.012)
                magnitude_ok = abs(abs(value) - abs(actual)) <= tol
                direction_ok = claim_sign == 0 or (actual > 0 and claim_sign > 0) or (actual < 0 and claim_sign < 0) or actual == 0
                if magnitude_ok and direction_ok:
                    matches.append(actual)
            if not matches:
                raise ValueError(f"market script introduced unsupported percentage/window/direction: {raw}%")

    @staticmethod
    def _validate_market_money_claims(text: str, topic: Topic) -> None:
        fields: dict[str, float] = {}
        for key in ("price", "high_24h", "low_24h", "volume_24h", "market_cap"):
            try:
                value = float(topic.data[key])
                if value > 0:
                    fields[key] = value
            except (KeyError, TypeError, ValueError):
                pass
        if not fields:
            return

        # Only dollar-prefixed figures are treated as monetary factual claims.
        # Supports forms such as $105,250, $2.4B and $0.0042 and checks nearby
        # semantic labels so high/low/volume values cannot silently be swapped.
        pattern = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([KkMmBb]?)")
        for match in pattern.finditer(text):
            raw, suffix = match.group(1), match.group(2)
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(suffix.lower(), 1.0)
            claim = value * mult
            before = text[max(0, match.start()-55): match.start()].lower()
            after = text[match.end(): min(len(text), match.end()+35)].lower()
            labels = {
                "high_24h": ("high", "maximum", "максим"),
                "low_24h": ("low", "minimum", "миним"),
                "volume_24h": ("volume", "объем", "объём"),
                "market_cap": ("market cap", "capitalization", "капитализац"),
                "price": ("price", "цена", "торгуется", "стоит"),
            }
            nearest: tuple[int, str] | None = None
            for key, words in labels.items():
                pos = max((before.rfind(word) for word in words), default=-1)
                if pos >= 0 and (nearest is None or pos > nearest[0]):
                    nearest = (pos, key)
            if nearest is not None:
                keys = [nearest[1]]
            else:
                # Less common postfix labels such as "$2.4B volume".
                postfix = next((key for key, words in labels.items() if any(word in after for word in words)), None)
                keys = [postfix] if postfix else list(fields)
            allowed = [fields[k] for k in keys if k in fields]
            if not allowed or not any(abs(claim - actual) <= max(0.02 * abs(actual), 0.01) for actual in allowed):
                raise ValueError(f"market script introduced unsupported/mislabelled dollar value: ${raw}{suffix}")

    def _score(self, s: Script, recent_hooks: list[str]) -> float:
        score = 70.0
        hook_words = len(s.hook.split())
        if 4 <= hook_words <= 12:
            score += 8
        else:
            score -= min(10, abs(hook_words - 8))
        novelty = max_similarity(s.hook, recent_hooks)
        score -= novelty * 35
        word_count = len(s.narration.split())
        target = self.cfg.target_seconds * (150 if self.cfg.language == "ru" else 160) / 60
        score -= min(14, abs(word_count - target) / max(target, 1) * 25)
        if len(s.title) <= 70:
            score += 5
        if len({x.visual_query.lower() for x in s.scenes}) == len(s.scenes):
            score += 4
        overlays = [x.overlay_text.lower() for x in s.scenes if x.overlay_text]
        if len(set(overlays)) >= 4:
            score += 3
        cta = s.scenes[-1].voiceover.lower()
        cta_tokens = [self.cfg.exchange_name.lower(), "первая ссылка", "профил", "first link", "profile"]
        if sum(1 for x in cta_tokens if x and x in cta) >= 2:
            score += 5
        return max(0.0, min(100.0, score))
