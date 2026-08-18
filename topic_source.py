from __future__ import annotations

import math
import random
import time
from typing import Any
from datetime import datetime, timezone

import requests

from config import Settings
from models import Topic
from utils import LOG, format_money, request_with_retry, stable_id

# Subjects are intentionally broad enough to support many editorial angles without making up current news.
EVERGREEN_RU = [
    ("Почему плечо ускоряет ликвидацию", "leverage liquidation crypto trading screen"),
    ("Что funding rate говорит о рынке фьючерсов", "crypto perpetual futures funding trading"),
    ("Почему рыночный ордер иногда исполняется хуже цены на экране", "crypto order book market order"),
    ("Лимитный и рыночный ордер: где новички путаются", "limit order market order exchange app"),
    ("Что такое проскальзывание и когда оно становится заметным", "crypto liquidity order book trading"),
    ("Почему одна цена монеты ничего не говорит о её размере", "crypto market cap coins visualization"),
    ("Капитализация и FDV: в чём разница", "token market cap crypto data"),
    ("Почему объём важен при чтении движения цены", "crypto volume candlestick chart"),
    ("Что на самом деле показывает волатильность", "bitcoin volatility trading chart"),
    ("Почему стоп-лосс не гарантирует точную цену выхода", "stop loss crypto trading risk"),
    ("Что такое ликвидность и почему она важнее красивого графика", "crypto liquidity trading terminal"),
    ("Что значит spread между покупкой и продажей", "bid ask spread order book"),
    ("Почему ликвидация отличается от обычного стопа", "crypto futures liquidation chart"),
    ("Изолированная и кросс-маржа: простая разница", "margin trading crypto exchange"),
    ("Почему PnL в процентах может вводить новичка в заблуждение", "crypto pnl trading interface"),
    ("Что такое open interest и чего он не доказывает", "open interest crypto futures chart"),
    ("Почему рост цены без объёма стоит читать осторожно", "low volume crypto chart"),
    ("Три вещи, которые проверить перед первым фьючерсным ордером", "crypto futures risk management phone"),
    ("Почему усреднение не отменяет риск", "crypto portfolio risk chart"),
    ("Что такое maker и taker комиссия", "crypto exchange fees order book"),
    ("Почему комиссии важны при частых сделках", "crypto trading fees calculator"),
    ("Как работает take-profit без обещаний прибыли", "take profit crypto order screen"),
    ("Что такое trailing stop простыми словами", "trailing stop trading chart"),
    ("Почему высокая доходность обычно означает высокий риск", "crypto yield risk concept"),
    ("Стейблкоин — это не то же самое, что наличные", "stablecoin crypto wallet phone"),
    ("Почему хранение на бирже и в личном кошельке — разные модели риска", "crypto wallet exchange security"),
    ("Что такое seed-фраза и почему её нельзя никому отправлять", "crypto wallet seed phrase security"),
    ("Почему 2FA лучше одного пароля", "two factor authentication crypto security"),
    ("Как распознать фишинговую страницу криптобиржи", "cybersecurity phishing smartphone crypto"),
    ("Почему обещание гарантированной прибыли — красный флаг", "crypto scam warning smartphone"),
]

