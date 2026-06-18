import requests

# Базовый URL зеркала Binance (доступно из любого региона)
BINANCE_API = "https://data-api.binance.vision"

# Глобальный кэш для exchangeInfo
_EXCHANGE_INFO = None

def get_base_asset(symbol):
    """
    Возвращает официальный базовый актив (короткий тикер) для символа.
    Например: 'BTCUSDT' → 'BTC', 'PHBUSDT' → 'PHB'.
    """
    global _EXCHANGE_INFO
    if _EXCHANGE_INFO is None:
        try:
            url = f"{BINANCE_API}/api/v3/exchangeInfo"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            data = r.json()
            _EXCHANGE_INFO = {
                s["symbol"]: s["baseAsset"]
                for s in data.get("symbols", [])
                if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
            }
        except Exception as e:
            print(f"[WARN] Не удалось загрузить exchangeInfo: {e}")
            _EXCHANGE_INFO = {}

    return _EXCHANGE_INFO.get(symbol, symbol.replace("USDT", ""))

def get_trending_symbols(limit=100):
    """Возвращает топ-N USDT-пар по объёму и изменению за 24ч."""
    try:
        url = f"{BINANCE_API}/api/v3/ticker/24hr"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[ERROR] Не удалось получить тикеры: {e}")
        return []

    if not isinstance(data, list):
        print(f"[ERROR] Неожиданный формат ответа: {data}")
        return []

    pairs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        if not symbol or not symbol.endswith("USDT"):
            continue
        try:
            volume = float(item.get("quoteVolume", 0))
            change = float(item.get("priceChangePercent", 0))
            pairs.append({"symbol": symbol, "volume": volume, "change": change})
        except (TypeError, ValueError):
            continue

    pairs.sort(key=lambda x: (abs(x["change"]), x["volume"]), reverse=True)
    return [x["symbol"] for x in pairs[:limit]]
