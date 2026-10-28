# -*- coding: utf-8 -*-
"""Debug: why does S1 carry ~30 positions?"""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s0s1
REPO = s0s1.REPO
from engine.api import run_backtest
import pandas as pd

reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
m = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}
labels = {d: m[s] for d, s in zip(reg["date"], reg["state"])}

strat = s0s1.PlatformPort(mode="S1", name="dbg-S1")
res = run_backtest(strat, start="2019-01-01", end="2020-12-31", capital=100_000.0,
                   data_dir=REPO / "data", warmup_days=340, rebalance="M",
                   benchmark=["sh000300"], slippage_bps=10.0,
                   regime_labels=labels, name=strat.name)
pos = res["positions"]
print("pos cols", list(pos.columns), "rows", len(pos))
last = pos[pos["date"] == pos["date"].max()]
print(last[["date", "code", "shares", "price", "value"]].to_string())
print("\nn_positions over time (last 10):")
print(res["nav"]["n_positions"].tail(10).to_string())
print("\nevents types:", res["events"]["type"].value_counts().to_dict())
ev = res["events"]
print(ev[ev["type"].astype(str).str.contains("target|block", na=False)].head(20).to_string())
print("\nlast audit day positions:", len(strat.audit_days[-1]["positions"]))
print(strat.audit_days[-1]["positions"][:12])
