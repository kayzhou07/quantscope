"""量寰：按研究说明新增的纯规则实现；不是历史账户回测引擎。"""

import math
import statistics


def three_state(closes):
    if len(closes) < 200:
        raise ValueError("三态需要200个完整收盘价")
    p = closes[-1]
    fast = statistics.mean(closes[-50:])
    slow = statistics.mean(closes[-200:])
    if p > slow and fast > slow:
        return {"TQQQ.US": 1.0}
    if p < slow and fast < slow:
        return {"QQQ.US": 0.5}
    return {"QQQ.US": 1.0}


def volatility_weights(exposure):
    if not math.isfinite(exposure) or not 0 <= exposure <= 2.5:
        raise ValueError("指数暴露超出研究范围")
    if exposure <= 1:
        return {"QQQ.US": exposure} if exposure else {}
    if exposure <= 2:
        return {"QQQ.US": 2 - exposure, "QLD.US": exposure - 1}
    return {"QLD.US": 3 - exposure, "TQQQ.US": exposure - 2}


def evaluate(data, rows):
    closes = [float(r["close"]) for r in rows["QQQ.US"]]
    branch = data.get("branch", "daily_three_state")
    if branch == "daily_three_state":
        return three_state(closes), "daily_three_state"
    if branch != "monthly_volatility":
        raise ValueError("未知量寰研究分支")
    if not data.get("rebalance_due", False):
        raise ValueError("月度分支须由外部交易日历确认rebalance_due")
    if closes[-1] <= statistics.mean(closes[-200:]):
        return {}, "monthly_cash"
    returns = [
        closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 63, len(closes))
    ]
    sigma = statistics.stdev(returns) * math.sqrt(252)
    if sigma <= 0:
        raise ValueError("波动率为零，不能计算指数暴露")
    return volatility_weights(min(2.5, 0.35 / sigma)), "monthly_volatility"
