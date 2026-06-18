from data import get_data
from indicators import build_indicators
from filters import score_signal
from writer import write_post
from publisher import publish
from trend import get_trending_symbols, get_base_asset
from history import get_recently_published, add_published, cleanup_history
from chart import generate_chart
import os

cleanup_history()

symbols = get_trending_symbols(100)
print("TRENDING:", symbols)
print("BOT STARTED")

candidates = []

for s in symbols:
    print(f"Analyzing {s}")
    raw = get_data(s)
    if raw is None:
        print(f"Skip {s} – no data")
        continue

    d = build_indicators(raw)
    d["symbol"] = s
    d["basic"] = get_base_asset(s)
    d["raw"] = raw  # сохраняем сырые данные для графика
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

print("Generating post for", best["symbol"])
post = write_post(best)
print("POST:", post)

# Генерируем график
chart_path = generate_chart(best["symbol"], best["raw"], best["basic"])
if chart_path:
    print(f"[CHART] Chart generated: {chart_path}")
else:
    print("[CHART] Chart generation failed, posting text only.")

# Публикуем с графиком (если есть)
success = publish(post, image_path=chart_path)

if success:
    add_published(best["symbol"])
    print("DONE")
else:
    print("Publication failed, not saving symbol.")

# Удаляем временный график (если создан)
if chart_path and os.path.exists(chart_path):
    os.remove(chart_path)
