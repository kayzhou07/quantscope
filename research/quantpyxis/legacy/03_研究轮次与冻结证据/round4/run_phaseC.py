# -*- coding: utf-8 -*-
"""Round 4 Phase C: risk-overlay variants on the FULL window (2014-07-01..2026-10-19).

The overlay parameters are a priori (dd fuse 10/15 or 12/20; crash brake -5%/-8%, 10 sessions)
and were NOT tuned on the 2024-09+ segment.  Reported as full-window + six user-named segments.
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

REPO = V.REPO
from engine.api import run_backtest  # noqa: E402

OUT = HERE / "out"
START, END = PB.START, PB.END
CAP = 100_000.0

EXP100_30 = {"B": 1.0, "N": .30, "E": 0, "C": 0}
EXP100_60 = {"B": 1.0, "N": .60, "E": 0, "C": 0}
B = dict(state_mode="frozen", top_n=10, buffer=20, max_vol=0.55,
         score_mode="base", afford="drop")
T15 = dict(B, top_n=15, exposure=EXP100_30)

SPECS = [
    ("top15_fuse_10_15", T15, dict(dd_fuse=(.10, .15))),
    ("top15_fuse_12_20", T15, dict(dd_fuse=(.12, .20))),
    ("top15_crash", T15, dict(crash_brake=(-.05, -.08, 10))),
    ("top15_fuse_crash", T15, dict(dd_fuse=(.10, .15), crash_brake=(-.05, -.08, 10))),
    ("b100n30_fuse_10_15", dict(B, exposure=EXP100_30), dict(dd_fuse=(.10, .15))),
    ("b100n30_fuse_crash", dict(B, exposure=EXP100_30),
     dict(dd_fuse=(.10, .15), crash_brake=(-.05, -.08, 10))),
    ("b100n60_fuse_10_15", dict(B, exposure=EXP100_60), dict(dd_fuse=(.10, .15))),
    ("b100n60_fuse_crash", dict(B, exposure=EXP100_60),
     dict(dd_fuse=(.10, .15), crash_brake=(-.05, -.08, 10))),
    ("lowvol_fuse_crash", dict(B, exposure=EXP100_30, score_mode="lowvol"),
     dict(dd_fuse=(.10, .15), crash_brake=(-.05, -.08, 10))),
    ("top15_vt20", dict(T15, vol_target=.20), {}),
    ("top15_vt20_fuse", dict(T15, vol_target=.20), dict(dd_fuse=(.10, .15))),
]


def main():
    from overlays import OverlayVariant
    OUT.mkdir(parents=True, exist_ok=True)
    labels = PB.load_regime()
    rows, navs = [], {}
    print(f"{'tag':22s} {'ret':>8s} {'maxDD':>8s} {'sharpe':>7s} {'2024-09+':>9s} "
          f"{'oosDD':>8s} {'expo':>6s} {'trades':>6s} {'fees':>7s} {'sec':>5s}")
    for tag, kw, ov in SPECS:
        strat = OverlayVariant(name=f"R4C-{tag}", **kw, **ov)
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
                   elapsed_s=round(el, 1))
        rows.append(row)
        print(f"{tag:22s} {row['total_return']:8.4f} {row['max_drawdown']:8.4f} "
              f"{row['sharpe']:7.2f} {row['latest_ret']:9.4f} {row['latest_dd']:8.4f} "
              f"{row['avg_exposure']:6.3f} {row['trade_count']:6d} {row['total_fees']:7.0f} {el:5.1f}")

    seg_rows = []
    for tag, nav in navs.items():
        n = nav["nav"].astype(float)
        for name, s, e in PB.SEGS:
            st = PB.seg_stats(n, s, e)
            if st:
                seg_rows.append(dict(tag=tag, segment=name, start=s, end=e, **st))
    pd.DataFrame(seg_rows).to_csv(OUT / "phaseC_segments.csv", index=False)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "phaseC_summary.csv", index=False)
    (OUT / "phaseC_summary.json").write_text(json.dumps(rows, indent=1, default=str),
                                             encoding="utf-8")
    print("\nwrote", OUT / "phaseC_summary.csv")


if __name__ == "__main__":
    main()
