# Crypto Shorts Bot 2.4 Production

Автоматический бот для вертикальных YouTube Shorts о крипто-механике и публичных рыночных данных Binance. Генерирует сценарий, озвучку, видеоряд/инфографику, субтитры, финальный MP4, метаданные и при необходимости загружает ролик на YouTube.

## Что нужно для запуска

Обязательные GitHub Actions Secrets:

```text
MISTRAL_API
REFERRAL_URL
```

Рекомендуемый, но необязательный Secret:

```text
PEXELS_API_KEY
```

Для рыночных тем отдельный Binance API key **не нужен**: бот использует публичные Binance Spot market-data endpoints. Если Pexels недоступен, ролик всё равно собирается на собственных локальных визуалах.

По умолчанию бренд CTA — Binance, отдельной переменной `EXCHANGE_NAME` нет.

## Что делает версия 2.4

- Mistral создаёт несколько редакционных вариантов сценария, бот валидирует их и выбирает лучший.
- Рыночный Short строится из **8–9 коротких сцен**: живой stock-видеоряд меняется примерно каждые 4–6 секунд, а точные значения выводятся отдельной кинетической типографикой поверх кадра.
- Полноэкранные «data cards» удалены. Если stock недоступен, fallback — текст-свободный кинематографичный motion-background (chart/order-flow/network), а не карточка с цифрами.
- Финальный CTA использует минимальный полноэкранный фон без имитации интерфейса приложения или большой UI-карточки.
- Рыночный ранжировщик отдаёт приоритет узнаваемым ликвидным USDT-парам; мелкая пара больше не может попасть в Short только из-за экстремального процента движения.
- Pexels проходит дополнительную relevance-проверку по человекочитаемому slug результата. Очевидно нерелевантный ролик вроде одежды/лайфстайла для trading-сцены отклоняется и заменяется контролируемым визуалом.
- Stock search-запросы автоматически очищаются от невозможных указаний вроде `split-screen`, точных цен, `highlighted`, `animation`, Binance/logo и слишком длинных описаний.
- Финальный CTA нормализуется кодом, а не доверяется модели: русская версия заканчивается фразой `Хочешь посмотреть Binance? Первая ссылка — в профиле канала.` Без обещаний низких спредов, лучших комиссий, бонусов или доходности.
- Для evergreen-тем есть локальные content-guards против типичных правдоподобных, но неточных упрощений.
- Edge TTS/ElevenLabs дают тайминги слов; пунктуация возвращается из исходного текста, а captions собираются по смысловым фразам.
- SRT/ASS captions имеют строго монотонные непересекающиеся интервалы.
- Числа вида `50 000` не разрываются между соседними субтитрами.
- Финальная длительность теперь определяется голосом: видеоряд при необходимости дотягивается последним кадром, аудио дополняется тишиной, затем оба потока жёстко trim'ятся к одной длительности. `-shortest` больше не может обрезать конец озвучки.
- FFprobe QA сверяет фактическую длительность с ожидаемой и отдельно проверяет, что аудиодорожка не короче исходной речи.
- Если YouTube upload не удался, уже готовый MP4/JSON/SRT сохраняются.
- Если отдельная генерация упала, бот повторяет попытку, а пакет из нескольких Shorts не теряет уже готовые результаты.

## GitHub Actions с телефона

Открой репозиторий → **Settings → Secrets and variables → Actions**.

### Secrets

Обязательные:

```text
MISTRAL_API
REFERRAL_URL
```

Рекомендуемый:

```text
PEXELS_API_KEY
```

Опциональные:

```text
ELEVENLABS_API_KEY
ELEVENLABS_VOICE_ID
YOUTUBE_CLIENT_SECRET_B64
YOUTUBE_TOKEN_B64
```

### Variables

Ничего обязательного нет. При желании можно задать:

```text
LANGUAGE=ru
MISTRAL_MODEL=mistral-large-latest
VOICE_PROVIDER=edge
EDGE_VOICE=auto
EDGE_RATE=+6%
AUTO_UPLOAD=0
YOUTUBE_PRIVACY=private
YOUTUBE_PAID_PROMOTION=1
YOUTUBE_SYNTHETIC_MEDIA=0
```

Затем: **Actions → Generate Crypto Shorts → Run workflow**. Можно выбрать количество роликов, принудительную тему и загрузку на YouTube.

## Локальный запуск

```bash
cp .env.example .env
# заполнить MISTRAL_API и REFERRAL_URL
bash run.sh
```

Windows: первый запуск `setup_windows.bat`, затем `run_windows.bat`.

Основные команды:

```bash
python main.py
python main.py --count 3
python main.py --topic "Funding rate"
python main.py --no-upload
python main.py --self-test
```

## Голос

По умолчанию используется Edge TTS без отдельного ключа:

```text
VOICE_PROVIDER=edge
EDGE_VOICE=auto
EDGE_RATE=+6%
```

Можно подключить ElevenLabs через `ELEVENLABS_API_KEY` и `ELEVENLABS_VOICE_ID`. При включённом `TTS_FALLBACK=1` бот использует резервный доступный провайдер при ошибке основного.

## YouTube upload

Для автоматической публикации нужен официальный OAuth YouTube. Сначала один раз создаётся `token.json` через:

```bash
python youtube_auth.py
```

Для GitHub Actions `client_secret.json` и `token.json` передаются как base64 Secrets `YOUTUBE_CLIENT_SECRET_B64` и `YOUTUBE_TOKEN_B64`.

По умолчанию `AUTO_UPLOAD=0`, поэтому обычный запуск только генерирует артефакт. Ссылка для зрителя ведёт в профиль канала; сам `REFERRAL_URL` также добавляется в описание вместе с referral disclosure.

## Результат

В `output/` появляются:

```text
<timestamp>_<title>.mp4
<timestamp>_<title>.json
<timestamp>_<title>.srt
```

JSON содержит сценарий по сценам, источник данных, media credits, качество сценария, длительность голоса, фактическую длительность видео и FFprobe QA.

## Проверки перед релизом 2.4

Проект должен проходить:

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
python main.py --self-test
python tests/smoke_render.py
```

Smoke-render специально проверяет регрессию, при которой голос мог быть длиннее итогового MP4.
