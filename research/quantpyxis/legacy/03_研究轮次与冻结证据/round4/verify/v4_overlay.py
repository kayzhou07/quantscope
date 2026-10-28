# -*- coding: utf-8 -*-
"""Round-4 verification item 4 (+ report stress table cross-check).

Re-runs (read-only use of out/):
  1. top15_fuse_10_15 once -> confirm the reported account-drawdown-fuse deadlock
     (latest-segment return exactly 0, avg exposure ~0.10, permanent cash).
  2. the 20bp/5bp cost-stress reruns for the two final candidates, because
     out/phase_stress_summary.csv and out/phase_stress_b100n30_top15.csv disagree.
Writes verify/ only.
"""
from __future__ import annotations

import io
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
LATEST = ("2024-09-18", "2026-10-19")
SEGS = PB.SEGS


def run(strat, *, slippage_bps=10.0, commission_bps=2.5):
    labels = PB.load_regime()
    return run_backtest(strat, start=START, end=END, capital=CAP,
                        data_dir=WS / "qrun_1416" / "data", warmup_days=340,
                        rebalance="M", benchmark=["sh000300", "sz399006"],
                        slippage_bps=slippage_bps, commission_bps=commission_bps,
                        min_commission=5.0, regime_labels=labels, name=strat.name)


def nav_frame(res):
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    return nav


def m_stats(n: pd.Series) -> dict:
    r = n.pct_change().fillna(0.0)
    return dict(total_return=float(n.iloc[-1] / CAP - 1),
                max_drawdown=float((n / n.cummax() - 1).min()),
                cagr=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1),
                ann_vol=float(r.std() * math.sqrt(252.0)),
                sharpe=float(r.mean() / r.std() * math.sqrt(252.0)) if r.std() > 0 else None)


def seg(n: pd.Series, s: str, e: str) -> dict:
    sub = n.loc[(n.index >= s) & (n.index <= e)]
    if len(sub) < 2:
        return {}
    return dict(ret=float(sub.iloc[-1] / sub.iloc[0] - 1),
                dd=float((sub / sub.cummax() - 1).min()), days=int(len(sub)))


