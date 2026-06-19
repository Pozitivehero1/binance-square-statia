import requests
import os
import json

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
- не используй хештеги
- не разделяяй блоки черточками
- перед упоминанием монеты ставь знак $, но не более 3 в одном посте (например $BTC)
- не используй слова:
  возможно
  вероятно
  скорее всего
  может быть
  Запрещено придумывать:

- новости
- обновления проекта
- листинги
- партнерства
- заявления команды

Если информация не была передана во входных данных,
не упоминай её.

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

Верни ТОЛЬКО JSON.

{
  "title": "...",
  "content": "..."
}

Никакого дополнительного текста.
Никаких markdown блоков.
Никаких комментариев.
}
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {MISTRAL_API}",
            "Content-Type": "application/json"
        },
        json={
            "model": "mistral-small",
            "temperature": 0.85,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        },
        timeout=90
    )

    response = r.json()

  content = response["choices"][0]["message"]["content"]

parsed = json.loads(content)

title = parsed["title"]
text = parsed["content"]

    if "ЗАГОЛОВОК:" in content:

        parts = content.split("ЗАГОЛОВОК:", 1)

        if len(parts) > 1:

            title_part = parts[1].split("ТЕКСТ:", 1)

            if len(title_part) > 1:
                title = title_part[0].strip()
                text = title_part[1].strip()
            else:
                title = title_part[0].strip()

    for ch in ["*", "_", "`", "#"]:
        title = title.replace(ch, "")
        text = text.replace(ch, "")

    return title, text