EVERGREEN_EN = [
    ("Why leverage accelerates liquidation", "leverage liquidation crypto trading screen"),
    ("What funding rate actually tells you", "crypto perpetual futures funding trading"),
    ("Why market orders can fill worse than the screen price", "crypto order book market order"),
    ("Limit vs market orders: the beginner trap", "limit order market order exchange app"),
    ("What slippage is and when it matters", "crypto liquidity order book trading"),
    ("Why a coin's unit price says little about its size", "crypto market cap coins visualization"),
    ("Market cap vs FDV in plain English", "token market cap crypto data"),
    ("Why volume matters when reading price moves", "crypto volume candlestick chart"),
    ("What volatility actually measures", "bitcoin volatility trading chart"),
    ("Why a stop-loss cannot guarantee an exact exit price", "stop loss crypto trading risk"),
    ("Liquidity matters more than a pretty chart", "crypto liquidity trading terminal"),
    ("Bid-ask spread explained fast", "bid ask spread order book"),
    ("Liquidation vs a normal stop", "crypto futures liquidation chart"),
    ("Isolated vs cross margin", "margin trading crypto exchange"),
    ("Why percentage PnL can mislead beginners", "crypto pnl trading interface"),
    ("Open interest: what it says and what it doesn't", "open interest crypto futures chart"),
    ("Why price rising without volume deserves caution", "low volume crypto chart"),
    ("Three checks before your first futures order", "crypto futures risk management phone"),
    ("Why averaging down does not remove risk", "crypto portfolio risk chart"),
    ("Maker vs taker fees", "crypto exchange fees order book"),
    ("Why fees matter for frequent trading", "crypto trading fees calculator"),
    ("How take-profit orders work", "take profit crypto order screen"),
    ("Trailing stops in plain English", "trailing stop trading chart"),
    ("Why high yield usually means high risk", "crypto yield risk concept"),
    ("A stablecoin is not the same as cash", "stablecoin crypto wallet phone"),
    ("Exchange custody vs self-custody risk", "crypto wallet exchange security"),
    ("What a seed phrase is and why it stays private", "crypto wallet seed phrase security"),
    ("Why 2FA beats a password alone", "two factor authentication crypto security"),
    ("How to spot a crypto phishing page", "cybersecurity phishing smartphone crypto"),
    ("Guaranteed profit is a red flag", "crypto scam warning smartphone"),
]

# Curated mechanics briefs. Both RU and EN topic lists use the same concept order.
# These constrain the LLM to a small factual surface instead of asking it to invent
# the lesson from a title alone. Venue-specific formulas/rules must still be described
# as venue-specific.
EVERGREEN_GUIDES = [
    "Leverage magnifies PnL relative to posted margin; liquidation can occur when margin no longer satisfies maintenance requirements. Exact liquidation rules and prices are venue-specific.",
    "Perpetual funding is a periodic transfer mechanism between long and short positions; sign, interval and formula depend on the venue. Funding is not a prediction of future price.",
    "A market order prioritizes execution over price and consumes available liquidity, so average fill can differ from the displayed price, especially in thin or fast markets.",
    "A limit order sets a price constraint but is not guaranteed to fill. A market order prioritizes execution but exposes the trader to slippage.",
    "Slippage is the difference between an expected/reference price and actual execution; it tends to matter more when liquidity is thin, order size is large or markets move quickly.",
    "Token unit price alone does not measure network/token size. Market capitalization is roughly circulating supply multiplied by unit price and has its own limitations.",
    "Market cap uses circulating supply; fully diluted valuation commonly uses a broader/max supply assumption. A large gap can indicate future dilution risk but is not a standalone valuation verdict.",
    "Trading volume measures activity over a period. A price move with weak volume can have different context from one accompanied by strong activity, but volume alone does not prove direction will continue.",
    "Volatility describes the magnitude/dispersion of price changes, not whether price will go up or down. Higher volatility generally means wider possible short-term outcomes.",
    "A stop-loss becomes an order when triggered; exact execution can differ from the stop level because of gaps/slippage. Stop mechanics vary by order type and venue.",
    "Liquidity is the ability to trade size with limited price impact. Order-book depth and spread can matter even when a chart looks attractive.",
    "Bid is the highest displayed buy price and ask is the lowest displayed sell price; their difference is the spread. Spreads often widen when liquidity is weaker or volatility jumps.",
    "Liquidation is venue-driven forced position reduction/closure when margin requirements fail; a normal stop is a user-defined order trigger and is not the same mechanism.",
    "Isolated margin confines assigned margin to a position; cross margin may share available account collateral across positions. Exact behavior and risk controls are venue-specific.",
    "Percentage PnL depends on the denominator and leverage/margin conventions. A large percentage return on margin can coexist with a much smaller underlying asset move and substantial risk.",
    "Open interest is the amount/value of outstanding derivatives positions. Rising or falling OI shows participation/positioning changes but does not by itself identify bullish or bearish direction.",
    "Price and volume are separate observations. A rising price on low activity is not automatically invalid, but it provides less evidence of broad participation than a move with stronger volume.",
    "Before a futures order, understand leverage, liquidation/margin rules, fees/funding and a maximum-loss plan. Exact contract rules differ by venue.",
    "Averaging down changes the average entry price but increases exposure/capital at risk; it does not remove the possibility that price continues against the position.",
    "Maker orders add resting liquidity when they do not execute immediately; taker orders remove available liquidity. Fee schedules and classification details depend on the venue.",
    "Frequent trading compounds explicit fees and implicit costs such as spread/slippage. Small per-trade costs can materially change net results over many trades.",
    "A take-profit is an order instruction intended to reduce/close exposure after a trigger/price condition. It does not guarantee profit or exact execution in all market conditions.",
    "A trailing stop moves its trigger reference as price moves favorably according to configured rules, then can trigger on a reversal. Exact implementation varies by venue.",
    "Higher advertised yield generally comes with additional sources of risk such as market, credit/counterparty, liquidity, smart-contract or incentive-token risk; yield is not risk-free.",
    "Stablecoins aim to track a reference asset but can carry issuer/reserve, market, redemption, smart-contract and de-pegging risks. They are not identical to insured bank cash.",
    "Exchange custody delegates key control to a custodian and adds counterparty/platform risk; self-custody gives the user key responsibility and adds operational/key-loss risk.",
    "A seed/recovery phrase can restore control of a wallet. Anyone who obtains it may be able to control the wallet, so it should not be shared or entered into untrusted sites.",
    "Two-factor authentication adds a second authentication factor beyond a password. App/hardware-based factors can reduce risk from stolen passwords, though no control eliminates account risk.",
    "Phishing pages imitate trusted services to steal credentials or approvals. Verify domains, avoid unsolicited links, use bookmarks and treat unexpected wallet/sign-in requests cautiously.",
    "Guaranteed-return language is a scam/red-flag pattern because market outcomes are uncertain. Verify counterparties, withdrawal conditions, domains and permissions before sending funds or credentials.",
]

