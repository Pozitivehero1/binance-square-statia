from data import get_data
from indicators import build_indicators
from filters import score_signal
from writer import write_post
from publisher import publish
from trend import get_base_asset
from history import get_recently_published, add_published, cleanup_history
import os

# Список монет для анализа (указываешь вручную)
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "RIVERUSDT", "DASHUSDT", "LABUSDT", "ASTERUSDT",
    "DUSKUSDT", "GUNUSDT", "DOGEUSDT", "WLDUSDT"
]

def get_cover_path(basic):
    """Ищет обложку в папке covers/ по базовому имени токена."""
    cover_dir = "covers/"
    if not os.path.exists(cover_dir):
        return None
    # Пробуем разные расширения
    for ext in ['.png', '.jpg', '.jpeg', '.gif']:
        path = os.path.join(cover_dir, f"{basic}{ext}")
        if os.path.exists(path):
            return path
    return None

cleanup_history()

print("BOT STARTED")
print("Analyzing symbols:", SYMBOLS)

candidates = []

for s in SYMBOLS:
    print(f"Analyzing {s}")
    raw = get_data(s)
    if raw is None:
        print(f"Skip {s} – no data")
        continue

    d = build_indicators(raw)
    d["symbol"] = s
    d["basic"] = get_base_asset(s)   # например, DUSK
    score = score_signal(d)
    print(f"{s} score = {score}")

    if score >= 4:
        d["score"] = score
        candidates.append(d)

print("Candidates:", len(candidates))
if not candidates:
    print("No good setups found")
    exit()

recent = get_recently_published(minutes=180)
print(f"Recently published (last 3h): {recent}")

filtered = [c for c in candidates if c["symbol"] not in recent]

if not filtered:
    print("All candidates were published recently. Skipping.")
    exit()

filtered.sort(key=lambda x: x["score"], reverse=True)
best = filtered[0]

print("Generating article for", best["symbol"])
title, post_text = write_post(best)
print(f"TITLE: {title}")
print("TEXT:", post_text)

# Ищем обложку
cover_path = get_cover_path(best["basic"])
if cover_path:
    print(f"[COVER] Using cover: {cover_path}")
else:
    print("[COVER] No cover found for this token, posting without cover.")

# Публикуем статью
success = publish(post_text, title=title, cover_path=cover_path)

if success:
    add_published(best["symbol"])
    print("DONE")
else:
    print("Publication failed, not saving symbol.")
