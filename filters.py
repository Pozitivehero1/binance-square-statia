def score_signal(d):

    score = 0

    # тренд
    if d["ema20"] > d["ema50"]:
        score += 2

    # импульс
    if abs(d["change"]) > 2:
        score += 2

    # RSI зона интереса
    if 45 < d["rsi"] < 75:
        score += 2

    # перегрев
    if d["rsi"] > 85:
        return 0

    return score
