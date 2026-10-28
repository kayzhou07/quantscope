"""公司级目标合并。预算是配置假设；未建立成本优化或自动交易系统。"""

from math import isfinite
from quantscope.core import Signal, dates


def combine(signals, budgets, *, max_asset=0.25, sectors=None, max_sector=None):
    if not signals:
        raise ValueError("至少需要一个模型信号")
    models = [s.model for s in signals]
    if len(models) != len(set(models)):
        raise ValueError("模型信号重复")
    if any(m not in ("quantorion", "quantlibra", "quantpyxis") for m in models):
        raise ValueError("公司组合仅接收三个经营模型")
    if set(models) != set(budgets):
        raise ValueError("模型与预算必须完全匹配，不能静默丢失失败模型")
    if len({(s.signal_date, s.execute_after) for s in signals}) != 1:
        raise ValueError("信号与执行日期必须对齐")
    if any(s.status not in ("ok", "no_candidates") for s in signals):
        raise ValueError("失败信号禁止进入组合")
    for s in signals:
        dates(s.signal_date, s.execute_after)
    values = [float(v) for v in budgets.values()]
    if not all(isfinite(v) and 0 <= v <= 1 for v in values) or sum(values) > 1 + 1e-9:
        raise ValueError("模型预算超限")
    if not isfinite(max_asset) or not 0 < max_asset <= 1:
        raise ValueError("单资产上限无效")
    weights = {}
    for s in signals:
        for code, w in s.targets.items():
            weights[code] = weights.get(code, 0.0) + float(budgets[s.model]) * w
    if any(w > max_asset + 1e-9 for w in weights.values()):
        raise ValueError("单资产集中度超限，拒绝而非隐式改权重")
    if max_sector is not None:
        if not isfinite(max_sector) or not 0 < max_sector <= 1:
            raise ValueError("行业上限无效")
        if sectors is None or any(c not in sectors for c in weights):
            raise ValueError("缺少行业归属")
        grouped = {}
        for code, w in weights.items():
            grouped[sectors[code]] = grouped.get(sectors[code], 0.0) + w
        if any(w > max_sector + 1e-9 for w in grouped.values()):
            raise ValueError("行业集中度超限")
    return Signal(
        "portfolio",
        "1.0.0",
        signals[0].signal_date,
        signals[0].execute_after,
        "ok",
        weights,
        "allocated",
        "预算加权目标；成本、流动性与成交回执仍需平台复核",
        {"budgets": budgets},
    )
