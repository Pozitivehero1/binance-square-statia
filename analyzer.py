import requests
import pandas as pd
import ta

def get_symbol_data(symbol):

    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=200"

    r = requests.get(url)
    data = r.json()

    if not isinstance(data, list):
        print("[SKIP]", symbol, data)
        return None

    df = pd.DataFrame(data, columns=[
        "t","o","h","l","c","v","ct","q","n","tb","tq","i"
    ])

    close = df["c"].astype(float)
    high = df["h"].astype(float)
    low = df["l"].astype(float)

    rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]

    change = (close.iloc[-1] - close.iloc[-10]) / close.iloc[-10] * 100

    atr = ta.volatility.AverageTrueRange(high, low, close).average_true_range().iloc[-1]

    return {
        "symbol": symbol,
        "price": float(close.iloc[-1]),
        "rsi": float(rsi),
        "ema20": float(ema20),
        "ema50": float(ema50),
        "change": float(change),
        "atr": float(atr)
    }
