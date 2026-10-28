# -*- coding: utf-8 -*-
"""Quick functional test of the S0/S1 port on a short window."""
import sys, time, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s0s1  # noqa: E402
REPO = s0s1.REPO
from engine.api import run_backtest  # noqa: E402
import pandas as pd

reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
m = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}
labels = {d: m[s] for d, s in zip(reg["date"], reg["state"])}
print("labels", len(labels), "sample", list(labels.items())[:3])

for mode in ("S0", "S1", "S1b"):
    strat = s0s1.PlatformPort(mode=mode, name=f"test-{mode}")
    t0 = time.time()
    res = run_backtest(strat, start="2019-01-01", end="2020-12-31", capital=100_000.0,
                       data_dir=REPO / "data", warmup_days=340, rebalance="M",
                       benchmark=["sh000300"], slippage_bps=10.0,
                       regime_labels=labels, name=strat.name)
    s = res["summary"]
    print(f"[{mode}] {time.time()-t0:.1f}s ret={s['total_return']:+.4f} "
          f"dd={s['max_drawdown']:.4f} trades={s['trade_count']} fees={s['total_fees']:.1f} "
          f"exp={s['avg_exposure']:.3f} cashd={s['all_cash_days']} days={len(res['nav'])}")
    print("   rebalances", len(strat.audit_rebalances), "days", len(strat.audit_days))
    rb = strat.audit_rebalances
    if rb:
        print("   first rb:", json.dumps(rb[0], default=str))
        print("   n_selected dist:", pd.Series([r["n_selected"] for r in rb]).value_counts().to_dict())
        print("   unaffordable total:", sum(r["n_unaffordable"] for r in rb))
    print("   nav tail:", res["nav"].tail(2)[["nav", "cash", "n_positions"]].to_dict("index"))
