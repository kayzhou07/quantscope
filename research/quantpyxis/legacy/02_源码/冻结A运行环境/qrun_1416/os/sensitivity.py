# -*- coding: utf-8 -*-
"""Parameter sensitivity grid for the final V3 system (full disclosure, no cherry-picking).

Runs the same engine over 2014-01-02..2026-10-19 while changing ONE frozen parameter at a
time from the V3 baseline (top10, liquidity 200M, vol cap 55%, quarterly, BULL 95% /
NEUTRAL 30%).  Output: os/results/sensitivity.csv (+ os/results/tables/sensitivity.md).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

from engine.api import run_backtest  # noqa: E402


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "os" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_final = _load("os_strategy_final_sens", "strategy_final.py")
V3 = _final.BullBearOSv3
reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
LABELS = dict(zip(reg["date"], reg["state"]))

BASE = dict(top_n=10, min_amount=2.0e8, max_vol=0.55,
            exposure={"BULL": 0.95, "NEUTRAL": 0.30, "BEAR": 0.0, "CRASH": 0.0})
REBAL = "Q"
CASES = [
    ("baseline", {}, REBAL),
    ("top_n=8", {"top_n": 8}, REBAL),
    ("top_n=12", {"top_n": 12}, REBAL),
    ("top_n=15", {"top_n": 15}, REBAL),
    ("liq=1e8", {"min_amount": 1.0e8}, REBAL),
    ("liq=3e8", {"min_amount": 3.0e8}, REBAL),
    ("liq=5e8", {"min_amount": 5.0e8}, REBAL),
    ("vol_cap=none", {"max_vol": None}, REBAL),
    ("vol_cap=0.45", {"max_vol": 0.45}, REBAL),
    ("vol_cap=0.65", {"max_vol": 0.65}, REBAL),
    ("bull=0.90", {"exposure": {"BULL": 0.90, "NEUTRAL": 0.30, "BEAR": 0.0, "CRASH": 0.0}}, REBAL),
    ("bull=1.00", {"exposure": {"BULL": 1.00, "NEUTRAL": 0.30, "BEAR": 0.0, "CRASH": 0.0}}, REBAL),
    ("neutral=0.20", {"exposure": {"BULL": 0.95, "NEUTRAL": 0.20, "BEAR": 0.0, "CRASH": 0.0}}, REBAL),
    ("neutral=0.45", {"exposure": {"BULL": 0.95, "NEUTRAL": 0.45, "BEAR": 0.0, "CRASH": 0.0}}, REBAL),
    ("rebalance=monthly", {}, "M"),
    ("buffer=1", {"buffer_mult": 1}, REBAL),
    ("buffer=3", {"buffer_mult": 3}, REBAL),
]


def main():
    rows = []
    for name, changes, rb in CASES:
        cfg = dict(BASE)
        cfg.update(changes)
        strat = V3(name=f"sens_{name}", **cfg)
        rb2 = rb
        try:
            res = run_backtest(strat, start="2014-01-01", end="2026-10-19",
                               capital=100000, data_dir=REPO / "data", rebalance=rb2,
                               benchmark=["sh000300", "sz399006"], warmup_days=340,
                               regime_labels=LABELS, name=f"sens_{name}")
            s = res["summary"]
            rows.append({"case": name, "rebalance": rb,
                         "total_return": s["total_return"], "cagr": s["cagr"],
                         "ann_vol": s["ann_vol"], "sharpe": s["sharpe"],
                         "max_drawdown": s["max_drawdown"],
                         "avg_exposure": s["avg_exposure"],
                         "trade_count": s["trade_count"], "total_fees": s["total_fees"]})
            print(f"{name:22s} ret={s['total_return']:+8.2%} cagr={s['cagr']:+6.2%} "
                  f"maxDD={s['max_drawdown']:7.2%} sharpe={s['sharpe']:5.2f} "
                  f"exp={s['avg_exposure']:5.1%} trades={s['trade_count']:4d}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(name, "FAILED", type(e).__name__, e, flush=True)
    df = pd.DataFrame(rows)
    out = REPO / "os" / "results" / "sensitivity.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