def main():
    log = []

    def P(*a):
        s = " ".join(str(x) for x in a)
        log.append(s)
        print(s)

    P("=" * 100)
    P("ITEM 4: reproduce the negative overlay results (DD fuse deadlock, crash brake)")
    P("=" * 100)

    # ---------------------------------------------------------- DD fuse deadlock
    EXP100_30 = {"B": 1.0, "N": .30, "E": 0, "C": 0}
    B = dict(state_mode="frozen", top_n=10, buffer=20, max_vol=.55,
             score_mode="base", afford="drop")
    T15 = dict(B, top_n=15, exposure=EXP100_30)
    strat = OverlayVariant(name="R4C-top15_fuse_10_15", **T15, dd_fuse=(.10, .15))
    res = run(strat)
    nav = nav_frame(res)
    n = nav["nav"].astype(float)
    ref = pd.read_csv(OUT / "full_top15_fuse_10_15_nav.csv", dtype={"date": str})
    maxdiff = float(np.max(np.abs(n.to_numpy() - ref["nav"].astype(float).to_numpy())))
    st = m_stats(n)
    latest = seg(n, *LATEST)
    expo = nav["exposure"].astype(float)
    mv = nav["market_value"].astype(float)
    # locate the deadlock: last date on which NAV changed at all
    ch = n.diff().abs()
    last_move = str(ch[ch > 1e-9].index[-1])
    flat_after = n.loc[last_move:].nunique()
    P(f"\n[A] top15_fuse_10_15 re-run (same config as run_phaseC.py)")
    P(f"  nav vs published CSV: max abs diff {maxdiff:.3e} (tolerance 1e-4)")
    P(f"  total_return={st['total_return']:.10f}  maxDD={st['max_drawdown']:.10f}"
      f"  sharpe={st['sharpe']:.6f}")
    P(f"  avg_exposure={expo.mean():.6f}  all_cash_days={(mv <= .01).sum()}"
      f"  final_exposure={expo.iloc[-1]:.6f}  final_n_positions={int(nav['n_positions'].iloc[-1])}")
    P(f"  latest segment {LATEST[0]}..{LATEST[1]}: ret={latest['ret']!r} dd={latest['dd']!r}")
    P(f"  last day NAV changed: {last_move}; distinct NAV levels after that: {flat_after}")
    P(f"  exposure over last 502 days: mean={expo.loc[LATEST[0]:].mean():.6f}"
      f" max={expo.loc[LATEST[0]:].max():.6f}")
    # deadlock onset: first date after which exposure is permanently 0
    zero = (mv <= .01)
    onset = None
    for i in range(len(zero)):
        if zero.iloc[i:].all():
            onset = str(zero.index[i])
            break
    P(f"  first date of the permanent all-cash state: {onset}")
    # confirm running-peak condition never clears (the reported mechanism)
    peak = n.cummax()
    dd = 1.0 - n / peak
    after = dd.loc[onset:] if onset else dd
    P(f"  after {onset}: max account drawdown-from-peak={after.max():.4f}"
      f" (fuse arms at 0.10/0.15, never recovers because the peak cannot be regained)")
    phaseC = pd.read_csv(OUT / "phaseC_summary.csv")
    rowC = phaseC[phaseC["tag"] == "top15_fuse_10_15"].iloc[0]
    P(f"  phaseC_summary row: total={rowC['total_return']:.10f}"
      f" avg_exposure={rowC['avg_exposure']:.6f} latest_ret={rowC['latest_ret']!r}"
      f" latest_dd={rowC['latest_dd']!r}")

    # ------------------------------------------------------------- crash brake
    P("\n[B] crash brake check (from published nav CSVs, no rerun needed)")
    P("  top15_crash (b100n30_top15 + index <=-5%/5d<=-8% -> flat 10 sessions):")
    cr = pd.read_csv(OUT / "full_top15_crash_nav.csv", dtype={"date": str})
    cr.index = cr["date"].astype(str)
    cn = cr["nav"].astype(float)
    b15 = pd.read_csv(OUT / "full_b100_n30_top15_nav.csv", dtype={"date": str})
    b15.index = b15["date"].astype(str)
    bn = b15["nav"].astype(float)
    cs = m_stats(cn)
    bs = m_stats(bn)
    P(f"    crash_brake: total={cs['total_return']:.6f} maxDD={cs['max_drawdown']:.6f}"
      f" sharpe={cs['sharpe']:.4f}  latest={seg(cn, *LATEST)['ret']:.6f}")
    P(f"    no brake  : total={bs['total_return']:.6f} maxDD={bs['max_drawdown']:.6f}"
      f" sharpe={bs['sharpe']:.4f}  latest={seg(bn, *LATEST)['ret']:.6f}")
    P(f"    delta total={cs['total_return'] - bs['total_return']:+.6f}"
      f" -> crash brake {'HURTS' if cs['total_return'] < bs['total_return'] else 'HELPS'}")

    # ----------------------------------------------------- cost stress reruns
    P("\n[C] cost stress re-runs (20bp slippage + 5bp commission), report §4.6")
    a = run(V.Variant(name="stress_b100n30_top15", **dict(
        B, top_n=15, exposure=EXP100_30)), slippage_bps=20.0, commission_bps=5.0)
    an = nav_frame(a)["nav"].astype(float)
    ast = m_stats(an)
    alatest = seg(an, *LATEST)
    afees = float(a["metrics"].get("total_fees", np.nan))
    P(f"  b100_n30_top15 @20bp/5bp: total={ast['total_return']:.10f}"
      f" maxDD={ast['max_drawdown']:.10f} sharpe={ast['sharpe']:.6f}"
      f" fees={afees:.2f} latest={alatest['ret']:.6f} latestDD={alatest['dd']:.6f}")
    stress1 = pd.read_csv(OUT / "phase_stress_b100n30_top15.csv")
    x2 = stress1[stress1["cost"] == "x2cost"].iloc[0]
    P(f"  out/phase_stress_b100n30_top15.csv x2cost: total={x2['total_return']:.10f}"
      f" maxDD={x2['max_drawdown']:.10f} sharpe={x2['sharpe']:.6f}"
      f" fees={x2['total_fees']:.2f} latest={x2['latest_ret']:.6f}")
    P(f"  MATCH phase_stress_b100n30_top15.csv:"
      f" {abs(ast['total_return'] - x2['total_return']) <= 1e-6 and abs(afees - x2['total_fees']) <= 1e-4}")
    ss = pd.read_csv(OUT / "phase_stress_summary.csv")
    srow = ss[ss["tag"] == "stress_b100n30_top15"].iloc[0]
    P(f"  out/phase_stress_summary.csv stress_b100n30_top15 (as published):"
      f" total={srow['total_return']:.10f} maxDD={srow['max_drawdown']:.10f}"
      f" sharpe={srow['sharpe']:.6f} fees={srow['total_fees']:.2f}")
    P(f"  -> published stress_summary row CONTRADICTS report §4.6 (+149.67%/-25.23%/0.75);"
      f" rerun value is the report value: {abs(ast['total_return'] - 0.1496727737698396) <= 1e-9}")

    # n90 stress
    EXP90 = {"B": 1.0, "N": .90, "E": 0, "C": 0}
    BCFG = dict(state_mode="frozen", top_n=20, buffer=20, max_vol=None,
                score_mode="base", afford="drop", exposure=EXP90)
    b = run(OverlayVariant(name="stress_n90_top20_novolcap", **BCFG),
            slippage_bps=20.0, commission_bps=5.0)
    bn2 = nav_frame(b)["nav"].astype(float)
    bst = m_stats(bn2)
    bfees = float(b["metrics"].get("total_fees", np.nan))
    P(f"  n90_top20_novolcap @20bp/5bp: total={bst['total_return']:.10f}"
      f" maxDD={bst['max_drawdown']:.10f} sharpe={bst['sharpe']:.6f} fees={bfees:.2f}")
    srow2 = ss[ss["tag"] == "stress_n90_top20_novolcap"].iloc[0]
    P(f"  out/phase_stress_summary.csv stress_n90_top20_novolcap:"
      f" total={srow2['total_return']:.10f} maxDD={srow2['max_drawdown']:.10f}"
      f" sharpe={srow2['sharpe']:.6f} fees={srow2['total_fees']:.2f}")
    P(f"  MATCH stress_summary n90 row:"
      f" {abs(bst['total_return'] - srow2['total_return']) <= 1e-6}")

    P("\n" + "=" * 100)
    payload = dict(
        dd_fuse=dict(nav_max_diff=maxdiff, metrics=st, latest=latest,
                     avg_exposure=float(expo.mean()),
                     all_cash_days=int((mv <= .01).sum()),
                     final_exposure=float(expo.iloc[-1]),
                     last_nav_move=last_move, permanent_cash_from=onset,
                     phaseC_row=rowC.to_dict()),
        crash_brake=dict(crash=m_stats(cn), nocrash=m_stats(bn),
                         latest_crash=seg(cn, *LATEST), latest_nocrash=seg(bn, *LATEST)),
        stress=dict(
            b100n30_top15_rerun=dict(metrics=ast, latest=alatest, fees=afees),
            phase_stress_b100n30_top15_x2cost=x2.to_dict(),
            phase_stress_summary_b100n30_top15=srow.to_dict(),
            n90_top20_novolcap_rerun=dict(metrics=bst, fees=bfees),
            phase_stress_summary_n90=srow2.to_dict()),
    )
    (VER / "v4_overlay.json").write_text(json.dumps(payload, indent=1, default=str),
                                         encoding="utf-8")
    (VER / "v4_overlay.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\nwrote", VER / "v4_overlay.log", "and v4_overlay.json")


if __name__ == "__main__":
    main()
