# -*- coding: utf-8 -*-
"""Round-4 verification item 2, part b.

  * recover the Phase-H final candidate n90_top15_novolcap config (run_phaseH.py was
    never saved) and calibrate it against out/full_n90_top15_novolcap_nav.csv;
  * refine the fill-timing test (2-decimal execution price rounding);
  * test the report's claim "状态变化即调仓" (immediate state-change rebalance) for
    candidate B: B<->N transitions are 10pp, below OverlayVariant's 15pp threshold;
  * test E/C risk-off exits are immediate (T+1 open) and re-entry is month-end only.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R4 = HERE.parent
WS = R4.parent.parent
sys.path.insert(0, str(R4))
sys.path.insert(0, str(WS / "qrun_1416"))

import variants as V  # noqa: E402
from overlays import OverlayVariant  # noqa: E402
from engine.api import run_backtest  # noqa: E402

sys.path.insert(0, str(R4))
import run_phaseB as PB  # noqa: E402

OUT = R4 / "out"
VER = HERE
CAP = 100_000.0
START, END = "2014-07-01", "2026-10-19"


def run(strat):
    labels = PB.load_regime()
    return run_backtest(strat, start=START, end=END, capital=CAP,
                        data_dir=WS / "qrun_1416" / "data", warmup_days=340,
                        rebalance="M", benchmark=["sh000300", "sz399006"],
                        slippage_bps=10.0, commission_bps=2.5, min_commission=5.0,
                        regime_labels=labels, name=strat.name)


def nav_frame(res):
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    return nav


def main():
    log = []

    def P(*a):
        s = " ".join(str(x) for x in a)
        log.append(s)
        print(s)

    P("=" * 100)
    P("ITEM 2b: Phase-H candidate calibration, fill timing, state-change mechanics")
    P("=" * 100)

    # ------------------------------------------- calibrate n90_top15_novolcap
    P("\n[A] CALIBRATE out/full_n90_top15_novolcap_nav.csv (Phase-H final candidate C)")
    P("    inferred config: frozen, top_n=15, buffer=20, max_vol=None, B=1.0/N=.90")
    EXP90 = {"B": 1.0, "N": .90, "E": 0, "C": 0}
    cfg = dict(state_mode="frozen", top_n=15, buffer=20, max_vol=None,
               score_mode="base", afford="drop", exposure=EXP90)
    strat15 = OverlayVariant(name="R4H-n90_top15_novolcap", record=True, **cfg)
    res15 = run(strat15)
    got = nav_frame(res15)
    ref = pd.read_csv(OUT / "full_n90_top15_novolcap_nav.csv", dtype={"date": str})
    cols = ["nav", "cash", "market_value", "exposure", "ret", "n_positions", "fees",
            "dividends", "traded_amount", "turnover", "bench_sh000300", "bench_sz399006"]
    diffs = {}
    ok_len = len(got) == len(ref)
    for c in cols:
        a = got[c].astype(float).to_numpy()
        b = ref[c].astype(float).to_numpy()
        diffs[c] = float(np.nanmax(np.abs(a - b))) if ok_len else float("nan")
    n = got["nav"].astype(float)
    r = n.pct_change().fillna(0.0)
    m = dict(total_return=float(n.iloc[-1] / CAP - 1),
             max_drawdown=float((n / n.cummax() - 1).min()),
             sharpe=float(r.mean() / r.std() * math.sqrt(252.0)))
    ph = pd.read_csv(OUT / "phaseH_summary.csv")
    row = ph[ph["tag"] == "n90_top15_novolcap"].iloc[0]
    P(f"  rows={len(got)}  max abs diff nav={diffs['nav']:.3e}"
      f" cash={diffs['cash']:.3e} market_value={diffs['market_value']:.3e}")
    P(f"  metrics: total={m['total_return']:.10f} (phaseH {row['total_return']:.10f})"
      f" maxDD={m['max_drawdown']:.10f} (phaseH {row['max_drawdown']:.10f})"
      f" sharpe={m['sharpe']:.6f} (phaseH {row['sharpe']:.6f})")
    ok = ok_len and diffs["nav"] <= 1e-4 and diffs["exposure"] <= 1e-9
    P(f"  calibration {'PASS' if ok else 'FAIL'}")

    # --------------------------------------------------- refined fill timing
    P("\n[B] FILL TIMING (refined): expected price = round(open*(1+/-10bp), 2)")
    from engine.data import DataBundle
    bundle = DataBundle.load(WS / "qrun_1416" / "data", START, END, warmup_days=340)
    open_mat = bundle.field("open")
    t = res15["trades"].copy()
    t = t[t["side"].isin(["buy", "sell"])].reset_index(drop=True)
    n_exact, n_clamp, n_bad, n_unknown = 0, 0, 0, 0
    bad = []
    for _, rr in t.iterrows():
        code = str(rr["code"])
        if code not in bundle.code_index:
            n_unknown += 1
            continue
        try:
            i = bundle.i_of(str(rr["date"]))
        except Exception:
            n_unknown += 1
            continue
        raw = float(open_mat[i, bundle.code_index[code]])
        px = float(rr["price"])
        if not np.isfinite(raw) or raw <= 0:
            n_unknown += 1
            continue
        sgn = 1 if rr["side"] == "buy" else -1
        exp = round(raw * (1.0 + 0.001 * sgn), 2)
        if abs(px - exp) <= 0.011:
            n_exact += 1
        elif abs(px / raw - 1.0) <= 0.02:      # within 2% of open: limit clamp
            n_clamp += 1
        else:
            n_bad += 1
            if len(bad) < 10:
                bad.append(dict(date=str(rr["date"]), code=code, side=rr["side"],
                                price=px, open=raw, expected=exp))
    tot = max(len(t), 1)
    P(f"  trades={len(t)} exact open+/-10bp(2dp)={n_exact/tot:.4%} ({n_exact})"
      f" within-2%-of-open(clamp)={n_clamp/tot:.4%} ({n_clamp})"
      f" unexplained={n_bad} unknown/no-open={n_unknown}")
    for b in bad:
        P(f"      unexplained: {b}")

    # ------------------------------------------------------- schedule checks
    P("\n[C] SCHEDULE / state-change mechanics (2989 days, full window)")
    nav = nav_frame(res15)
    dates = nav.index.to_list()
    reg = nav["regime"].astype(str).to_numpy()
    expo = nav["exposure"].astype(float).to_numpy()
    mv = nav["market_value"].astype(float).to_numpy()
    ac = strat15.audit_reb
    reasons = {}
    for r_ in ac:
        reasons[r_["reason"]] = reasons.get(r_["reason"], 0) + 1
    P(f"  rebalance signals: {len(ac)}  reasons={reasons}")
    # every scheduled rebalance must be the last trading day of its month
    last_of_month = set()
    for d in dates:
        last_of_month.add(d)
    # build real last trading day of each month
    by_month = {}
    for d in dates:
        by_month[d[:7]] = d
    last_td = set(by_month.values())
    not_month_end = [r_["date"] for r_ in ac if r_["date"] not in last_td]
    P(f"  rebalance dates that are NOT the last trading day of their month:"
      f" {len(not_month_end)} {not_month_end[:5]}")
    # B<->N transitions between month-ends must NOT rebalance (10pp < 15pp overlay arm)
    idx = {d: k for k, d in enumerate(dates)}
    exec_days = set()
    for k, d in enumerate(dates[:-1]):
        exec_days.add(dates[k + 1])
    reb_exec = set()
    for r_ in ac:
        k = idx.get(r_["date"])
        if k is not None and k + 1 < len(dates):
            reb_exec.add(dates[k + 1])
    trans_bn, jumps = 0, []
    for k in range(1, len(dates)):
        if reg[k] != reg[k - 1] and {reg[k], reg[k - 1]} == {"B", "N"}:
            trans_bn += 1
            d = dates[k]
            jump = abs(expo[k] - expo[k - 1])
            # ignore price-driven drift: |jump| must stay below 5pp to prove no rebalance
            if jump > 0.05 and d not in reb_exec:
                jumps.append((d, reg[k - 1], reg[k], float(expo[k - 1]), float(expo[k])))
    P(f"  B<->N regime transitions: {trans_bn}; transitions with >5pp exposure jump"
      f" on a non-rebalance day: {len(jumps)} {jumps[:5]}")
    # E/C risk-off: signal at day k close (first E/C day) -> exit fills at k+1 open,
    # so exposure/market value must be ~0 at the k+1 close (k+2 tolerates blocked carry)
    risks = 0
    bad_risk = []
    lag_exit = []
    for k in range(1, len(dates) - 1):
        if reg[k] in ("E", "C") and reg[k - 1] not in ("E", "C") and mv[k - 1] > 1.0:
            risks += 1
            if mv[k + 1] > 1.0 or expo[k + 1] > 1e-6:
                bad_risk.append((dates[k], dates[k + 1], float(expo[k + 1]), float(mv[k + 1])))
            lag_exit.append((dates[k], dates[k + 1], float(expo[k]), float(expo[k + 1]),
                             float(mv[k + 1])))
    P(f"  B/N->E/C risk-off events with positions: {risks}; still holding at the next"
      f" close (T+1 exit failed): {len(bad_risk)} {bad_risk[:5]}")
    P("  sample transitions (signal day, fill day, exposure@signal, exposure@fill,"
      " market_value@fill):")
    for x in lag_exit[:6]:
        P(f"      {x}")
    P(f"  first trade {res15['trades']['date'].iloc[0]}"
      f" (first B/N month-end after 2014-07-01); last trade"
      f" {res15['trades']['date'].iloc[-1]}")
    P("  NOTE: report §5 row B says '状态变化即调仓' (rebalance on state change).")
    P("        The code (overlays.py on_bar) only rebalances off month-end when |dExp|>15pp;")
    P("        B=1.00 <-> N=0.90 is 10pp -> NO immediate rebalance.  Only E/C exits are")
    P("        immediate (Variant.on_bar desired<=0 -> _exit_all).  The claim is inaccurate")
    P("        for B<->N; it is accurate only as 'immediate risk-off exit / month-end re-entry'.")

    payload = dict(
        calibration_n90_top15=dict(rows=len(got), max_abs_diff=diffs, metrics=m,
                                   phaseH_row=row.to_dict(), ok=bool(ok)),
        fill_timing=dict(n_trades=int(len(t)), n_exact=int(n_exact), n_clamp=int(n_clamp),
                         n_bad=int(n_bad), n_unknown=int(n_unknown), bad=bad),
        schedule=dict(n_rebalances=len(ac), reasons=reasons,
                      not_month_end=not_month_end,
                      bn_transitions=int(trans_bn),
                      bn_jumps_on_non_reb=[[list(map(str, x)) for x in jumps]],
                      riskoff_events=int(risks), bad_risk=bad_risk),
    )
    (VER / "v2b_state_timing.json").write_text(json.dumps(payload, indent=1, default=str),
                                               encoding="utf-8")
    (VER / "v2b_state_timing.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\nwrote", VER / "v2b_state_timing.log", "and v2b_state_timing.json")


if __name__ == "__main__":
    main()
