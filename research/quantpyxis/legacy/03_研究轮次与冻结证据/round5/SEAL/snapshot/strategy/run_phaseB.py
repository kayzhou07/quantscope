# -*- coding: utf-8 -*-
"""Round 4 Phase B: full window 2014-07-01..2026-10-19 for the train-selected candidates.

Selection was made on 2014-2023 ONLY (Phase A/A2).  The 2021-02..2024-09 and 2024-09..2026-10
segments are therefore one-step out-of-sample checks for the selection decision.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import variants as V  # noqa: E402

REPO = V.REPO
from engine.api import run_backtest, BuyHold  # noqa: E402

OUT = HERE / "out"
START, END = "2014-07-01", "2026-10-19"
CAP = 100_000.0
STATE_MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}

EXP100_30 = {"B": 1.0, "N": .30, "E": 0, "C": 0}
EXP100_25 = {"B": 1.0, "N": .25, "E": 0, "C": 0}
EXP80_30 = {"B": .80, "N": .30, "E": 0, "C": 0}
B = dict(state_mode="frozen", top_n=10, buffer=20, max_vol=0.55,
         score_mode="base", afford="drop")

CANDS = [
    ("V_base", dict(B), "M"),
    ("b80_n30", dict(B, exposure=EXP80_30), "M"),
    ("b100_n25", dict(B, exposure=EXP100_25), "M"),
    ("b100_n30", dict(B, exposure=EXP100_30), "M"),
    ("b100_n30_vt20", dict(B, exposure=EXP100_30, vol_target=.20), "M"),
    ("b100_n30_vt15", dict(B, exposure=EXP100_30, vol_target=.15), "M"),
    ("b80_n30_vt18", dict(B, exposure=EXP80_30, vol_target=.18), "M"),
    ("b100_n30_lowvol", dict(B, exposure=EXP100_30, score_mode="lowvol"), "M"),
    ("b100_n30_top15", dict(B, exposure=EXP100_30, top_n=15), "M"),
    ("b100_n30_capma200", dict(B, exposure=EXP100_30, index_cap="ma200"), "M"),
    ("base_vt18", dict(B, vol_target=.18), "M"),
    ("REF_none_100", dict(B, state_mode="none", full=1.0), "M"),
    ("REF_ma200_100", dict(B, state_mode="ma200", full=1.0), "M"),
]

SEGS = [
    ("bull_2014_2015", "2014-07-01", "2015-06-12"),
    ("crash_2015_2016", "2015-06-15", "2016-01-28"),
    ("bear_2018", "2018-01-02", "2018-12-31"),
    ("bull_2019_2021", "2019-01-02", "2021-02-10"),
    ("bear_2021_2024", "2021-02-18", "2024-09-13"),
    ("bull_2024_now", "2024-09-18", "2026-10-19"),
]
LATEST = ("2024-09-18", "2026-10-19")


def load_regime():
    reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    return {d: STATE_MAP[s] for d, s in zip(reg["date"], reg["state"])}


def seg_stats(nav: pd.Series, s: str, e: str) -> dict:
    sub = nav.loc[(nav.index >= s) & (nav.index <= e)]
    if len(sub) < 2:
        return {}
    ret = float(sub.iloc[-1] / sub.iloc[0] - 1.0)
    dd = float((sub / sub.cummax() - 1).min())
    return dict(ret=ret, dd=dd, days=len(sub))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = load_regime()
    rows, navs = [], {}
    print(f"{'tag':20s} {'ret':>8s} {'maxDD':>8s} {'sharpe':>7s} {'2024-09+':>9s} {'oosDD':>8s} {'sec':>6s}")
    for tag, kw, rb in CANDS:
        strat = V.Variant(name=f"R4B-{tag}", **kw)
        t0 = time.time()
        res = run_backtest(strat, start=START, end=END, capital=CAP,
                           data_dir=REPO / "data", warmup_days=340, rebalance=rb,
                           benchmark=["sh000300", "sz399006"], slippage_bps=10.0,
                           commission_bps=2.5, min_commission=5.0,
                           regime_labels=labels, name=strat.name)
        el = time.time() - t0
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        navs[tag] = nav
        nav.to_csv(OUT / f"full_{tag}_nav.csv")
        s = res["metrics"]
        n = nav["nav"].astype(float)
        day_ret = n.pct_change().fillna(0.0)
        latest = seg_stats(n, *LATEST)
        row = dict(tag=tag, total_return=float(n.iloc[-1] / CAP - 1),
                   max_drawdown=float((n / n.cummax() - 1).min()),
                   cagr=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1),
                   ann_vol=float(day_ret.std() * np.sqrt(252)),
                   sharpe=float(day_ret.mean() / day_ret.std() * np.sqrt(252))
                   if day_ret.std() > 0 else None,
                   calmar=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1)
                   / abs(float((n / n.cummax() - 1).min())) if (n / n.cummax() - 1).min() < 0 else None,
                   avg_exposure=float(nav["exposure"].astype(float).mean()),
                   all_cash_days=int((nav["market_value"].astype(float) <= .01).sum()),
                   trade_count=int(s.get("trade_count", 0)),
                   total_fees=float(s.get("total_fees", 0)),
                   latest_ret=latest.get("ret"), latest_dd=latest.get("dd"),
                   elapsed_s=round(el, 1))
        rows.append(row)
        print(f"{tag:20s} {row['total_return']:8.4f} {row['max_drawdown']:8.4f} "
              f"{row['sharpe']:7.2f} {row['latest_ret']:9.4f} {row['latest_dd']:8.4f} {el:6.1f}")

    # benchmarks ------------------------------------------------------------
    bench = {}
    for tag, target in (("bh_csi300", "sh000300"), ("bh_chinext", "sz399006")):
        res = run_backtest(BuyHold(target), start=START, end=END, capital=CAP,
                           data_dir=REPO / "data", warmup_days=340, rebalance=None)
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        n = nav["nav"].astype(float)
        bench[tag] = dict(total_return=float(n.iloc[-1] / CAP - 1),
                          max_drawdown=float((n / n.cummax() - 1).min()),
                          latest_ret=seg_stats(n, *LATEST).get("ret"),
                          latest_dd=seg_stats(n, *LATEST).get("dd"))
        nav.to_csv(OUT / f"full_{tag}_nav.csv")

    # segment + yearly tables ----------------------------------------------
    seg_rows, year_rows = [], []
    for tag, nav in navs.items():
        n = nav["nav"].astype(float)
        for name, s, e in SEGS:
            st = seg_stats(n, s, e)
            if st:
                seg_rows.append(dict(tag=tag, segment=name, start=s, end=e, **st))
        df = pd.DataFrame({"nav": n})
        df["prev"] = df["nav"].shift(1); df.iloc[0, df.columns.get_loc("prev")] = CAP
        df["year"] = [d[:4] for d in df.index]
        for y, g in df.groupby("year"):
            year_rows.append(dict(tag=tag, year=y,
                                  ret=float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1),
                                  dd=float((g["nav"] / g["nav"].cummax() - 1).min())))
    pd.DataFrame(seg_rows).to_csv(OUT / "full_segments.csv", index=False)
    pd.DataFrame(year_rows).to_csv(OUT / "full_yearly.csv", index=False)

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "full_summary.csv", index=False)
    (OUT / "full_summary.json").write_text(json.dumps(
        {"window": [START, END], "benchmarks": bench, "candidates": rows,
         "segments": SEGS, "latest_window": LATEST}, indent=1, default=str,
        ensure_ascii=False), encoding="utf-8")
    print("\nbenchmarks:", json.dumps(bench, indent=1))
    print("wrote", OUT / "full_summary.csv")


if __name__ == "__main__":
    main()
