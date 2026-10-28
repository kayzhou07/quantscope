"""严格离线日线校验。禁止跨日拼接、重复日期及隐式跳过缺失值。"""

from datetime import date
from math import isfinite
from quantscope.core import asset


def positive(value):
    v = float(value)
    if not isfinite(v) or v <= 0:
        raise ValueError("价格必须为有限正数")
    return v


def history(data, as_of, min_count):
    source = data.get("history")
    if not isinstance(source, dict) or not source:
        raise ValueError("缺少history行情")
    result = {}
    reference = None
    for symbol, rows in source.items():
        symbol = asset(symbol)
        if symbol in result:
            raise ValueError("重复资产别名")
        if not isinstance(rows, list) or len(rows) < min_count:
            raise ValueError("行情观察数不足")
        days = [date.fromisoformat(r["date"]) for r in rows]
        if days != sorted(set(days)) or days[-1] != date.fromisoformat(as_of):
            raise ValueError("行情必须日期升序、不重复且截至信号日")
        tail = days[-min_count:]
        if reference is not None and tail != reference:
            raise ValueError("资产交易日期不对齐")
        reference = tail
        for row in rows:
            positive(row["close"])
        result[symbol] = rows
    return result
