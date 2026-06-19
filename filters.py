def score_signal(d):

    score = 0

    # Краткосрочный тренд
    if d["ema20"] > d["ema50"]:
        score += 2

    # Глобальный тренд
    if d["ema50"] > d["ema200"]:
        score += 3

    # Здоровый RSI
    if 50 <= d["rsi"] <= 70:
        score += 2

    # Импульс
    if abs(d["change"]) >= 3:
        score += 2

    # Объем
    if d["volume_ratio"] >= 1.5:
        score += 3

    # Сильный объем
    if d["volume_ratio"] >= 2:
        score += 2

    # Перегрев
    if d["rsi"] > 85:
        return 0

    return score
