# -*- coding: utf-8 -*-
"""Debug: trace where the extra S1 positions come from."""
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

strat = s0s1.PlatformPort(mode="S1", name="dbg-S1b")
res = run_backtest(strat, start="2019-01-01", end="2020-12-31", capital=100_000.0,
                   data_dir=REPO / "data", warmup_days=340, rebalance="M",
                   benchmark=["sh000300"], slippage_bps=10.0,
                   regime_labels=labels, name=strat.name)
pos = res["positions"]
last = pos[pos["date"] == pos["date"].max()]
print("last-date positions:", len(last))
tr = res["trades"].copy()
for code in last["code"].tolist():
    sub = tr[tr["code"] == code]
    print("\n==", code, "last shares", float(last[last.code == code]["shares"].iloc[0]))
    print(sub[["date", "side", "shares", "amount", "reason", "note"]].tail(6).to_string(index=False))
print("\norders (explicit):")
od = res["orders"]
print(od["status"].value_counts().to_dict(), od["side"].value_counts().to_dict())
print(od.tail(15).to_string())