FORMATS = ["counterintuitive", "mistake", "myth_vs_fact", "explainer", "three_checks", "comparison"]
STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDS", "FRAX", "USD1", "EURC"}


class TopicSource:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "CryptoShortsBot/2.1"})
        self._market_cache: list[Topic] | None = None
        self._market_cache_at = 0.0

    def get_candidates(self) -> list[Topic]:
        topics: list[Topic] = []
        if self.cfg.topic_mode in {"market", "mixed"}:
            try:
                topics.extend(self._market_topics())
            except Exception as exc:
                LOG.warning("Market data unavailable: %s", exc)
        if self.cfg.topic_mode in {"evergreen", "mixed"} or not topics:
            topics.extend(self._evergreen_topics())
        random.shuffle(topics)
        return topics

    def _market_topics(self) -> list[Topic]:
        if self._market_cache is not None and time.monotonic() - self._market_cache_at < 300:
            return list(self._market_cache)
        if not self.cfg.coingecko_api_key:
            raise RuntimeError("COINGECKO_API_KEY is not configured")
        resp = request_with_retry(
            self.session,
            "GET",
            "https://api.coingecko.com/api/v3/coins/markets",
            headers={"x-cg-demo-api-key": self.cfg.coingecko_api_key},
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 100,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "1h,24h,7d",
            },
        )
        rows: list[dict[str, Any]] = resp.json()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for c in rows:
            symbol = str(c.get("symbol") or "").upper()
            rank = int(c.get("market_cap_rank") or 9999)
            ch24 = c.get("price_change_percentage_24h")
            vol = float(c.get("total_volume") or 0)
            mcap = float(c.get("market_cap") or 0)
            if symbol in STABLE_SYMBOLS or rank > 120 or ch24 is None or vol <= 0 or mcap <= 0:
                continue
            ch1 = float(c.get("price_change_percentage_1h_in_currency") or 0)
            ch24f = float(ch24)
            ch7 = float(c.get("price_change_percentage_7d_in_currency") or 0)
            liquidity = min(1.0, vol / max(mcap, 1.0))
            # Skip sleepy market rows; evergreen education is stronger than manufacturing excitement from noise.
            if abs(ch24f) < 1.5 and abs(ch1) < 0.75 and abs(ch7) < 4.0:
                continue
            score = abs(ch24f) + abs(ch1) * 0.8 + min(abs(ch7), 30) * 0.15 + liquidity * 8
            if math.isfinite(score):
                ranked.append((score, c))
        ranked.sort(key=lambda x: x[0], reverse=True)

        out: list[Topic] = []
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for _, c in ranked[:14]:
            name = str(c.get("name") or c.get("symbol") or "Asset")
            symbol = str(c.get("symbol") or "").upper()
            ch1 = float(c.get("price_change_percentage_1h_in_currency") or 0)
            ch24 = float(c.get("price_change_percentage_24h") or 0)
            ch7 = float(c.get("price_change_percentage_7d_in_currency") or 0)
            data = {
                "id": c.get("id"), "name": name, "symbol": symbol,
                "price": c.get("current_price"), "change_1h": ch1, "change_24h": ch24,
                "change_7d": ch7, "high_24h": c.get("high_24h"), "low_24h": c.get("low_24h"),
                "volume_24h": c.get("total_volume"), "market_cap": c.get("market_cap"),
                "market_cap_rank": c.get("market_cap_rank"), "retrieved_at": retrieved_at,
            }
            facts = (
                f"{name} ({symbol}); price {format_money(data['price'])}; 1h {ch1:+.2f}%; 24h {ch24:+.2f}%; "
                f"7d {ch7:+.2f}%; 24h high {format_money(data['high_24h'])}; low {format_money(data['low_24h'])}; "
                f"24h volume {format_money(data['volume_24h'])}; market cap {format_money(data['market_cap'])}; "
                f"market-cap rank {data['market_cap_rank']}."
            )
            if self.cfg.language == "ru":
                direction = "вырос" if ch24 >= 0 else "снизился"
                title = f"{name} {direction} на {abs(ch24):.1f}%: что видно из цифр"
                context = f"Данные CoinGecko получены {retrieved_at} UTC: " + facts + " Не выдумывай причины движения или новости."
            else:
                direction = "rose" if ch24 >= 0 else "fell"
                title = f"{name} {direction} {abs(ch24):.1f}%: what the numbers show"
                context = f"CoinGecko data retrieved {retrieved_at} UTC: " + facts + " Do not invent catalysts or news."
            bucket = int(round(ch24 / 2.0))
            out.append(Topic(
                title=title,
                context=context,
                visual_hint=f"{name} {symbol} cryptocurrency trading chart phone",
                fingerprint=stable_id(f"market:{c.get('id')}:{bucket}"),
                source="CoinGecko /coins/markets",
                format_hint=random.choice(["number_breakdown", "counterintuitive", "explainer"]),
                kind="market",
                data=data,
            ))
        self._market_cache = list(out)
        self._market_cache_at = time.monotonic()
        return out

    def _evergreen_topics(self) -> list[Topic]:
        rows = EVERGREEN_RU if self.cfg.language == "ru" else EVERGREEN_EN
        topics: list[Topic] = []
        if len(rows) != len(EVERGREEN_GUIDES):
            raise RuntimeError("Evergreen topic/brief table length mismatch")
        for idx, (title, visual) in enumerate(rows):
            fmt = random.choice(FORMATS)
            context = (
                "Curated evergreen crypto-literacy brief: " + EVERGREEN_GUIDES[idx] + " "
                "Explain only these mechanics accurately and beginner-friendly. Do not add invented statistics, "
                "venue-specific formulas, personalized investment advice, or guaranteed outcomes."
            )
            topics.append(Topic(
                title=title,
                context=context,
                visual_hint=visual,
                fingerprint=stable_id("evergreen:" + title),
                source="curated evergreen mechanics brief",
                format_hint=fmt,
                kind="evergreen",
            ))
        return topics
