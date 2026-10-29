# -*- coding: utf-8 -*-
"""STEP 3h: PRE-REGISTERED cost / execution-delay stress on frozen strategy A.

Two stress runs, settings fixed BEFORE running (and A's rules are NOT changed):
  (i)  doubled costs: slippage 20 bp/side + commission 5 bp (min commission 5 CNY,
       stamp tax / transfer fee unchanged)  -- engine.api.run_backtest parameters.
  (ii) 1-trading-day execution delay: every order batch A issues at signal day T is
       buffered and replayed at T+1 close, therefore filled at the T+2 open.
       Implemented as DelayedVariant(Variant): it intercepts ctx.set_targets / sell /
       buy and queues them; the queue is replayed at the start of the next trading
       day's on_bar (before A's own logic for that day).  The engine then collects the
       replayed batch at T+1 close and executes it at T+2 open (the engine's normal
       "close signal -> next open" is T+1; adding the buffer makes it T+2).

Pre-registered failure line: if either stress breaks a -30.0% max drawdown, it is
reported as a FAILED stress result; strategy A is not modified.

Outputs: cost_delay_stress.csv, cost_delay_stress.json, a_cost2x_nav.csv,
         a_delay1_nav.csv, a_delay1_trades.csv
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attrib_common as C  # noqa: E402
import variants as V  # noqa: E402
from engine.api import run_backtest  # noqa: E402

CAP = C.CAP
# ---- pre-registered settings (fixed before the stress run) ----------------
STRESS_SETTINGS = {
    "cost_2x": dict(slippage_bps=20.0, commission_bps=5.0, min_commission=5.0),
    "delay_1d": dict(exec_delay_days=1),
}
DD_FAIL_LINE = -0.30

BASE_KW = dict(state_mode="frozen", exposure={"B": 1.0, "N": 0.30, "E": 0, "C": 0},
               top_n=15, buffer=20, max_vol=0.55, score_mode="base", afford="drop")


class DelayedVariant(V.Variant):
    """Frozen A whose order batch is buffered by one trading day.

    Only the *timing* of the calls A already makes is changed; the selection logic,
    exposure map, buffer, affordability rule and exits are exactly variants.Variant.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self._queue: list = []
        self._orig = None

    def _patch(self, ctx):
        if self._orig is not None:
            return
        names = ("set_targets", "sell", "buy", "close", "close_all",
                 "order_target_percent", "cancel_pending")
        self._orig = {n: getattr(ctx, n) for n in names}

        def make(nm):
            def wrapper(*a, **k):
                self._queue.append((nm, a, k))
                return None
            return wrapper
        for n in names:
            setattr(ctx, n, make(n))
        self._ctx = ctx

    def _flush(self):
        if not self._queue:
            return
        batch, self._queue = self._queue, []
        for nm, a, k in batch:
            self._orig[nm](*a, **k)

    def on_init(self, ctx):
        self._patch(ctx)

    def on_bar(self, ctx):
        self._patch(ctx)
        self._flush()            # replay yesterday's batch today -> fills at T+2 open
        super().on_bar(ctx)

    def on_rebalance(self, ctx):
        self._patch(ctx)
        super().on_rebalance(ctx)


def stats_of(res: dict) -> dict:
    nav = res["nav"]["nav"].astype(float)
    ret = nav.pct_change(fill_method=None).dropna()
    n = len(nav)
    return dict(
        total_return=float(nav.iloc[-1] / CAP - 1.0),
        max_drawdown=float((nav / nav.cummax() - 1.0).min()),
        cagr=float((nav.iloc[-1] / CAP) ** (252.0 / n) - 1.0),
        ann_vol=float(ret.std(ddof=1) * np.sqrt(252.0)),
        sharpe=float(ret.mean() / ret.std(ddof=1) * np.sqrt(252.0)) if ret.std(ddof=1) > 0 else None,
        avg_exposure=float(res["nav"]["exposure"].astype(float).mean()),
        n_positions_max=int(res["nav"]["n_positions"].max()),
        trade_count=int(res["metrics"]["trade_count"]),
        total_fees=float(res["metrics"]["total_fees"]),
        final_nav=float(nav.iloc[-1]),
        n_trading_days=n,
    )


