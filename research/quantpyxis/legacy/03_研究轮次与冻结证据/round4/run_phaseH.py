# -*- coding: utf-8 -*-
"""Round 4 Phase H: n85/n90 exposure micro-tuning (written after the fact for provenance).

This file reproduces the Phase-H runs that were originally executed inline:
  n85_top20_novolcap, n90_top20, n90_top15_novolcap, n85_top15_novolcap
Configs and outputs match out/phaseH_summary.json and out/full_*_nav.csv.
"""
from __future__ import annotations

import json
import sys
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
         score_mode="base", afford="drop",
         exposure={"B": 1.0, "N": .85, "E": 0, "C": 0})
SPECS = [
    ("n85_top20_novolcap", dict(B)),
    ("n90_top20", dict(B, exposure={"B": 1.0, "N": .90, "E": 0, "C": 0}, max_vol=.55)),
    ("n90_top15_novolcap", dict(B, exposure={"B": 1.0, "N": .90, "E": 0, "C": 0}, top_n=15)),
    ("n85_top15_novolcap", dict(B, top_n=15)),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = PB.load_regime()
    rows = []
    for tag, kw in SPECS:
        st = OverlayVariant(name=f"R4H-{tag}", **kw)
        res = run_backtest(st, start=START, end=END, capital=CAP, data_dir=REPO / "data",
                           warmup_days=340, rebalance="M", benchmark=["sh000300", "sz399006"],
                           slippage_bps=10.0, commission_bps=2.5, min_commission=5.0,
                           regime_labels=labels, name=st.name)
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        nav.to_csv(OUT / f"full_{tag}_nav.csv")
        n = nav["nav"].astype(float)
        dr = n.pct_change().fillna(0.0)
        latest = PB.seg_stats(n, *PB.LATEST)
        d = pd.DataFrame({"nav": n}); d["prev"] = d["nav"].shift(1)
        d.iloc[0, d.columns.get_loc("prev")] = CAP
        d["year"] = [x[:4] for x in d.index]
        yr = {y: float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1) for y, g in d.groupby("year")}
        rows.append(dict(tag=tag, total_return=float(n.iloc[-1] / CAP - 1),
                         max_drawdown=float((n / n.cummax() - 1).min()),
                         cagr=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1),
                         ann_vol=float(dr.std() * np.sqrt(252)),
                         sharpe=float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else None,
                         avg_exposure=float(nav["exposure"].astype(float).mean()),
                         all_cash_days=int((nav["market_value"].astype(float) <= .01).sum()),
                         trade_count=int(res["metrics"].get("trade_count", 0)),
                         total_fees=float(res["total_fees"]),
                         latest_ret=latest.get("ret"), latest_dd=latest.get("dd"),
                         y2024=yr.get("2024"), y2025=yr.get("2025"), y2026=yr.get("2026")))
        print(tag, rows[-1]["total_return"], rows[-1]["max_drawdown"])
    pd.DataFrame(rows).to_csv(OUT / "phaseH_summary.csv", index=False)
    (OUT / "phaseH_summary.json").write_text(json.dumps(rows, indent=1, default=str),
                                             encoding="utf-8")


if __name__ == "__main__":
    main()
