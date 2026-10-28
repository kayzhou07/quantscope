"""量枢冻结A：评分与目标仓位快照。完整调仓/撮合保留在research平台脚本。"""

import bisect
from datetime import date
import json
import math
from pathlib import Path
import statistics


class FrozenRegimeExpired(ValueError):
    pass


def state_at(day):
    date.fromisoformat(day)
    cfg = json.loads(Path(__file__).with_name("frozen_regimes.json").read_text())
    if day > cfg["end_date"]:
        raise FrozenRegimeExpired("冻结状态仅覆盖至" + cfg["end_date"])
    i = bisect.bisect_right([r[0] for r in cfg["runs"]], day) - 1
    if i < 0:
        raise ValueError("冻结状态起始日之前无状态")
    return cfg["runs"][i][1]


def rank_candidates(rows):
    if not rows:
        return []
    if len({r["symbol"] for r in rows}) != len(rows):
        raise ValueError("候选代码重复")
    ranks = {}
    for field in ("ras", "m6", "inv"):
        values = [float(r[field]) for r in rows]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("特征必须有限")
        # pandas.rank(pct=True) 默认平均秩；先对全特征池排名，再筛资格。
        ranks[field] = [
            (sum(x < v for x in values) + (sum(x == v for x in values) + 1) / 2)
            / len(values)
            for v in values
        ]
    scored = [
        (
            r["symbol"],
            0.5 * ranks["ras"][i] + 0.3 * ranks["m6"][i] + 0.2 * ranks["inv"][i],
        )
        for i, r in enumerate(rows)
        if r["eligible"]
    ]
    return sorted(scored, key=lambda p: (-p[1], p[0]))


def evaluate(data, histories, signal_date):
    if data.get("regime_source", "frozen") == "frozen":
        state = state_at(signal_date)
    elif data.get("regime_source") == "external":
        if data.get("regime_date") != signal_date:
            raise ValueError("外部状态日期不一致")
        state = data.get("regime_state")
    else:
        raise ValueError("未知市场状态来源")
    if state not in ("B", "N", "E", "C"):
        raise ValueError("未知市场状态")
    exposure = 1.0 if state == "B" else 0.3 if state == "N" else 0.0
    if not exposure:
        return {}, {
            "state": state,
            "regime_source": data.get("regime_source", "frozen"),
            "scope": "target_snapshot",
        }
    rows = []
    for s, bars in histories.items():
        allowed = (
            s.endswith(".SH") and s.startswith(("600", "601", "603", "605"))
        ) or (
            s.endswith(".SZ")
            and s.startswith(("000", "001", "002", "003", "300", "301"))
        )
        if not allowed:
            continue
        last = bars[-1]
        if last.get("is_st") not in (0, 1) or last.get("is_paused") not in (0, 1):
            raise ValueError("缺少ST/停牌状态")
        if last["is_st"] or last["is_paused"]:
            continue
        x = [float(b["close"]) for b in bars]
        returns = [x[i] / x[i - 1] - 1 for i in range(len(x) - 61, len(x))]
        vol = statistics.stdev(returns) * math.sqrt(252)
        if vol <= 0:
            continue
        raw = float(last["raw_close"])
        turns = [float(b["turnover"]) for b in bars[-60:]]
        if (
            not all(math.isfinite(v) and v >= 0 for v in turns)
            or not math.isfinite(raw)
            or raw <= 0
        ):
            raise ValueError("原始价格或成交额无效")
        eligible = (
            raw >= 2
            and statistics.mean(turns) >= 2e8
            and x[-1] > statistics.mean(x[-60:]) > statistics.mean(x[-120:])
            and vol <= 0.55
        )
        rows.append(
            dict(
                symbol=s,
                ras=(x[-22] / x[-253] - 1) / vol,
                m6=x[-22] / x[-127] - 1,
                inv=1 / vol,
                raw=raw,
                eligible=eligible,
            )
        )
    ranked = rank_candidates(rows)
    names = [s for s, _ in ranked]
    rank = {s: i for i, s in enumerate(names)}
    held = data.get("held", [])
    selected = sorted(
        {s for s in held if rank.get(s, 10**9) < 20}, key=lambda s: rank[s]
    )[:15]
    for s in names:
        if len(selected) >= 15:
            break
        if s not in selected:
            selected.append(s)
    nav = float(data.get("nav", 100000.0))
    if not math.isfinite(nav) or nav <= 0:
        raise ValueError("净资产无效")
    raw = {r["symbol"]: r["raw"] for r in rows}
    selected = [s for s in selected if raw[s] * 100 <= exposure * nav / 15 * 1.02]
    weights = {s: exposure / len(selected) for s in selected}
    return weights, {
        "state": state,
        "scores": dict(ranked),
        "regime_source": data.get("regime_source", "frozen"),
        "scope": "target_snapshot",
        "selected": selected,
    }
