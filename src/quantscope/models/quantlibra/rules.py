"""量衡V6：本地26点/平台25点分别命名，保留自然日冷却。"""

import datetime

ETFS = ("510880", "159915", "513100", "518880")


def linreg_slope_r2(y):
    n = len(y)
    xm = (n - 1) / 2
    ym = sum(y) / n
    xx = sum((i - xm) ** 2 for i in range(n))
    xy = sum((i - xm) * (v - ym) for i, v in enumerate(y))
    yy = sum((v - ym) ** 2 for v in y)
    if xx == 0 or yy == 0:
        return 0.0, 0.0
    return xy / xx, xy * xy / (xx * yy)


def calc_slope_momentum(closes):
    slope, r2 = linreg_slope_r2([c / closes[0] for c in closes])
    return slope * r2


def calc_eff_momentum(closes):
    ret = closes[-1] / closes[0] - 1
    path = sum(abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)))
    return 0.0 if path < 1e-12 else ret * (abs(ret) / path)


def decide(closes, state, today):
    slope = {c: calc_slope_momentum(closes[c]) for c in ETFS}
    eff = {c: calc_eff_momentum(closes[c]) for c in ETFS}

    def rank(scores):
        return {
            c: i + 1
            for i, c in enumerate(sorted(ETFS, key=lambda c: scores[c], reverse=True))
        }

    sr = rank(slope)
    er = rank(eff)
    composite = {c: 0.5 * sr[c] + 0.5 * er[c] for c in ETFS}
    best = min(composite, key=composite.get)
    current = state.get("holding")
    entry = state.get("entry_date")
    days = 0
    if current is not None and current not in ETFS:
        raise ValueError("当前持仓不在量衡候选池")
    if current:
        if not entry:
            raise ValueError("有持仓时必须提供entry_date")
        days = (today - datetime.date.fromisoformat(entry)).days
        if days < 0:
            raise ValueError("建仓日不能在信号日之后")
    if eff[best] <= 0:
        target = None
    elif current is None:
        target = best
    elif best == current:
        target = current
    else:
        target = current if days < 14 and composite[current] <= 3 else best
    return dict(
        slope_m=slope,
        eff_m=eff,
        composite=composite,
        best=best,
        current=current,
        target=target,
        days_held=days,
    )


def evaluate(data, histories, signal_date):
    variant = data.get("variant", "local_v6_26")
    if variant not in ("local_v6_26", "platform_v6_25"):
        raise ValueError("未知量衡版本")
    n = 26 if variant == "local_v6_26" else 25
    series = {
        c: [
            float(b["close"]) for b in histories[c + (".SH" if c[0] == "5" else ".SZ")]
        ][-n:]
        for c in ETFS
    }
    state = dict(data.get("state", {}))
    if state.get("holding"):
        state["holding"] = state["holding"].split(".")[0]
    result = decide(series, state, datetime.date.fromisoformat(signal_date))
    target = result["target"]
    weights = {target + (".SH" if target[0] == "5" else ".SZ"): 1.0} if target else {}
    return weights, result
