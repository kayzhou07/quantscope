"""量曜：基础两日连板、缩量规则。0.7实验版必须显式选择。"""

import math


def select_candidates(rows, volume_ratio=1.0):
    if not 0 < volume_ratio <= 1:
        raise ValueError("缩量阈值范围错误")
    found = []
    seen = set()
    for r in rows:
        s = r["symbol"]
        if s in seen:
            raise ValueError("候选代码重复")
        seen.add(s)
        if s[:2] not in ("00", "60", "30", "68"):
            continue
        vals = [
            float(r[k]) for k in ("pct_chg", "prev_pct_chg", "volume", "prev_volume")
        ]
        if not all(math.isfinite(v) for v in vals) or min(vals[2:]) < 0:
            raise ValueError("行情数值无效")
        threshold = (20 if s[:2] in ("30", "68") else 10) * 0.995
        if (
            vals[0] >= threshold
            and vals[1] >= threshold
            and vals[2] < vals[3] * volume_ratio
        ):
            found.append((vals[2], s))
    # 同成交量按代码排序是新接口的确定性约定，不影响命中集合。
    return [s for _, s in sorted(found)]


def evaluate(data, histories):
    records = []
    for s, bars in histories.items():
        a, b = bars[-2:]
        records.append(
            dict(
                symbol=s,
                pct_chg=b["pct_chg"],
                prev_pct_chg=a["pct_chg"],
                volume=b["volume"],
                prev_volume=a["volume"],
            )
        )
    variant = data.get("variant", "baseline")
    if variant not in ("baseline", "volume_0_7"):
        raise ValueError("未知量曜版本")
    selected = select_candidates(records, 0.7 if variant == "volume_0_7" else 1.0)
    # 只形成等权目标，不再以无法事先取得的收盘价声称实际买入。
    return {s: 1 / len(selected) for s in selected}, variant
