import requests
import os

MISTRAL_API = os.getenv("MISTRAL_API")

def write_post(data):
    prompt = f"""
Ты опытный криптоаналитик Binance Square.

Монета: {data['basic']} (тикер пары: {data['symbol']})
Цена: {data['price']}
Изменение за последние 10 свечей: {round(data['change'], 2)}%
RSI: {round(data['rsi'], 2)}
EMA20: {round(data['ema20'], 2)}
EMA50: {round(data['ema50'], 2)}

Напиши аналитический пост на русском языке строго по следующей структуре:

Что движет ценой, технические уровни, настроение).
Вход (цена входа, причина, по какой стратегии входим).
TP1 (Take Profit 1) – ближайшая цель.
TP2 (Take Profit 2) – средняя цель.
TP3 (Take Profit 3) – максимальная цель (оптимистичный сценарий).
Стоп-лосс (уровень, при котором выходим в минус).
Вывод – итоговый вердикт, стоит ли торговать или наблюдать.

Требования:
- Начни с броского заголовка (1 строка, с эмодзи).
- Задай вопрос аудитории в конце поста.
- Остальные требования (структура, длина) сохраняются.

- Все упоминания монеты делай строго в формате **${data['basic']}** (например, $BTC, $ETH) – это обязательно!
- Не используй полное имя с USDT (например, BTCUSDT) – только $BTC.
- Не добавляй хештеги (#).
- Не используй звёздочки, жирный шрифт, курсив.
- Длина 500–700 символов.используй смайлы, где уместно
- Стиль живого автора, не робота.
- Не пиши "не является финансовой рекомендацией".
"""

    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {MISTRAL_API}",
            "Content-Type": "application/json"
        },
        json={
            "model": "mistral-small",
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=60
    )

    response = r.json()
    text = response["choices"][0]["message"]["content"]

    # Очистка от случайного форматирования
    for ch in ['*', '_', '`', '#']:
        text = text.replace(ch, '')

    return text
