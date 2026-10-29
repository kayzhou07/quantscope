# -*- coding: utf-8 -*-
"""Digest of round-3 outputs for report writing."""
import json
from pathlib import Path
import pandas as pd

OUT = Path(__file__).resolve().parent / "out"
m = json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))
v = m["variants"]

print("=== headline ===")
for tag in ("S0", "S1", "S1b", "S0_20bp", "S1_stress"):
    x = v[tag]
    print(f"{tag:10s} ret={x['total_return']:+.4f} dd={x['max_drawdown']:.4f} "
          f"cagr={x['cagr']:+.4f} vol={x['ann_vol']:.4f} sharpe={x['sharpe']:.2f} "
          f"win_all={x['winrate_all']:.4f} win_risk={x['winrate_risk']:.4f} "
          f"win_nz={x['winrate_nonzero']:.4f} pf={x['daily_profit_factor']:.3f}")

print("\n=== cash reasons S0 / S1 ===")
print("S0", v["S0"]["cash_reason_counts"])
print("S1", v["S1"]["cash_reason_counts"])

print("\n=== fifo / exposure / audit counters ===")
for tag in ("S0", "S1", "S1b"):
    x = v[tag]
    print(tag, {k: x[k] for k in ("fifo_win_rate", "fifo_win_count", "fifo_loss_count",
                                  "fifo_avg_win", "fifo_avg_loss", "fifo_profit_factor",
                                  "fifo_realized_pnl", "fifo_unrealized_pnl",
                                  "avg_positions", "max_positions", "all_cash_days",
                                  "risk_flat_days", "unaffordable_drops_total",
                                  "rebalance_count", "blocked_sell_days", "blocked_buy_days",
                                  "trade_count", "total_fees", "turnover_annual")})

print("\n=== rebalances S0 / S1 ===")
rb = pd.read_csv(OUT / "REBALANCES.csv", dtype={"date": str})
for tag in ("S0", "S1", "S1b"):
    sub = rb[rb["tag"] == tag]
    print(tag, "n_rebal", len(sub),
          "n_selected dist", sub["n_selected"].value_counts().sort_index().to_dict(),
          "total drops", int(sub["n_unaffordable"].fillna(0).sum()),
          "notes", sub["note"].value_counts().to_dict())

print("\n=== daily audit checks ===")
for tag in ("S0", "S1"):
    d = pd.read_csv(OUT / f"DAILY_AUDIT_{tag}.csv", index_col=0)
    print(tag, "days", len(d), "sum positive", int(d.positive.sum()),
          "sum negative", int(d.negative.sum()), "flat", int(d.flat.sum()),
          "risk", int(d.risk_day.sum()), "risk_positive", int((d.risk_day & d.positive).sum()),
          "pnl sum", round(float(d.daily_pnl.sum()), 2),
          "nav_end", round(float(d.nav.iloc[-1]), 2))

print("\n=== risk flat audit ===")
for tag in ("S0", "S1"):
    rf = pd.read_csv(OUT / f"RISK_FLAT_AUDIT_{tag}.csv")
    print(tag, len(rf), rf["classification"].value_counts().to_dict())
    if len(rf):
        print(rf[["date", "n_positions", "prev_n_positions", "n_held", "n_no_bar",
                  "classification"]].to_string(index=False))

print("\n=== holdings final ===")
for tag in ("S0", "S1"):
    res = json.loads((OUT / f"{tag}_result.json").read_text(encoding="utf-8"))
    print(tag, "final nav", res.get("final_nav"), "holdings", res.get("holdings_final"))
    s = res.get("settings", {})
    print("  settings:", {k: s.get(k) for k in ("commission_bps", "slippage_bps",
                                               "min_commission", "allow_st_buy",
                                               "max_pending_days", "warmup_days")})
