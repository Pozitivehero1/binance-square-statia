import requests
import pandas as pd

BINANCE_API = "https://data-api.binance.vision"

def binance_spot(symbol):
    try:
        url = f"{BINANCE_API}/api/v3/klines?symbol={symbol}&interval=15m&limit=200"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        if not isinstance(data, list):
            return None
        return data
    except:
        return None

def bybit(symbol):
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=15"
        r = requests.get(url, timeout=10)
        data = r.json()
        if "result" not in data:
            return None
        return data["result"]["list"]
    except:
        return None

def coingecko(symbol):
    # Резерв – пока не реализован
    return None

def get_data(symbol):
    sources = [binance_spot, bybit, coingecko]
    for source in sources:
        data = source(symbol)
        if data:
            return data
    return None
