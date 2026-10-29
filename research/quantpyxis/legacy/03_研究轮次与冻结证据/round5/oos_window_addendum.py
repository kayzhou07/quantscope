# -*- coding: utf-8 -*-
"""Correct the 2024-09 'latest window' comparison: two start dates, all benchmarks, window DD.

The round-4 report used 2024-09-18.  The user's pre-registered 2024-09 window starts at the
2024-08-30 close.  Candidate A was in cash across 2024-08-30..2024-09-18, so its return is
identical under both starts, but the benchmark numbers change.  This script writes the corrected
two-window table from the published NAV CSVs (read-only) plus a Markdown addendum.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

WS = Path(__file__).resolve().parents[2]
R4OUT = WS / "winrate_backtest_20261020" / "round4" / "out"
R5 = WS / "winrate_backtest_20261020" / "round5"
CAP = 100_000.0
INSTRUMENTS = [
    ("A_b100_n30_top15", "full_b100_n30_top15_nav.csv", "A：B100/N30、top15"),
    ("S0_local_baseline", "full_V_base_nav.csv", "本地 S0/R2 规则基线"),
    ("B_b100_n90_top20_novol", "full_n90_top20_novolcap_nav.csv", "B：B100/N90、top20、无波动上限"),
    ("C_b100_n90_top15_novol", "full_n90_top15_novolcap_nav.csv", "C：B100/N90、top15、无波动上限"),
    ("REF_none_100", "full_REF_none_100_nav.csv", "不用状态、始终满仓"),
    ("CSI300_bh", "full_bh_csi300_nav.csv", "沪深300 买入持有"),
    ("ChiNext_bh", "full_bh_chinext_nav.csv", "创业板指 买入持有"),
]
STARTS = [("2024-08-30", "2024-08-30 收盘起"), ("2024-09-18", "2024-09-18 收盘起")]


def window(nav: pd.Series, start: str) -> dict:
    sub = nav.loc[nav.index >= start]
    if len(sub) < 2:
        return {}
    peak = sub.cummax()
    dd = sub / peak - 1
    return {"start": str(sub.index[0]), "end": str(sub.index[-1]), "days": int(len(sub)),
            "return": float(sub.iloc[-1] / sub.iloc[0] - 1),
            "max_drawdown": float(dd.min())}


def main():
    rows, meta = [], {}
    for key, fname, label in INSTRUMENTS:
        p = R4OUT / fname
        nav = pd.read_csv(p, index_col=0)
        nav.index = nav.index.astype(str)
        n = nav["nav"].astype(float)
        meta[key] = label
        for start, slabel in STARTS:
            w = window(n, start)
            if w:
                rows.append(dict(instrument=key, label=label, start_window=start, **w))
    df = pd.DataFrame(rows)
    df.to_csv(R5 / "OOS_WINDOW_ADDENDUM.csv", index=False)
    json.dump({"instruments": meta, "rows": rows},
              open(R5 / "OOS_WINDOW_ADDENDUM.json", "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)

    def table(start: str) -> str:
        sub = df[df["start_window"] == start]
        lines = ["| 标的 | 窗口起点→终点 | 收益 | 窗口内最大回撤 |", "|---|---|---:|---:|"]
        for _, r in sub.iterrows():
            lines.append(f"| {r['label']} | {r['start']} → {r['end']} | "
                         f"{r['return'] * 100:+.2f}% | {r['max_drawdown'] * 100:.2f}% |")
        return "\n".join(lines)

    a = df[(df["instrument"] == "A_b100_n30_top15")]
    same = bool(len(a) == 2 and abs(a["return"].iloc[0] - a["return"].iloc[1]) < 1e-12)
    md = f"""# 2024-09 起点口径补全（OOS 窗口）

日期：2026-10-20（第五轮补）。原因：第四轮报告用 2024-09-18 起算，用户预登记的窗口是
**2024-08-30 收盘**起算。候选 A 在 2024-08-30..2024-09-18 期间为全现金（净值 238,821.888 两天相同），
所以 A 的收益两口径相同（{'是' if same else '否，需检查'}），但基准不同。两个口径全部并列，不择其一。

## 窗口 A：2024-08-30 收盘起（用户预登记口径）

{table('2024-08-30')}

## 窗口 B：2024-09-18 收盘起（第四轮报告原口径）

{table('2024-09-18')}

## 结论边界

1. **A 的 +14.96% 在两口径下相同**（期间全现金），可作为"预注册候选集的第一眼 OOS 观测"，
   但仍受 F6 限制：A 的最终入选也参考了 2024 年以后表现，不是纯训练期决定的。
2. 基准在两口径下差异明显（沪深300 +25.68% vs +31.65%；创业板 +73.42% vs +78.71%），
   因此**比较必须同时给出起点**，不能只改标签。
3. 窗口内最大回撤按窗口内峰值计算，两口径数值相同，但含义是"从该窗口起点之后的高点回撤"。
4. 这些历史全部属于已见研究证据；前向证据从 `round5/SEAL` 的 freeze_at 之后开始收集。
"""
    (R5 / "OOS_WINDOW_ADDENDUM.md").write_text(md, encoding="utf-8")
    print(df.to_string(index=False))
    print("wrote", R5 / "OOS_WINDOW_ADDENDUM.md")


if __name__ == "__main__":
    main()
