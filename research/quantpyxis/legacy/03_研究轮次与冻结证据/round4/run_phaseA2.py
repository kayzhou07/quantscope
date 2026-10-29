# -*- coding: utf-8 -*-
"""Round 4 Phase A2: risk overlays on the best exposure configs (train window 2014-2023)."""
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
import run_phaseA as A  # noqa: E402

OUT = HERE / "out"
BASE = dict(A.BASE)
EXP100_30 = {"B": 1.0, "N": .30, "E": 0, "C": 0}
EXP100_25 = {"B": 1.0, "N": .25, "E": 0, "C": 0}
EXP80_30 = {"B": .80, "N": .30, "E": 0, "C": 0}

SPECS = [
    ("b100n30_capma200", dict(BASE, exposure=EXP100_30, index_cap="ma200")),
    ("b100n30_vt20", dict(BASE, exposure=EXP100_30, vol_target=.20)),
    ("b100n30_vt15", dict(BASE, exposure=EXP100_30, vol_target=.15)),
    ("b80n30_capma200", dict(BASE, exposure=EXP80_30, index_cap="ma200")),
    ("b80n30_vt18", dict(BASE, exposure=EXP80_30, vol_target=.18)),
    ("base_capma200", dict(BASE, index_cap="ma200")),
    ("base_vt18", dict(BASE, vol_target=.18)),
    ("b100n30_novolcap", dict(BASE, exposure=EXP100_30, max_vol=None)),
    ("b100n30_lowvol", dict(BASE, exposure=EXP100_30, score_mode="lowvol")),
    ("b100n25_capma200", dict(BASE, exposure=EXP100_25, index_cap="ma200")),
    ("b100n30_buffer5", dict(BASE, exposure=EXP100_30, buffer=5)),
    ("b100n30_backfill", dict(BASE, exposure=EXP100_30, afford="backfill")),
    ("b100n30_top15", dict(BASE, exposure=EXP100_30, top_n=15)),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = A.load_regime()
    rows = []
    print(f"{'tag':20s} {'ret':>8s} {'maxDD':>8s} {'sharpe':>7s} {'expo':>6s} "
          f"{'cashd':>6s} {'pos':>5s} {'trades':>6s} {'fees':>7s} {'win_risk':>8s} {'sec':>5s}")
    for tag, kw in SPECS:
        res, strat, row = A.run_one(tag, kw, "M", labels)
        rows.append(row)
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        nav.to_csv(OUT / f"{tag}_nav.csv")
        print(f"{tag:20s} {row['total_return']:8.4f} {row['max_drawdown']:8.4f} "
              f"{row['sharpe']:7.2f} {row['avg_exposure']:6.3f} {row['all_cash_days']:6d} "
              f"{row['avg_positions']:5.2f} {row['trade_count']:6d} {row['total_fees']:7.0f} "
              f"{(row['winrate_risk'] or 0):8.4f} {row['elapsed_s']:5.1f}")
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "phaseA2_summary.csv", index=False)
    (OUT / "phaseA2_summary.json").write_text(json.dumps(rows, indent=1, default=str),
                                              encoding="utf-8")
    print("\nwrote", OUT / "phaseA2_summary.csv")


if __name__ == "__main__":
    main()
