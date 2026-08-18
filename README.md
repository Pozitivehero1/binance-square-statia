# Crypto Shorts Bot 2.2 Production

Бот «под ключ» для автоматической сборки оригинальных вертикальных YouTube Shorts о крипто-механике и рыночных данных с мягким CTA на реферальную ссылку Binance.

## Что усилено в 2.2

- Генерирует **3 редакционных варианта** сценария и выбирает лучший локальным quality-score вместо публикации первого ответа LLM.
- Сценарий состоит из **5–7 смысловых сцен**: отдельный voice-over, отдельный видеозапрос и короткий экранный тезис для каждой сцены.
- Контроль повторяемости: история тем, хуков и Pexels video ID между запусками.
- Binance Spot: публичные 24h rolling данные по USDT-парам — цена, изменение, high/low, quote volume, weighted average и число сделок. API-ключ Binance не нужен; причины движения не выдумываются.
- Pexels выбирается **по сценам**, а не случайным пулом. При ошибке/лимите/отсутствии ключа бот сам создаёт локальный абстрактный визуал и продолжает работу.
- Edge TTS и ElevenLabs теперь возвращают **тайминги речи**. Для ElevenLabs используется endpoint `with-timestamps`; для Edge — WordBoundary.
- Субтитры привязаны к речи, а не распределены по тексту «на глаз»; длинные русские фразы адаптивно переносятся и не вылезают за края кадра.
- Экранный hook, scene beats, динамические captions и отдельный CTA.
- Voice mastering: high-pass/low-pass, compression и loudness normalization. Музыка при наличии тихо подмешивается под голос; битый музыкальный файл автоматически исключается, не ломая весь рендер.
- Рендер проходит автоматический QA через `ffprobe`: размер, длительность, аудио/видео потоки, кодеки.
- Если YouTube upload упал, готовый MP4 **не теряется**.
- Если одна попытка генерации упала, бот делает до `GENERATION_ATTEMPTS` попыток с другой темой.
- Выводит `.mp4`, `.json` и `.srt`.
- GitHub Actions имеет удобные мобильные inputs: количество роликов, принудительная тема, переключатель YouTube upload.
- CTA оптимизирован под реальный Shorts UX: «ссылка в профиле», потому что URL в описании Shorts некликабельны.

## Минимум для запуска

1. Python 3.11+ и FFmpeg.
2. Скопировать `.env.example` в `.env`.
3. Заполнить:

```env
MISTRAL_API=...
REFERRAL_URL=https://...
```

`PEXELS_API_KEY` теперь не является точкой отказа: без него визуалы будут созданы локально. Но для более живого видеоряда Pexels рекомендуется.

Рыночные темы берутся из публичного Binance Spot market-data endpoint `https://data-api.binance.vision/api/v3/ticker/24hr`; отдельный Binance API key не требуется. Если endpoint временно недоступен, `mixed` автоматически продолжит работу на evergreen-темах.

По умолчанию выбран `mistral-large-latest` для качества сценария. Если важнее снизить стоимость API, можно заменить `MISTRAL_MODEL` на `mistral-small-latest`.

## Windows

Первый раз:

```text
setup_windows.bat
```

Потом:

```text
run_windows.bat
```

## Linux / macOS

```bash
cp .env.example .env
# заполнить .env
bash run.sh
```

## Docker

```bash
docker build -t crypto-shorts-bot .
docker run --rm --env-file .env -v "$PWD/output:/app/output" -v "$PWD/music:/app/music:ro" crypto-shorts-bot
```

`.dockerignore` исключает `.env`, OAuth-файлы, state, output и локальное окружение из build context.

## Команды

```bash
python main.py                    # 1 автоматическая тема
python main.py --count 3          # 3 ролика
python main.py --topic "Funding rate"  # своя тема
python main.py --no-upload        # никогда не грузить на YouTube
python main.py --keep-work        # оставить промежуточные файлы
python main.py --self-test        # FFmpeg/libass check, без API
```

## Голос

По умолчанию:

```env
VOICE_PROVIDER=edge
EDGE_VOICE=auto
EDGE_RATE=+4%
TTS_FALLBACK=1
```

`EDGE_VOICE=auto` автоматически выбирает русский голос для `LANGUAGE=ru` и английский для `LANGUAGE=en`; при желании можно указать конкретное имя Edge-голоса.

