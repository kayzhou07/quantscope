# -*- coding: utf-8 -*-
"""Round 4 cost-stress runner (written after the fact for provenance).

Reproduces the two stress rows:
  - b100_n30_top15 with the plain Variant (month-end rebalancing only)
  - n90_top20_novolcap with OverlayVariant (no overlay active)
at 20bp slippage + 5bp commission, writes out/phase_stress_summary.csv/.json.
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
EXP100_30 = {"B": 1.0, "N": .30, "E": 0, "C": 0}
EXP100_90 = {"B": 1.0, "N": .90, "E": 0, "C": 0}
SPECS = [
    ("b100_n30_top15", V.Variant,
     dict(state_mode="frozen", top_n=15, buffer=20, max_vol=.55, score_mode="base",
          afford="drop", exposure=EXP100_30)),
    ("n90_top20_novolcap", OverlayVariant,
     dict(state_mode="frozen", top_n=20, buffer=20, max_vol=None, score_mode="base",
          afford="drop", exposure=EXP100_90)),
]


def run_one(tag, cls, kw, slip, comm):
    st = cls(name=f"R4S-{tag}", **kw)
    res = run_backtest(st, start=START, end=END, capital=CAP, data_dir=REPO / "data",
                       warmup_days=340, rebalance="M", benchmark=["sh000300"],
                       slippage_bps=slip, commission_bps=comm, min_commission=5.0,
                       regime_labels=PB.load_regime(), name=st.name)
    nav = res["nav"].copy(); nav.index = nav.index.astype(str)
    n = nav["nav"].astype(float)
    dr = n.pct_change().fillna(0.0)
    latest = PB.seg_stats(n, *PB.LATEST)
    return dict(total_return=float(n.iloc[-1] / CAP - 1),
                max_drawdown=float((n / n.cummax() - 1).min()),
                sharpe=float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else None,
                total_fees=float(res["total_fees"]),
                latest_ret=latest.get("ret"), latest_dd=latest.get("dd"))


def main():
    rows = []
    for tag, cls, kw in SPECS:
        base = run_one(tag, cls, kw, 10.0, 2.5)
        stress = run_one(tag, cls, kw, 20.0, 5.0)
        rows.append(dict(tag=tag, stress="slippage20bp_commission5bp",
                         base_return=base["total_return"], base_dd=base["max_drawdown"],
                         base_sharpe=base["sharpe"],
                         total_return=stress["total_return"],
                         max_drawdown=stress["max_drawdown"], sharpe=stress["sharpe"],
                         total_fees=stress["total_fees"], latest_ret=stress["latest_ret"],
                         latest_dd=stress["latest_dd"]))
    pd.DataFrame(rows).to_csv(OUT / "phase_stress_summary.csv", index=False)
    (OUT / "phase_stress_summary.json").write_text(
        json.dumps(rows, indent=1, default=str), encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
