from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config import Settings
from models import Topic
from utils import LOG, request_with_retry, stable_id

BINANCE_MARKET_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"
STABLE_BASES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USDP", "PYUSD", "USDS", "USD1", "FRAX", "EURC"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
FORMATS = ["counterintuitive", "mistake", "myth_vs_fact", "explainer", "three_checks", "comparison"]
POPULAR_BASES = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "SUI", "LINK", "AVAX", "DOT",
    "LTC", "BCH", "XLM", "HBAR", "UNI", "ETC", "NEAR", "APT", "ARB", "OP", "ATOM", "FIL",
    "INJ", "AAVE", "MKR", "RUNE", "PEPE", "SHIB", "WIF", "FET", "RENDER", "TAO", "SEI", "TIA"
}

EVERGREEN = [
    ("Почему плечо ускоряет ликвидацию", "Why leverage accelerates liquidation", "leverage liquidation crypto trading screen", "Leverage magnifies PnL relative to posted margin; liquidation can occur when margin no longer satisfies maintenance requirements. Exact liquidation rules are venue-specific."),
    ("Что funding rate говорит о фьючерсах", "What funding rate actually tells you", "crypto perpetual futures funding trading", "Perpetual funding is a periodic transfer mechanism between long and short positions. Its sign, interval and formula are venue-specific; it is not a price prediction."),
    ("Почему market order может исполниться хуже цены на экране", "Why market orders can fill worse than the screen price", "crypto order book market order", "A market order prioritizes execution over price and consumes available liquidity, so average fill can differ from the displayed price."),
    ("Limit и market order: главная разница", "Limit vs market orders: the key difference", "limit order market order exchange app", "A limit order sets a price constraint but may not fill. A market order prioritizes execution and can experience slippage."),
    ("Что такое проскальзывание", "What slippage is and when it matters", "crypto liquidity order book trading", "Slippage is the difference between an expected/reference price and actual execution; it tends to matter more with thin liquidity, large orders or fast markets."),
    ("Почему объём важен при движении цены", "Why volume matters when price moves", "crypto volume candlestick chart", "Trading volume measures activity over a period. It adds context to price movement but does not prove the move will continue."),
    ("Что на самом деле показывает волатильность", "What volatility actually measures", "bitcoin volatility trading chart", "Volatility describes the magnitude or dispersion of price changes, not direction. Higher volatility generally means a wider range of possible short-term outcomes."),
    ("Почему stop-loss не гарантирует точную цену", "Why a stop-loss cannot guarantee an exact exit", "stop loss crypto trading risk", "A stop becomes an order when triggered; gaps and slippage can cause execution away from the stop level. Exact mechanics vary by order type and venue."),
    ("Что такое ликвидность простыми словами", "Liquidity explained in plain English", "crypto liquidity trading terminal", "Liquidity is the ability to trade size with limited price impact. Order-book depth and spread both matter."),
    ("Что означает spread между bid и ask", "What the bid-ask spread means", "bid ask spread order book", "Bid is the highest displayed buy price and ask is the lowest displayed sell price; their difference is the spread."),
    ("Ликвидация и обычный stop — не одно и то же", "Liquidation and a normal stop are different", "crypto futures liquidation chart", "Liquidation is venue-driven forced position reduction or closure when margin requirements fail; a normal stop is a user-defined order trigger."),
    ("Изолированная и cross-маржа: простая разница", "Isolated vs cross margin", "margin trading crypto exchange", "Isolated margin confines assigned margin to a position; cross margin may share available account collateral. Exact rules are venue-specific."),
    ("Что такое open interest и чего он не доказывает", "Open interest: what it says and what it doesn't", "open interest crypto futures chart", "Open interest is outstanding derivatives positioning. Changes show participation or positioning changes but do not by themselves identify bullish or bearish direction."),
    ("Почему комиссии важны при частых сделках", "Why fees matter for frequent trading", "crypto trading fees calculator", "Frequent trading compounds explicit fees and implicit costs such as spread and slippage; small per-trade costs can materially affect net results."),
    ("Maker и taker комиссия: в чём разница", "Maker vs taker fees", "crypto exchange fees order book", "Maker orders add resting liquidity when they do not execute immediately; taker orders remove available liquidity. Fee schedules depend on the venue."),
    ("Почему усреднение не убирает риск", "Why averaging down does not remove risk", "crypto portfolio risk chart", "Averaging down changes the average entry price but increases exposure and capital at risk; it does not prevent price from continuing against the position."),
    ("Seed-фраза: почему её нельзя никому отправлять", "Why a seed phrase must stay private", "crypto wallet seed phrase security", "A recovery phrase can restore control of a wallet. Anyone who obtains it may be able to control the wallet, so it must stay private."),
    ("Почему 2FA лучше одного пароля", "Why 2FA beats a password alone", "two factor authentication crypto security", "Two-factor authentication adds a second factor beyond a password and reduces risk from password compromise, though no control removes all account risk."),
    ("Как распознать фишинговую страницу биржи", "How to spot a crypto phishing page", "cybersecurity phishing smartphone crypto", "Phishing pages imitate trusted services to steal credentials or approvals. Verify domains and treat unsolicited links and unexpected sign-in requests cautiously."),
    ("Почему гарантированная прибыль — красный флаг", "Why guaranteed profit is a red flag", "crypto scam warning smartphone", "Guaranteed-return language is a warning sign because market outcomes are uncertain. Verify counterparties, domains, withdrawal conditions and permissions."),
]