Если основной TTS-провайдер недоступен, бот попробует резервный: ElevenLabs — только когда его ключ и voice ID заполнены; Edge может быть резервом для ElevenLabs.

Для ElevenLabs:

```env
VOICE_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

ElevenLabs используется через API с timestamp alignment, чтобы субтитры шли по речи.

## Музыка

Положить **только музыку, на которую есть право коммерческого использования**, в `music/` (`mp3`, `wav`, `m4a`, `aac`). Если папка пустая, бот прекрасно работает без музыки. Громкость задаёт `MUSIC_VOLUME`.

## Важно: куда вести реферальный трафик

YouTube делает обычные URL в **описаниях и комментариях Shorts некликабельными**. Поэтому версия 2.2 не говорит зрителю «ссылка в описании»: CTA в голосе и на экране ведёт на **первую ссылку в профиле канала**.

Перед первым запуском с реферальным CTA один раз добавьте `REFERRAL_URL` в YouTube Studio → Customization / Настройка канала → Profile / Профиль → Links / Ссылки и поставьте её первой. Сам URL всё равно дублируется в описании вместе с referral disclosure, но основной кликабельный путь — профиль канала.

## YouTube OAuth

1. В Google Cloud включить YouTube Data API v3.
2. Создать OAuth Desktop App.
3. Сохранить JSON как `client_secret.json`.
4. Один раз на компьютере с браузером:

```bash
python youtube_auth.py
```

5. Появится `token.json`.

> Если вы обновились с версии бота, где OAuth запрашивал только `youtube.upload`, удалите старый `token.json` и один раз снова запустите `youtube_auth.py`: в 2.2 дополнительно используется scope `youtube.force-ssl` для установки disclosure paid promotion.

6. В `.env`:

```env
AUTO_UPLOAD=1
YOUTUBE_PRIVACY=private
```

По умолчанию `YOUTUBE_PAID_PROMOTION=1`: проект построен вокруг коммерческой реферальной ссылки, поэтому uploader после загрузки пытается выставить YouTube paid-promotion/commercial-relationship flag. В описании независимо от этого всегда остаётся явный referral disclosure.

Учтите: YouTube может принудительно оставлять API-загрузки `private` для непроверенных API-проектов до прохождения аудита проекта. Это ограничение YouTube, а не бота.

`YOUTUBE_SYNTHETIC_MEDIA=0` не меняйте автоматически: включайте его только если конкретный визуальный контент подпадает под требования YouTube к disclosure синтетически созданного/существенно изменённого реалистичного контента.

## GitHub Actions с телефона

Workflow: `.github/workflows/shorts.yml`.

### Secrets

Обязательные:

- `MISTRAL_API`
- `REFERRAL_URL`

Рекомендуемые:

- `PEXELS_API_KEY`

Опциональные:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`
- `YOUTUBE_CLIENT_SECRET_B64`
- `YOUTUBE_TOKEN_B64`

### Variables

Обязательных Variables нет: бренд Binance уже зафиксирован в проекте.

Опциональные: `LANGUAGE`, `MISTRAL_MODEL`, `VOICE_PROVIDER`, `EDGE_VOICE`, `EDGE_RATE`, `AUTO_UPLOAD`, `YOUTUBE_PRIVACY`, `YOUTUBE_PAID_PROMOTION`, `YOUTUBE_SYNTHETIC_MEDIA`.

В Actions → **Generate Crypto Shorts → Run workflow** появятся три поля: число роликов, тема (можно оставить пустой), и Upload to YouTube.

## Что появляется в output/

```text
20260818_120000_topic.mp4
20260818_120000_topic.json
20260818_120000_topic.srt
```

JSON содержит сценарий по сценам, фактическую основу, media credits, script quality score, FFprobe QA и информацию о загрузке.

## Важные практические правила

- Не добавляйте в prompt/тему несуществующие бонусы или гарантированную доходность.
- Проверяйте условия конкретной реферальной программы биржи и применимое законодательство к рекламе финансовых/крипто-сервисов.
- Pexels API используется через `/v1/videos/search`; если его материалы реально использованы, описание содержит ссылку на Pexels и credits.
- Чтобы канал не превращался в однообразную фабрику, не ставьте слишком высокую частоту публикаций сразу. Сначала посмотрите, какие форматы реально удерживают зрителя, и только затем масштабируйте.
