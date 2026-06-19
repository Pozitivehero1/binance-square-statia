import pandas as pd
import ta


def build_indicators(raw):

    df = pd.DataFrame(
        raw,
        columns=[
            "t","o","h","l","c","v",
            "ct","q","n","tb","tq","i"
        ]
    )

    close = df["c"].astype(float)
    high = df["h"].astype(float)
    low = df["l"].astype(float)
    volume = df["v"].astype(float)

    rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]

    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]
    ema200 = close.ewm(span=200).mean().iloc[-1]

    atr = ta.volatility.AverageTrueRange(
        high,
        low,
        close
    ).average_true_range().iloc[-1]

    atr_percent = atr / close.iloc[-1] * 100

    change = (
        (close.iloc[-1] - close.iloc[-10])
        / close.iloc[-10]
        * 100
    )

    avg_volume = volume.tail(20).mean()
    current_volume = volume.iloc[-1]

    volume_ratio = current_volume / avg_volume

    trend_strength = (
        abs(ema20 - ema50)
        / ema50
        * 100
    )

    return {
        "price": float(close.iloc[-1]),
        "rsi": float(rsi),
        "ema20": float(ema20),
        "ema50": float(ema50),
        "ema200": float(ema200),
        "atr": float(atr),
        "atr_percent": float(atr_percent),
        "change": float(change),
        "volume_ratio": float(volume_ratio),
        "trend_strength": float(trend_strength)
    }