def main():
    labels = C.load_regime_letters()
    labels = {d: r["letter"] for d, r in labels.iterrows()}
    common = dict(start=C.START, end=C.END, capital=CAP, data_dir=C.DATA,
                  warmup_days=340, rebalance="M", benchmark=["sh000300", "sz399006"],
                  regime_labels=labels)
    rows = []

    # ---- baseline A (already computed; re-use saved nav) ----------------
    base_sum = json.loads((C.OUT / "a_summary.json").read_text(encoding="utf-8"))
    rows.append(dict(case="A baseline (10bp slip / 2.5bp comm, T+1 open)",
                     slippage_bps=10.0, commission_bps=2.5, exec_delay_days=0,
                     total_return=base_sum["total_return"],
                     max_drawdown=base_sum["max_drawdown"],
                     cagr=base_sum["cagr"], ann_vol=base_sum["ann_vol"],
                     sharpe=base_sum["sharpe"], avg_exposure=base_sum["avg_exposure"],
                     n_positions_max=base_sum["max_positions_observed"],
                     trade_count=base_sum["trade_count"],
                     total_fees=base_sum["total_fees"],
                     final_nav=base_sum["final_nav"], dd_breach_30=False))

    # ---- (i) doubled costs ---------------------------------------------
    print("running doubled-cost stress (20bp slippage, 5bp commission) ...")
    t0 = time.time()
    strat = V.Variant(name="A-stress-cost2x", **BASE_KW)
    res = run_backtest(strat, slippage_bps=STRESS_SETTINGS["cost_2x"]["slippage_bps"],
                       commission_bps=STRESS_SETTINGS["cost_2x"]["commission_bps"],
                       min_commission=5.0, name="A-stress-cost2x", **common)
    s = stats_of(res)
    res["nav"].copy().to_csv(C.OUT / "a_cost2x_nav.csv")
    rows.append(dict(case="(i) doubled costs (20bp slip / 5bp comm)", slippage_bps=20.0,
                     commission_bps=5.0, exec_delay_days=0, dd_breach_30=bool(s["max_drawdown"] < DD_FAIL_LINE),
                     elapsed_s=round(time.time() - t0, 1), **s))
    print(f"  ret={s['total_return']:+.4f} dd={s['max_drawdown']:.4f} sharpe={s['sharpe']:.3f} "
          f"({time.time() - t0:.1f}s)")

    # ---- (ii) 1-trading-day execution delay ----------------------------
    print("running 1-trading-day execution delay (T+2 open fills) ...")
    t0 = time.time()
    strat = DelayedVariant(name="A-stress-delay1", **BASE_KW)
    res = run_backtest(strat, slippage_bps=10.0, commission_bps=2.5,
                       min_commission=5.0, name="A-stress-delay1", **common)
    s = stats_of(res)
    res["nav"].copy().to_csv(C.OUT / "a_delay1_nav.csv")
    res["trades"].to_csv(C.OUT / "a_delay1_trades.csv", index=False)
    rows.append(dict(case="(ii) 1-trading-day exec delay (T+2 open)", slippage_bps=10.0,
                     commission_bps=2.5, exec_delay_days=1,
                     dd_breach_30=bool(s["max_drawdown"] < DD_FAIL_LINE),
                     elapsed_s=round(time.time() - t0, 1), **s))
    print(f"  ret={s['total_return']:+.4f} dd={s['max_drawdown']:.4f} sharpe={s['sharpe']:.3f} "
          f"({time.time() - t0:.1f}s)")
    # delay sanity: the delayed run must actually shift fill dates by ~1 trading day;
    # compare the first buy date of every code held by both runs
    base_tr = pd.read_csv(C.OUT / "a_trades.csv", dtype={"date": str})
    base_tr["code"] = base_tr["code"].astype(str).str.zfill(6)
    base_first = base_tr[base_tr["side"] == "buy"].groupby("code")["date"].min()
    dl = res["trades"]
    dl["code"] = dl["code"].astype(str).str.zfill(6)
    dl_first = dl[dl["side"] == "buy"].groupby("code")["date"].min()
    common_codes = base_first.index.intersection(dl_first.index)
    shifts = []
    for c in common_codes:
        b = pd.Timestamp(base_first[c])
        d = pd.Timestamp(dl_first[c])
        if d >= b:
            shifts.append(int(np.busday_count(b.date(), d.date())))
    shifts = np.asarray(shifts, dtype=float)
    n_cmp = len(shifts)
    shift_days = float(shifts.sum()) if n_cmp else 0.0

    out = pd.DataFrame(rows)
    out.to_csv(C.OUT / "cost_delay_stress.csv", index=False)
    payload = dict(settings=STRESS_SETTINGS, dd_fail_line=DD_FAIL_LINE,
                   results=rows, delay_validation=dict(
                       n_codes_compared=int(n_cmp),
                       mean_business_day_shift=float(shift_days / n_cmp) if n_cmp else None,
                       median_business_day_shift=float(np.median(shifts)) if n_cmp else None,
                       pct_shifted_at_least_1_day=float((shifts >= 1).mean()) if n_cmp else None,
                       n_shifted_exactly_1=int((shifts == 1).sum()) if n_cmp else 0,
                       first_buy_date_baseline=str(base_first.min()),
                       first_buy_date_delayed=str(dl_first.min()),
                       note="positive shift is expected (~1 business day)"))
    (C.OUT / "cost_delay_stress.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    print("\n=== 3h cost / delay stress (pre-registered) ===")
    print(out[["case", "total_return", "max_drawdown", "cagr", "sharpe",
               "avg_exposure", "trade_count", "total_fees", "dd_breach_30"]].to_string(index=False))
    print("\ndelay validation:", json.dumps(payload["delay_validation"], indent=1))


if __name__ == "__main__":
    main()
