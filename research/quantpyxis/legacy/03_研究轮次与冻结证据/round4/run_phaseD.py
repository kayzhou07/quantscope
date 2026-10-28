# -*- coding: utf-8 -*-
"""Round 4 Phase D: exposure endgame + fast regime re-entry, full window.

Motivated by the 2024-09+ miss: the frozen machine stayed at N (30%) through most of the
2024-2025 bull and did not re-enter between month-ends.  Phase C showed the account-drawdown
fuse deadlocks (permanent cash), so it is dropped here.
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
import run_phaseB as PB  # noqa: E402
from overlays import OverlayVariant  # noqa: E402

REPO = V.REPO
from engine.api import run_backtest  # noqa: E402

OUT = HERE / "out"
START, END = PB.START, PB.END
CAP = 100_000.0
EXP100_30 = {"B": 1.0, "N": .30, "E": 0, "C": 0}
EXP100_60 = {"B": 1.0, "N": .60, "E": 0, "C": 0}
EXP100_100 = {"B": 1.0, "N": 1.0, "E": 0, "C": 0}
EXP100_50 = {"B": 1.0, "N": .50, "E": 0, "C": 0}
B = dict(state_mode="frozen", top_n=10, buffer=20, max_vol=0.55,
         score_mode="base", afford="drop")
T15 = dict(B, top_n=15, exposure=EXP100_30)


class FastReentry(OverlayVariant):
    """Frozen-state variants that may re-enter on the first B/N day, not only month-end."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fast_reentry = True

    def on_bar(self, ctx):
        prev = self._prev_exp
        super().on_bar(ctx)
        cur = self._prev_exp
        if prev <= 0 and cur > 0 and self._last_reb != str(ctx.date) \
                and not ctx.is_rebalance_day:
            self._rebalance(ctx, cur, "fast_reentry")


SPECS = [
    # tag, class, kwargs, overlay kwargs
    ("b100n50", OverlayVariant, dict(B, exposure=EXP100_50), {}),
    ("b100n60", OverlayVariant, dict(B, exposure=EXP100_60), {}),
    ("b100n100", OverlayVariant, dict(B, exposure=EXP100_100), {}),
    ("b100n60_top15", OverlayVariant, dict(B, exposure=EXP100_60, top_n=15), {}),
    ("b100n100_top15", OverlayVariant, dict(B, exposure=EXP100_100, top_n=15), {}),
    ("none_100_crash", OverlayVariant, dict(B, state_mode="none", full=1.0),
     dict(crash_brake=(-.05, -.08, 10))),
    ("none_60_crash", OverlayVariant, dict(B, state_mode="none", full=.6),
     dict(crash_brake=(-.05, -.08, 10))),
    ("b100n30_top15_crash", OverlayVariant, dict(B, exposure=EXP100_30, top_n=15),
     dict(crash_brake=(-.05, -.08, 10))),
    ("top15_fast", FastReentry, dict(T15), {}),
    ("b100n30_fast", FastReentry, dict(B, exposure=EXP100_30), {}),
    ("b100n60_top15_fast", FastReentry, dict(B, exposure=EXP100_60, top_n=15), {}),
    ("b100n100_top15_fast", FastReentry, dict(B, exposure=EXP100_100, top_n=15), {}),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = PB.load_regime()
    rows, navs = [], {}
    print(f"{'tag':22s} {'ret':>8s} {'maxDD':>8s} {'sharpe':>7s} {'2024-09+':>9s} "
          f"{'oosDD':>8s} {'2024':>7s} {'2025':>7s} {'2026':>7s} {'expo':>6s} {'sec':>5s}")
    for tag, cls, kw, ov in SPECS:
        strat = cls(name=f"R4D-{tag}", **kw, **ov)
        t0 = time.time()
        res = run_backtest(strat, start=START, end=END, capital=CAP,
                           data_dir=REPO / "data", warmup_days=340, rebalance="M",
                           benchmark=["sh000300", "sz399006"], slippage_bps=10.0,
                           commission_bps=2.5, min_commission=5.0,
                           regime_labels=labels, name=strat.name)
        el = time.time() - t0
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        navs[tag] = nav
        nav.to_csv(OUT / f"full_{tag}_nav.csv")
        n = nav["nav"].astype(float)
        dr = n.pct_change().fillna(0.0)
        latest = PB.seg_stats(n, *PB.LATEST)
        yr = {}
        d = pd.DataFrame({"nav": n})
        d["prev"] = d["nav"].shift(1); d.iloc[0, d.columns.get_loc("prev")] = CAP
        d["year"] = [x[:4] for x in d.index]
        for y, g in d.groupby("year"):
            yr[y] = float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1)
        row = dict(tag=tag, total_return=float(n.iloc[-1] / CAP - 1),
                   max_drawdown=float((n / n.cummax() - 1).min()),
                   cagr=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1),
                   ann_vol=float(dr.std() * np.sqrt(252)),
                   sharpe=float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else None,
                   avg_exposure=float(nav["exposure"].astype(float).mean()),
                   all_cash_days=int((nav["market_value"].astype(float) <= .01).sum()),
                   trade_count=int(res["metrics"].get("trade_count", 0)),
                   total_fees=float(res["total_fees"]),
                   latest_ret=latest.get("ret"), latest_dd=latest.get("dd"),
                   y2024=yr.get("2024"), y2025=yr.get("2025"), y2026=yr.get("2026"),
                   elapsed_s=round(el, 1))
        rows.append(row)
        print(f"{tag:22s} {row['total_return']:8.4f} {row['max_drawdown']:8.4f} "
              f"{row['sharpe']:7.2f} {row['latest_ret']:9.4f} {row['latest_dd']:8.4f} "
              f"{(row['y2024'] or 0):7.3f} {(row['y2025'] or 0):7.3f} {(row['y2026'] or 0):7.3f} "
              f"{row['avg_exposure']:6.3f} {el:5.1f}")

    seg_rows = []
    for tag, nav in navs.items():
        n = nav["nav"].astype(float)
        for name, s, e in PB.SEGS:
            st = PB.seg_stats(n, s, e)
            if st:
                seg_rows.append(dict(tag=tag, segment=name, start=s, end=e, **st))
    pd.DataFrame(seg_rows).to_csv(OUT / "phaseD_segments.csv", index=False)
    pd.DataFrame(rows).to_csv(OUT / "phaseD_summary.csv", index=False)
    (OUT / "phaseD_summary.json").write_text(json.dumps(rows, indent=1, default=str),
                                             encoding="utf-8")
    print("\nwrote", OUT / "phaseD_summary.csv")


if __name__ == "__main__":
    main()
