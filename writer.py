import requests
import os

MISTRAL_API = os.getenv("MISTRAL_API")

def write_post(data):
    prompt = f"""
Ты опытный автор Binance Square.

Ты пишешь статьи, которые получают комментарии,
сохранения и подписки.

Твоя задача:

Не перечислять индикаторы.

Не писать сухой технический анализ.

Объяснять происходящее на рынке человеческим языком.

ДАННЫЕ

Монета: {data['basic']}
Пара: {data['symbol']}

Цена:
{round(data['price'], 6)}

Изменение:
{round(data['change'], 2)}%

RSI:
{round(data['rsi'], 2)}

EMA20:
{round(data['ema20'], 6)}

EMA50:
{round(data['ema50'], 6)}

EMA200:
{round(data['ema200'], 6)}

Volume Ratio:
{round(data['volume_ratio'], 2)}

Trend Strength:
{round(data['trend_strength'], 2)}

ATR:
{round(data['atr_percent'], 2)}%

ТРЕБОВАНИЯ

- пиши как живой аналитик
- не используй списки кроме блока уровней
- не используй эмодзи
- перед упоминанием монеты ставь знак $, не больше 3 упоминаний в тексте (например $BTC)
- не разделяй блоки черточками
- не используй хештеги
- не используй слова:
  возможно
  вероятно
  скорее всего
  может быть

- не упоминай индикаторы подряд

Вместо:

RSI = 65

Пиши:

Покупатели продолжают удерживать инициативу,
а импульс остается на стороне быков.

СТРУКТУРА

ЗАГОЛОВОК

Краткое резюме

Что сейчас происходит

Почему рынок обращает внимание на актив

Ключевые уровни

Бычий сценарий

Медвежий сценарий

Вывод

Вопрос аудитории

Размер статьи:

1200-1800 символов

Верни готовую статью в виде:
ЗАГОЛОВОК: [твой заголовок]
ТЕКСТ: [текст статьи]
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
    content = response["choices"][0]["message"]["content"]
    
    # Парсим заголовок и текст
    title = ""
    text = content
    if "ЗАГОЛОВОК:" in content:
        parts = content.split("ЗАГОЛОВОК:", 1)
        if len(parts) > 1:
            title_part = parts[1].split("ТЕКСТ:", 1)
            if len(title_part) > 1:
                title = title_part[0].strip()
                text = title_part[1].strip()
            else:
                title = title_part[0].strip()
    
    # Очистка от случайного форматирования
    for ch in ['*', '_', '`', '#']:
        text = text.replace(ch, '')
        title = title.replace(ch, '')

    return title, text