def _fmt_usdt(value: Any, compact: bool = False) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    av = abs(v)
    if compact and av >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B USDT"
    if compact and av >= 1_000_000:
        return f"{v / 1_000_000:.2f}M USDT"
    if av >= 1_000:
        return f"{v:,.0f} USDT"
    if av >= 1:
        return f"{v:,.2f} USDT"
    if av >= 0.01:
        return f"{v:.4f} USDT"
    return f"{v:.8f}".rstrip("0").rstrip(".") + " USDT"


class TopicSource:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "CryptoShortsBot/2.4"})
        self._market_cache: list[Topic] | None = None
        self._market_cache_at = 0.0

    def get_candidates(self) -> list[Topic]:
        market: list[Topic] = []
        evergreen: list[Topic] = []
        if self.cfg.topic_mode in {"market", "mixed"}:
            try:
                market = self._market_topics()
            except Exception as exc:
                LOG.warning("Binance market data unavailable: %s", exc)
        if self.cfg.topic_mode in {"evergreen", "mixed"} or not market:
            evergreen = self._evergreen_topics()
            random.shuffle(evergreen)
        return market + evergreen

    def _market_topics(self) -> list[Topic]:
        if self._market_cache is not None and time.monotonic() - self._market_cache_at < 300:
            return list(self._market_cache)
        resp = request_with_retry(self.session, "GET", BINANCE_MARKET_URL, timeout=30)
        rows = resp.json()
        if not isinstance(rows, list):
            raise RuntimeError("Binance ticker endpoint returned an unexpected payload")
        ranked = self._rank_binance_rows(rows)
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out: list[Topic] = []
        for _, row in ranked[:14]:
            base = row["base_asset"]
            pair = row["symbol"]
            ch24 = row["change_24h"]
            day_range = max(row["high_24h"] - row["low_24h"], 0.0)
            range_pct = (day_range / row["low_24h"] * 100.0) if row["low_24h"] > 0 else 0.0
            position = ((row["price"] - row["low_24h"]) / day_range * 100.0) if day_range > 0 else 50.0
            position = max(0.0, min(100.0, position))
            data = {**row, "range_24h_pct": range_pct, "position_in_24h_range_pct": position, "retrieved_at": retrieved_at}
            facts = (
                f"{base} ({pair}); last price {_fmt_usdt(row['price'])}; 24h change {ch24:+.2f}%; "
                f"24h high {_fmt_usdt(row['high_24h'])}; low {_fmt_usdt(row['low_24h'])}; "
                f"24h range {range_pct:.2f}%; current price sits {position:.1f}% of the way from the 24h low to the 24h high; "
                f"24h quote volume {_fmt_usdt(row['volume_24h'], compact=True)}; "
                f"weighted average {_fmt_usdt(row['weighted_avg_24h'])}; trades {row['trades_24h']:,}."
            )
            if self.cfg.language == "ru":
                direction = "вырос" if ch24 >= 0 else "снизился"
                title = f"{base} {direction} на {abs(ch24):.1f}% за 24 часа: что видно из Binance"
                context = (
                    f"Публичные данные Binance Spot получены {retrieved_at} UTC: {facts} "
                    "Все ценовые и объёмные значения номинированы в USDT. Это скользящее 24-часовое окно. "
                    "Не выдумывай причины движения, новости, прогнозы или уровни."
                )
            else:
                direction = "rose" if ch24 >= 0 else "fell"
                title = f"{base} {direction} {abs(ch24):.1f}% in 24h: what Binance data shows"
                context = (
                    f"Public Binance Spot data retrieved {retrieved_at} UTC: {facts} "
                    "All price and volume values are denominated in USDT. This is a rolling 24-hour window. "
                    "Do not invent catalysts, news, forecasts or support/resistance levels."
                )
            out.append(Topic(
                title=title,
                context=context,
                visual_hint=f"{base} cryptocurrency trading chart phone",
                fingerprint=stable_id(f"binance:{pair}:{round(ch24 / 2)}"),
                source="Binance Spot public market data /api/v3/ticker/24hr",
                format_hint=random.choice(["number_breakdown", "counterintuitive", "explainer"]),
                kind="market",
                data=data,
            ))
        self._market_cache = list(out)
        self._market_cache_at = time.monotonic()
        return out

    @staticmethod
    def _rank_binance_rows(rows: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
        parsed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pair = str(row.get("symbol") or "").upper()
            if not pair.endswith("USDT") or len(pair) <= 4:
                continue
            base = pair[:-4]
            if base in STABLE_BASES or base.endswith(LEVERAGED_SUFFIXES):
                continue
            try:
                price = float(row.get("lastPrice") or 0)
                change = float(row.get("priceChangePercent") or 0)
                high = float(row.get("highPrice") or 0)
                low = float(row.get("lowPrice") or 0)
                volume = float(row.get("quoteVolume") or 0)
                weighted = float(row.get("weightedAvgPrice") or 0)
                trades = int(row.get("count") or 0)
            except (TypeError, ValueError):
                continue
            vals = (price, change, high, low, volume, weighted)
            if not all(math.isfinite(v) for v in vals):
                continue
            if min(price, high, low, volume, weighted) <= 0 or trades <= 0:
                continue
            if volume < 25_000_000 or abs(change) < 1.0:
                continue
            parsed.append({
                "symbol": pair, "base_asset": base, "quote_asset": "USDT",
                "price": price, "change_24h": change, "high_24h": high, "low_24h": low,
                "volume_24h": volume, "weighted_avg_24h": weighted, "trades_24h": trades,
            })
        popular = [x for x in parsed if x["base_asset"] in POPULAR_BASES]
        pool = popular if len(popular) >= 5 else [x for x in parsed if x["volume_24h"] >= 100_000_000]
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in pool:
            volume = item["volume_24h"]
            trades = item["trades_24h"]
            change = abs(item["change_24h"])
            move_score = min(change, 18.0)
            liquidity = max(0.0, min(5.0, math.log10(volume) - 7.0))
            activity = max(0.0, min(3.0, math.log10(max(trades, 1)) - 4.0))
            score = move_score * 1.4 + liquidity * 4.0 + activity * 2.0
            ranked.append((score, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked

    def _evergreen_topics(self) -> list[Topic]:
        topics: list[Topic] = []
        ru = self.cfg.language == "ru"
        for ru_title, en_title, visual, brief in EVERGREEN:
            title = ru_title if ru else en_title
            topics.append(Topic(
                title=title,
                context=(
                    "Curated evergreen crypto-literacy brief: " + brief + " "
                    "Explain only these mechanics accurately and beginner-friendly. Do not invent statistics, "
                    "venue-specific formulas, personalized investment advice, or guaranteed outcomes."
                ),
                visual_hint=visual,
                fingerprint=stable_id("evergreen:" + title),
                source="curated evergreen mechanics brief",
                format_hint=random.choice(FORMATS),
                kind="evergreen",
            ))
        return topics
