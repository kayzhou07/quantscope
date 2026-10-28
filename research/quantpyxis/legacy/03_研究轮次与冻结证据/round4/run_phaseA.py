# -*- coding: utf-8 -*-
"""Round 4 Phase A: controlled variant sweep on the KNOWN history 2014-07-01..2023-12-29.

Every variant changes one thing versus V_base (which must reproduce round-3 S0 76.21% /
-12.65%).  Results are registered regardless of outcome; the top candidates go to Phase B
(full window incl. 2024-09+).  All runs use 100k, 10bp slippage, commission 2.5bp, stamp tax
by date, T+1 open fills, 100-share lots.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import variants as V  # noqa: E402

REPO = V.REPO
from engine.api import run_backtest  # noqa: E402

OUT = HERE / "out"
START, END = "2014-07-01", "2023-12-29"
CAP = 100_000.0
STATE_MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}

BASE = dict(state_mode="frozen", top_n=10, buffer=20, max_vol=0.55,
            score_mode="base", afford="drop")
SPECS = [
    # tag, kwargs, rebalance
    ("V_base", dict(BASE), "M"),
    ("exp_b80_n30", dict(BASE, exposure={"B": .80, "N": .30, "E": 0, "C": 0}), "M"),
    ("exp_b100_n25", dict(BASE, exposure={"B": 1.0, "N": .25, "E": 0, "C": 0}), "M"),
    ("exp_b100_n30", dict(BASE, exposure={"B": 1.0, "N": .30, "E": 0, "C": 0}), "M"),
    ("exp_b100_n50", dict(BASE, exposure={"B": 1.0, "N": .50, "E": 0, "C": 0}), "M"),
    ("exp_b60_n50", dict(BASE, exposure={"B": .60, "N": .50, "E": 0, "C": 0}), "M"),
    ("top5", dict(BASE, top_n=5), "M"),
    ("top15", dict(BASE, top_n=15), "M"),
    ("buffer5", dict(BASE, buffer=5), "M"),
    ("buffer1", dict(BASE, buffer=1), "M"),
    ("aff_backfill", dict(BASE, afford="backfill"), "M"),
    ("aff_fees", dict(BASE, afford="fees"), "M"),
    ("quarterly", dict(BASE), "Q"),
    ("novolcap", dict(BASE, max_vol=None), "M"),
    ("score_equal", dict(BASE, score_mode="equal"), "M"),
    ("score_lowvol", dict(BASE, score_mode="lowvol"), "M"),
    ("state_none_100", dict(BASE, state_mode="none", full=1.0), "M"),
    ("state_none_50", dict(BASE, state_mode="none", full=0.5), "M"),
    ("state_ma200_100", dict(BASE, state_mode="ma200", full=1.0), "M"),
    ("state_dualma_100", dict(BASE, state_mode="dual_ma", full=1.0, neutral=.30), "M"),
    ("state_dualma_60", dict(BASE, state_mode="dual_ma", full=.60, neutral=.30), "M"),
]


def load_regime():
    reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    return {d: STATE_MAP[s] for d, s in zip(reg["date"], reg["state"])}


def day_stats(res):
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    n = nav["nav"].astype(float)
    prev = n.shift(1)
    prev.iloc[0] = CAP
    pnl = n - prev
    pos = pnl > 1e-6
    neg = pnl < -1e-6
    flat = ~(pos | neg)
    npos = nav["n_positions"].astype(int)
    pprev = npos.shift(1).fillna(0).astype(int)
    risk = (npos > 0) | (pprev > 0)
    dd = n / n.cummax() - 1.0
    nonzero = int(pos.sum() + neg.sum())
    return dict(
        days=len(n), positive=int(pos.sum()), negative=int(neg.sum()), flat=int(flat.sum()),
        risk_days=int(risk.sum()), risk_positive=int((risk & pos).sum()),
        winrate_all=float(pos.sum() / len(n)),
        winrate_risk=float((risk & pos).sum() / risk.sum()) if risk.sum() else None,
        winrate_nonzero=float(pos.sum() / nonzero) if nonzero else None,
        daily_pf=float(pnl[pos].sum() / -pnl[neg].sum()) if neg.sum() else None,
        max_drawdown=float(dd.min()),
        dd_trough=str(dd.idxmin()),
        all_cash_days=int((nav["market_value"].astype(float) <= .01).sum()),
        avg_exposure=float(nav["exposure"].astype(float).mean()),
        avg_positions=float(npos.mean()), max_positions=int(npos.max()),
    )


def run_one(tag, kw, rebalance, labels):
    strat = V.Variant(name=f"R4-{tag}", **kw)
    t0 = time.time()
    res = run_backtest(strat, start=START, end=END, capital=CAP,
                       data_dir=REPO / "data", warmup_days=340,
                       rebalance=rebalance, benchmark=["sh000300", "sz399006"],
                       slippage_bps=10.0, commission_bps=2.5, min_commission=5.0,
                       regime_labels=labels, name=strat.name)
    el = time.time() - t0
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    s = res["summary"]
    m = res["metrics"]
    row = dict(tag=tag, rebalance=rebalance, elapsed_s=round(el, 1),
               total_return=float(s["total_return"]),
               cagr=float(m.get("cagr", np.nan)),
               ann_vol=float(m.get("ann_vol", np.nan)),
               sharpe=float(m.get("sharpe", np.nan)),
               sortino=float(m.get("sortino", np.nan)),
               calmar=float(m.get("calmar", np.nan)),
               trade_count=int(s["trade_count"]), total_fees=float(s["total_fees"]),
               turnover_annual=float(s["turnover_annual"]),
               fifo_win_rate=float(m.get("fifo_win_rate", np.nan)),
               fifo_profit_factor=float(m.get("fifo_profit_factor", np.nan)))
    row.update(day_stats(res))
    row["settings"] = json.dumps({k: v for k, v in kw.items()}, default=str)
    return res, strat, row


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = load_regime()
    rows, keep = [], {}
    print(f"{'tag':18s} {'ret':>8s} {'maxDD':>8s} {'sharpe':>7s} {'expo':>6s} "
          f"{'cashd':>6s} {'pos':>5s} {'trades':>6s} {'fees':>7s} {'win_risk':>8s} {'sec':>5s}")
    for tag, kw, rb in SPECS:
        res, strat, row = run_one(tag, kw, rb, labels)
        rows.append(row)
        nav = res["nav"].copy(); nav.index = nav.index.astype(str)
        nav.to_csv(OUT / f"{tag}_nav.csv")
        keep[tag] = row
        print(f"{tag:18s} {row['total_return']:8.4f} {row['max_drawdown']:8.4f} "
              f"{row['sharpe']:7.2f} {row['avg_exposure']:6.3f} {row['all_cash_days']:6d} "
              f"{row['avg_positions']:5.2f} {row['trade_count']:6d} {row['total_fees']:7.0f} "
              f"{(row['winrate_risk'] or 0):8.4f} {row['elapsed_s']:5.1f}")
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "phaseA_summary.csv", index=False)
    (OUT / "phaseA_summary.json").write_text(
        json.dumps({"window": [START, END], "capital": CAP,
                    "sha256_variants_py": hashlib.sha256((HERE / "variants.py").read_bytes()).hexdigest(),
                    "variants": rows}, indent=1, default=str, ensure_ascii=False),
        encoding="utf-8")
    print("\nwrote", OUT / "phaseA_summary.csv")
    print("base check (round3 S0 = 0.762105 / -0.126545):",
          keep["V_base"]["total_return"], keep["V_base"]["max_drawdown"])


if __name__ == "__main__":
    main()
