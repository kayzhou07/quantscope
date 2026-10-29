# -*- coding: utf-8 -*-
"""Round 4 Phase G: final DD-budget tuning around the aggressive endgame."""
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
B = dict(state_mode="frozen", top_n=20, buffer=20, max_vol=None,
         score_mode="base", afford="drop", exposure={"B": 1.0, "N": .80, "E": 0, "C": 0})

SPECS = [
    ("n80_top20_novolcap", OverlayVariant, dict(B), {}),
    ("n80_top20", OverlayVariant, dict(B, max_vol=.55), {}),
    ("n100_top20_novolcap_crash", OverlayVariant,
     dict(B, exposure={"B": 1.0, "N": 1.0, "E": 0, "C": 0}),
     dict(crash_brake=(-.05, -.08, 10))),
    ("n90_top20_novolcap", OverlayVariant,
     dict(B, exposure={"B": 1.0, "N": .90, "E": 0, "C": 0}), {}),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = PB.load_regime()
    rows = []
    print(f"{'tag':28s} {'ret':>8s} {'maxDD':>8s} {'sharpe':>7s} {'2024-09+':>9s} "
          f"{'oosDD':>8s} {'2025':>7s} {'2026':>7s} {'expo':>6s} {'trades':>6s} {'sec':>5s}")
    for tag, cls, kw, ov in SPECS:
        strat = cls(name=f"R4G-{tag}", **kw, **ov)
        t0 = time.time()
        res = run_backtest(strat, start=START, end=END, capital=CAP,
                           data_dir=REPO / "data", warmup_days=340, rebalance="M",
                           benchmark=["sh000300", "sz399006"], slippage_bps=10.0,
                           commission_bps=2.5, min_commission=5.0,
                           regime_labels=labels, name=strat.name)
        el = time.time() - t0
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        nav.to_csv(OUT / f"full_{tag}_nav.csv")
        n = nav["nav"].astype(float)
        dr = n.pct_change().fillna(0.0)
        latest = PB.seg_stats(n, *PB.LATEST)
        d = pd.DataFrame({"nav": n}); d["prev"] = d["nav"].shift(1)
        d.iloc[0, d.columns.get_loc("prev")] = CAP
        d["year"] = [x[:4] for x in d.index]
        yr = {y: float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1) for y, g in d.groupby("year")}
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
        print(f"{tag:28s} {row['total_return']:8.4f} {row['max_drawdown']:8.4f} "
              f"{row['sharpe']:7.2f} {row['latest_ret']:9.4f} {row['latest_dd']:8.4f} "
              f"{(row['y2025'] or 0):7.3f} {(row['y2026'] or 0):7.3f} "
              f"{row['avg_exposure']:6.3f} {row['trade_count']:6d} {el:5.1f}")
    pd.DataFrame(rows).to_csv(OUT / "phaseG_summary.csv", index=False)
    (OUT / "phaseG_summary.json").write_text(json.dumps(rows, indent=1, default=str),
                                             encoding="utf-8")
    print("\nwrote", OUT / "phaseG_summary.csv")


if __name__ == "__main__":
    main()
