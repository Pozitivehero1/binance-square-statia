import pandas as pd
import ta

def build_indicators(raw):

    df = pd.DataFrame(raw)

    close = df[4].astype(float)
    high = df[2].astype(float)
    low = df[3].astype(float)

    rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]

    change = (close.iloc[-1] - close.iloc[-10]) / close.iloc[-10] * 100

    return {
        "price": float(close.iloc[-1]),
        "rsi": float(rsi),
        "ema20": float(ema20),
        "ema50": float(ema50),
        "change": float(change)
    }
