# -*- coding: utf-8 -*-
"""Inspect engine result structure (no backtest side effects)."""
import sys, time
from pathlib import Path
REPO = Path(r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416")
sys.path.insert(0, str(REPO))
from engine.api import run_backtest

t0 = time.time()
res = run_backtest({"name": "cash_smoke2", "on_init": lambda ctx: None},
                   start="2014-01-01", end="2026-10-19", capital=100_000.0,
                   data_dir=REPO / "data", warmup_days=340, rebalance=None)
print("elapsed", round(time.time() - t0, 1))
print("keys", sorted(res.keys()))
nav = res["nav"]
print("nav type", type(nav), getattr(nav, "columns", None) is not None)
print("nav index name", nav.index.name, "len", len(nav))
print(nav.head(3).to_string())
print("summary keys", sorted(res["summary"].keys())[:40])
print("metrics count", len(res["metrics"]))
print("trades cols", list(res["trades"].columns) if res.get("trades") is not None else None)
print("orders cols", list(res["orders"].columns) if res.get("orders") is not None else None)
print("events cols", list(res["events"].columns) if res.get("events") is not None else None)
print("data_version", res.get("data_version"))
