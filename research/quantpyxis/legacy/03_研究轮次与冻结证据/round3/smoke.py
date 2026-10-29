# -*- coding: utf-8 -*-
"""Smoke test: engine + data + cache load for the winrate round-3 backtest."""
import sys, time
from pathlib import Path

REPO = Path(r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416")
sys.path.insert(0, str(REPO))

import pandas as pd
from engine.api import run_backtest, print_summary

t0 = time.time()
res = run_backtest({"name": "cash_smoke", "on_init": lambda ctx: None},
                   start="2014-07-01", end="2023-12-29", capital=100_000.0,
                   data_dir=REPO / "data", warmup_days=340, rebalance=None,
                   benchmark="sh000300")
print("elapsed", round(time.time() - t0, 1))
print("start/end", res["start"], res["end"])
print("metrics total_return", res["metrics"]["total_return"])
nav = res["nav"]
print("nav rows", len(nav), "first", nav["date"].iloc[0], "last", nav["date"].iloc[-1])
print("data_version", res.get("data_version"))
reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
print("regime rows", len(reg), "range", reg["date"].iloc[0], reg["date"].iloc[-1])
print(reg["state"].value_counts().to_dict())
