from data import get_data
from indicators import build_indicators
from filters import score_signal
from writer import write_post
from publisher import publish
from trend import get_base_asset  # оставляем для получения правильного тикера
from history import get_recently_published, add_published, cleanup_history
import os

# ========== НАСТРОЙКА ==========
# Список монет для анализа (пары USDT)
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "RIVERUSDT",
    "DASHUSDT",
    "LABUSDT",
    "ASTERUSDT",
    "DUSKUSDT",
    "GUNUSDT",
    "DOGEUSDT",
    "WLDUSDT",
]

# Папка с обложками
COVERS_DIR = "covers"

# Минимальный скор для публикации
MIN_SCORE = 4

# ================================

cleanup_history()

print("BOT STARTED")
print(f"Analyzing symbols: {SYMBOLS}")

candidates = []

for s in SYMBOLS:
    print(f"Analyzing {s}")
    raw = get_data(s)
    if raw is None:
        print(f"Skip {s} – no data")
        continue

    d = build_indicators(raw)
    d["symbol"] = s
    d["basic"] = get_base_asset(s)  # короткий тикер (BTC, ETH...)
    d["raw"] = raw  # для возможного использования (не для графика)
    score = score_signal(d)
    print(f"{s} score = {score}")

    if score >= MIN_SCORE:
        d["score"] = score
        candidates.append(d)

print("Candidates:", len(candidates))
if not candidates:
    print("No good setups found")
    exit()

# Исключаем недавно опубликованные
recent = get_recently_published(minutes=180)
print(f"Recently published (last 3h): {recent}")

filtered = [c for c in candidates if c["symbol"] not in recent]

if not filtered:
    print("All candidates were published recently. Skipping.")
    exit()

# Выбираем лучшего по скору
filtered.sort(key=lambda x: x["score"], reverse=True)
best = filtered[0]

print("Generating article for", best["symbol"])

# Генерируем статью (заголовок + текст)
title, post_text = write_post(best)
print(f"TITLE: {title}")
print("TEXT:", post_text)

# Ищем обложку в папке covers/
cover_path = None
cover_filename = f"{best['basic']}.png"  # например, BTC.png
cover_full_path = os.path.join(COVERS_DIR, cover_filename)
if os.path.exists(cover_full_path):
    cover_path = cover_full_path
    print(f"[COVER] Using cover: {cover_path}")
else:
    print(f"[COVER] Cover not found for {best['basic']}, posting without cover.")

# Публикуем статью
success = publish(post_text, title=title, cover_path=cover_path)

if success:
    add_published(best["symbol"])
    print("DONE")
else:
    print("Publication failed, not saving symbol.")
