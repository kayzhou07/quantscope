# -*- coding: utf-8 -*-
"""STEP 1 (round 5 / attribution): rerun frozen strategy A with full ledgers.

A = round4 variants.Variant(state_mode='frozen',
                            exposure={'B':1.0,'N':0.30,'E':0,'C':0},
                            top_n=15, buffer=20, max_vol=0.55,
                            score_mode='base', afford='drop')
    window 2014-07-01..2026-10-19, capital 100000, monthly rebalance,
    10bp slippage, 2.5bp commission (min 5 CNY), regime letters from
    qrun_1416/os/out/regime_v1.csv.

Nothing about A is changed.  A thin subclass `AttribVariant` only *observes* the
calls A makes (instrumentation), so the attribution report can map every
holding back to the most recent target book:
  * every ctx.set_targets(...) call is logged with (date, note, weights);
  * every ctx.sell(...) / ctx.buy(...) explicit order is logged with its call
    date and argument (A issues explicit full exits for names that drop out).

Outputs (round5/attrib/out/):
  a_nav.csv, a_trades.csv, a_orders.csv, a_events.csv, a_positions.csv
  a_audit_days.csv, a_audit_reb.csv, a_target_books.csv
  a_call_log.csv            (all instrumented ctx calls, in order)
  a_summary.json            (metrics + verification against published values)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ATTRIB = HERE
ROUND5 = HERE.parent
ROUND4 = ROUND5.parent / "round4"
REPO = ROUND5.parent.parent / "qrun_1416"
OUT = ATTRIB / "out"
sys.path.insert(0, str(ROUND4))
sys.path.insert(0, str(REPO))

import variants as V  # noqa: E402
from engine.api import run_backtest  # noqa: E402

START, END = "2014-07-01", "2026-10-19"
CAP = 100_000.0
STATE_MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}

PUBLISHED = {
    "total_return": 1.7456069325168508,
    "max_drawdown": -0.23979364746919452,
    "dd_peak": "2017-11-22",
    "dd_trough": "2020-03-23",
    "max_positions_observed": 17,
    "all_cash_days": 1285,
    "avg_exposure": 0.264221,
    "cagr": 0.088883,
}


def load_regime() -> dict:
    reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    return {d: STATE_MAP[s] for d, s in zip(reg["date"], reg["state"])}


class AttribVariant(V.Variant):
    """Frozen A + pure observation of the calls it makes (no rule changes)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.call_log: list = []
        self._patched = False

    def _patch(self, ctx):
        if self._patched:
            return
        self._orig = {n: getattr(ctx, n) for n in
                      ("set_targets", "sell", "buy", "close", "close_all",
                       "cancel_pending")}
        for name, orig in self._orig.items():
            def make(nm, fn):
                def wrapper(*a, **k):
                    rec = {"date": str(ctx.date), "call": nm}
                    if nm == "set_targets":
                        w = dict(a[0]) if a else dict(k.get("weights") or {})
                        rec["note"] = k.get("note", "")
                        rec["n_codes"] = len(w)
                        rec["weights"] = {str(c): float(x) for c, x in w.items()}
                    elif nm in ("sell", "buy"):
                        code = a[0] if a else k.get("code")
                        rec["code"] = str(code)
                        for key in ("shares", "fraction", "value", "weight"):
                            v = k.get(key, a[1] if len(a) > 1 and key in ("shares",)
                                      else None)
                            if v is not None:
                                rec[key] = float(v)
                        rec["note"] = k.get("note", "")
                    self.call_log.append(rec)
                    return fn(*a, **k)
                return wrapper
            setattr(ctx, name, make(name, orig))
        self._patched = True

    def on_init(self, ctx):
        self._patch(ctx)

    def on_bar(self, ctx):
        self._patch(ctx)
        super().on_bar(ctx)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels = load_regime()
    print(f"regime labels: {len(labels)} dates  range {min(labels)} .. {max(labels)}")
    strat = AttribVariant(name="A-attr", state_mode="frozen",
                          exposure={"B": 1.0, "N": 0.30, "E": 0, "C": 0},
                          top_n=15, buffer=20, max_vol=0.55,
                          score_mode="base", afford="drop", record=True)
    t0 = time.time()
    res = run_backtest(strat, start=START, end=END, capital=CAP,
                       data_dir=REPO / "data", warmup_days=340, rebalance="M",
                       benchmark=["sh000300", "sz399006"],
                       slippage_bps=10.0, commission_bps=2.5, min_commission=5.0,
                       regime_labels=labels, name="A-attr")
    elapsed = time.time() - t0
    print(f"run took {elapsed:.1f}s")

    # ---- ledgers ---------------------------------------------------------
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    nav.index.name = "date"
    nav.to_csv(OUT / "a_nav.csv")
    res["trades"].to_csv(OUT / "a_trades.csv", index=False)
    res["orders"].to_csv(OUT / "a_orders.csv", index=False)
    res["events"].to_csv(OUT / "a_events.csv", index=False)
    pos = res["positions"].copy()
    pos.to_csv(OUT / "a_positions.csv", index=False)
    pd.DataFrame(strat.audit_days).to_csv(OUT / "a_audit_days.csv", index=False)
    pd.DataFrame(strat.audit_reb).to_csv(OUT / "a_audit_reb.csv", index=False)

    # target books: one row per (set_targets call, code)
    book_rows, call_rows = [], []
    seq = 0
    for rec in strat.call_log:
        seq += 1
        flat = dict(date=rec["date"], seq=seq, call=rec["call"])
        if rec["call"] == "set_targets":
            flat.update(note=rec.get("note", ""), n_codes=rec.get("n_codes", 0))
            call_rows.append(flat)
            for code, w in rec.get("weights", {}).items():
                book_rows.append(dict(date=rec["date"], seq=seq, note=rec.get("note", ""),
                                      code=code, weight=float(w)))
        else:
            flat.update(code=rec.get("code", ""), note=rec.get("note", ""),
                        shares=rec.get("shares"), fraction=rec.get("fraction"),
                        value=rec.get("value"), weight=rec.get("weight"))
            call_rows.append(flat)
    pd.DataFrame(book_rows).to_csv(OUT / "a_target_books.csv", index=False)
    pd.DataFrame(call_rows).to_csv(OUT / "a_call_log.csv", index=False)

    # ---- summary + verification -----------------------------------------
    m = res["metrics"]
    n = nav["nav"].astype(float)
    day_ret = n.pct_change(fill_method=None).dropna()
    max_pos = int(nav["n_positions"].max())
    over15 = int((nav["n_positions"] > 15).sum())
    summary = {
        "name": res["name"],
        "window": [res["start"], res["end"]],
        "capital": CAP,
        "n_trading_days": int(len(nav)),
        "total_return": float(n.iloc[-1] / CAP - 1.0),
        "max_drawdown": float((n / n.cummax() - 1.0).min()),
        "maxdd_peak_date": m["maxdd_peak_date"],
        "maxdd_trough_date": m["maxdd_trough_date"],
        "maxdd_recovery_date": m["maxdd_recovery_date"],
        "cagr": m["cagr"],
        "ann_vol": m["ann_vol"],
        "sharpe": m["sharpe"],
        "avg_exposure": m["avg_exposure"],
        "all_cash_days": m["all_cash_days"],
        "trade_count": m["trade_count"],
        "total_fees": m["total_fees"],
        "fifo_realized_pnl": m["fifo_realized_pnl"],
        "fifo_unrealized_pnl": m["fifo_unrealized_pnl"],
        "fifo_win_rate": m["fifo_win_rate"],
        "final_nav": float(n.iloc[-1]),
        "max_positions_observed": max_pos,
        "days_over_15": over15,
        "n_rebalances": len(strat.audit_reb),
        "n_set_targets_calls": sum(1 for r in strat.call_log if r["call"] == "set_targets"),
        "n_explicit_sell_calls": sum(1 for r in strat.call_log if r["call"] == "sell"),
        "elapsed_s": round(elapsed, 1),
        "published": PUBLISHED,
        "verify": {},
    }
    v = summary["verify"]
    v["total_return_match"] = bool(abs(summary["total_return"] - PUBLISHED["total_return"]) < 1e-12)
    v["max_drawdown_match"] = bool(abs(summary["max_drawdown"] - PUBLISHED["max_drawdown"]) < 1e-12)
    v["dd_peak_match"] = summary["maxdd_peak_date"] == PUBLISHED["dd_peak"]
    v["dd_trough_match"] = summary["maxdd_trough_date"] == PUBLISHED["dd_trough"]
    v["max_positions_match"] = max_pos == PUBLISHED["max_positions_observed"]
    v["all_cash_days_match"] = summary["all_cash_days"] == PUBLISHED["all_cash_days"]
    v["published_reproduced"] = bool(all(v[k] for k in (
        "total_return_match", "max_drawdown_match", "dd_peak_match",
        "dd_trough_match", "max_positions_match")))
    (OUT / "a_summary.json").write_text(json.dumps(summary, indent=1,
                                                   ensure_ascii=False, default=str),
                                        encoding="utf-8")

    print("=" * 78)
    print(f"total_return   {summary['total_return']:.10f}  published {PUBLISHED['total_return']:.10f}")
    print(f"max_drawdown   {summary['max_drawdown']:.10f}  published {PUBLISHED['max_drawdown']:.10f}")
    print(f"dd peak/trough {summary['maxdd_peak_date']} -> {summary['maxdd_trough_date']} "
          f"(published {PUBLISHED['dd_peak']} -> {PUBLISHED['dd_trough']})")
    print(f"max positions  {max_pos} (published {PUBLISHED['max_positions_observed']})   "
          f"days>15 {over15}   all_cash_days {summary['all_cash_days']} "
          f"(published {PUBLISHED['all_cash_days']})")
    print(f"cagr {summary['cagr']:.6f} (published {PUBLISHED['cagr']})  "
          f"avg_exposure {summary['avg_exposure']:.6f} (published {PUBLISHED['avg_exposure']})")
    print(f"trades {summary['trade_count']}  fees {summary['total_fees']:.2f}  "
          f"fifo_win_rate {summary['fifo_win_rate']:.4f}")
    print(f"rebalances {summary['n_rebalances']}  set_targets calls "
          f"{summary['n_set_targets_calls']}  explicit sell calls {summary['n_explicit_sell_calls']}")
    print("VERIFY published reproduced:", summary["verify"]["published_reproduced"])
    print("=" * 78)
    if not summary["verify"]["published_reproduced"]:
        print("!!! STOP: published values NOT reproduced exactly !!!")
        sys.exit(2)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
