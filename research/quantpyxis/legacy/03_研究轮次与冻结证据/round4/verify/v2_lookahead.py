# -*- coding: utf-8 -*-
"""Round-4 verification item 2: no-lookahead audit (structural + empirical).

Empirical harness: rebuild the final candidate n90_top20_novolcap exactly as
run_phaseG.py does, calibrate against out/full_n90_top20_novolcap_nav.csv, then
  (a) truncate the run at 2019-12-31 and compare nav / positions / trades up to the
      cutoff with the full-window run  (data-end invariance => no lookahead);
  (b) truncate at 2024-09-13 (start of the latest segment) and compare the same way;
  (c) verify every fill happened at the trade-date OPEN with the configured slippage
      (T+1: signal at D close -> fill at D+1 open).
Read-only over round4/out and qrun_1416.  Writes verify/ only.
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
REPO = R4.parents[0].parent  # workspace root
sys.path.insert(0, str(R4))
sys.path.insert(0, str(REPO / "qrun_1416"))

import variants as V  # noqa: E402
from overlays import OverlayVariant  # noqa: E402
from engine.api import run_backtest  # noqa: E402

sys.path.insert(0, str(R4))
import run_phaseB as PB  # noqa: E402

OUT = R4 / "out"
VER = HERE
CAP = 100_000.0
START, END = "2014-07-01", "2026-10-19"
NAVTOL = 1e-4
RATTOL = 1e-9

B_CFG = dict(state_mode="frozen", top_n=20, buffer=20, max_vol=None,
             score_mode="base", afford="drop",
             exposure={"B": 1.0, "N": .90, "E": 0, "C": 0})


def build(record=False):
    return OverlayVariant(name="R4G-n90_top20_novolcap", record=record, **B_CFG)


def run(end, record=False):
    labels = PB.load_regime()
    strat = build(record=record)
    res = run_backtest(strat, start=START, end=end, capital=CAP,
                       data_dir=REPO / "qrun_1416" / "data", warmup_days=340,
                       rebalance="M", benchmark=["sh000300", "sz399006"],
                       slippage_bps=10.0, commission_bps=2.5, min_commission=5.0,
                       regime_labels=labels, name=strat.name)
    return res, strat


def nav_frame(res):
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    return nav


def compare_trades(a, b, cols):
    """Exact comparison of two trade prefixes (same engine, different data end)."""
    if len(a) != len(b):
        return False, f"row count {len(a)} != {len(b)}"
    for c in cols:
        if c not in a or c not in b:
            continue
        if c in ("date", "code", "side", "reason"):
            if not a[c].astype(str).equals(b[c].astype(str)):
                return False, f"column {c} differs"
        else:
            va = a[c].astype(float).to_numpy()
            vb = b[c].astype(float).to_numpy()
            if not np.array_equal(va, vb):
                d = float(np.nanmax(np.abs(va - vb))) if len(va) else 0.0
                if not (d <= 1e-9):
                    return False, f"column {c} max abs diff {d:.3e}"
    return True, ""


def main():
    log = []

    def P(*a):
        s = " ".join(str(x) for x in a)
        log.append(s)
        print(s)

    P("=" * 100)
    P("ITEM 2: no-lookahead audit of round4/variants.py + round4/overlays.py")
    P("=" * 100)

    # ---------------------------------------------------------------- structural
    P("\n[A] STRUCTURAL REVIEW (code paths that can reach data)")
    src_v = (R4 / "variants.py").read_text(encoding="utf-8")
    src_o = (R4 / "overlays.py").read_text(encoding="utf-8")
    ctx_calls = sorted({c.split("(")[0] for c in
                        __import__("re").findall(r"ctx\.[a-z_]+\s*\(", src_v + src_o)})
    P(f"  ctx accessors used by variants.py+overlays.py: {ctx_calls}")
    P("  ctx.universe(tradable=True, exclude_st=True) -> as_of=today (engine default)")
    P("  ctx.history_matrix('qfq_close', 260) / ('amount', 60) -> engine slices rows")
    P("      lo = max(0, i-window+1) .. i  (engine/core.py, 'last `window` bars up to today')")
    P("  ctx.latest('close') -> row i only;  ctx.regime -> dict lookup for today's date")
    P("  ctx.index_history(280/30, 'sh000300') -> index series rows <= today")
    P("  ctx.positions / ctx.nav / ctx.cash / ctx.date / ctx.day / ctx.is_rebalance_day ->")
    P("      current-bar state only")
    forbidden = [t for t in ("read_csv", "open(", "np.load", "pd.read_", "DataBundle",
                             "bundle", "future", "tomorrow") if t in src_v + src_o]
    P(f"  direct file/data reads or future refs in strategy code: {forbidden or 'NONE'}")
    P("  all orders go through ctx.set_targets / ctx.sell / ctx.buy inside on_bar /")
    P("  on_rebalance; engine order (API.md s3): execute pending at day open (step 4) THEN")
    P("  on_bar/on_rebalance (step 6) -> a signal formed at D's close can only fill at")
    P("  D+1's open.  No intraday data accessor exists in the API.")
    P("  round4 state machine input: ctx.regime <- os/out/regime_v1.csv, whose label for")
    P("  date T is evaluated on T's CLOSE (os/regime_v1.py docstring) and the portfolio")
    P("  action is executed at T+1 open -> same timing as the frozen platform rules.")
    P("  overlay timing: dd/crash evaluated on ctx.nav / index close of today; no offset.")

    # --------------------------------------------------------------- calibration
    P("\n[B] CALIBRATION: reproduce out/full_n90_top20_novolcap_nav.csv with the config")
    P("    implied by run_phaseG.py (frozen, top20, buffer20, max_vol=None, B=1.0/N=.90)")
    ref = pd.read_csv(OUT / "full_n90_top20_novolcap_nav.csv", dtype={"date": str})
    res_full, strat_full = run(END, record=True)
    got = nav_frame(res_full)
    calc = {}
    if len(got) != len(ref):
        P(f"  FAIL length {len(got)} != {len(ref)}")
        calc["row_count_ok"] = False
    else:
        calc["row_count_ok"] = True
    num_cols = ["nav", "cash", "market_value", "exposure", "ret", "n_positions", "fees",
                "dividends", "traded_amount", "turnover", "bench_sh000300", "bench_sz399006"]
    maxdiff = {}
    for c in num_cols:
        a = got[c].astype(float).to_numpy()
        b = ref[c].astype(float).to_numpy()
        maxdiff[c] = float(np.nanmax(np.abs(a - b))) if len(a) == len(b) else float("nan")
    calc["nav_csv_max_abs_diff"] = maxdiff
    ok = all(v <= NAVTOL for k, v in maxdiff.items() if k != "ret") and maxdiff["ret"] <= 1e-9
    P(f"  rows={len(got)}  max abs diff vs published nav CSV per column:")
    for c, v in maxdiff.items():
        P(f"      {c:20s} {v:.3e}")
    P(f"  calibration {'PASS' if ok else 'FAIL'} (nav cash/market_value tolerance 1e-4 CNY)")
    n = got["nav"].astype(float)
    ret = n.pct_change().fillna(0.0)
    m = dict(total_return=float(n.iloc[-1] / CAP - 1),
             max_drawdown=float((n / n.cummax() - 1).min()),
             sharpe=float(ret.mean() / ret.std() * math.sqrt(252.0)))
    pg = pd.read_csv(OUT / "phaseG_summary.csv")
    row = pg[pg["tag"] == "n90_top20_novolcap"].iloc[0]
    P(f"  metrics: total={m['total_return']:.10f} (phaseG {row['total_return']:.10f})"
      f" maxDD={m['max_drawdown']:.10f} (phaseG {row['max_drawdown']:.10f})"
      f" sharpe={m['sharpe']:.6f} (phaseG {row['sharpe']:.6f})")
    calc["metrics_match_phaseG"] = bool(
        abs(m["total_return"] - row["total_return"]) <= 1e-6
        and abs(m["max_drawdown"] - row["max_drawdown"]) <= 1e-6
        and abs(m["sharpe"] - row["sharpe"]) <= 1e-6)

    # ------------------------------------------------------- truncation at 2019
    P("\n[C] TRUNCATION TEST end=2019-12-31 (same config) vs full run")
    res_t19, _ = run("2019-12-31", record=True)
    nav19 = nav_frame(res_t19)
    cut = "2019-12-31"
    fa = got[got.index <= cut].copy()
    fb = nav19.copy()
    P(f"  truncated run rows={len(fb)}; full run rows<= {cut}: {len(fa)}")
    trunc = {}
    n_common = min(len(fa), len(fb))
    same_dates = bool(list(fa.index[:n_common]) == list(fb.index[:n_common]))
    trunc["dates_align"] = same_dates
    trunc["row_count_equal"] = bool(len(fa) == len(fb))
    for c in num_cols:
        a = fa[c].astype(float).to_numpy()[:n_common]
        b = fb[c].astype(float).to_numpy()[:n_common]
        trunc[c] = float(np.nanmax(np.abs(a - b))) if n_common else float("nan")
    toll = {"nav": NAVTOL, "cash": NAVTOL, "market_value": NAVTOL}
    ok_nav = all(trunc[c] <= toll.get(c, RATTOL) for c in num_cols if c in trunc)
    P(f"  date axis identical: {same_dates}; rows identical: {trunc['row_count_equal']}")
    for c in num_cols:
        P(f"      {c:20s} max abs diff {trunc[c]:.3e}")
    P(f"  NAV/exposure/trades prefix identical: {'PASS' if ok_nav and same_dates else 'FAIL'}")

    # trades prefix
    ta = res_full["trades"].copy()
    tb = res_t19["trades"].copy()
    ta["date"] = ta["date"].astype(str)
    tb["date"] = tb["date"].astype(str)
    ta19 = ta[ta["date"] <= cut].reset_index(drop=True)
    tb19 = tb[tb["date"] <= cut].reset_index(drop=True)
    P(f"  trades through {cut}: full={len(ta19)} truncated={len(tb19)}")
    cols = ["date", "code", "side", "shares", "price", "commission", "stamp", "transfer",
            "fee_total", "reason"]
    trade_ok, trade_why = compare_trades(ta19, tb19, cols)
    P(f"  trades identical through cutoff: {'PASS' if trade_ok else 'FAIL'}"
      + ("" if trade_ok else f" ({trade_why})"))

    # positions prefix
    pa = res_full["positions"].copy()
    pb = res_t19["positions"].copy()
    pa["date"] = pa["date"].astype(str)
    pb["date"] = pb["date"].astype(str)
    pa19 = pa[pa["date"] <= cut].reset_index(drop=True)
    pb19 = pb[pb["date"] <= cut].reset_index(drop=True)
    pcols = ["date", "code", "shares", "value", "weight"]
    pos_ok = len(pa19) == len(pb19)
    if pos_ok and len(pa19):
        pos_ok = bool(pa19[pcols].astype(str).equals(pb19[pcols].astype(str)))
        if pos_ok:
            maxv = float(np.nanmax(np.abs(pa19["value"].astype(float).to_numpy()
                                         - pb19["value"].astype(float).to_numpy())))
            pos_ok = maxv <= NAVTOL
    P(f"  positions snapshots through {cut}: full={len(pa19)} truncated={len(pb19)}"
      f" -> identical: {'PASS' if pos_ok else 'FAIL'}")

    # audit/rebalance list prefix
    P(f"  strategy audit bookkeeping: record=True captured"
      f" {len(getattr(strat_full, 'audit_reb', []))} rebalance rows in the full run")

    # ------------------------------------------ truncation at start of latest seg
    P("\n[D] TRUNCATION TEST end=2024-09-13 (start of latest segment) vs full run")
    res_t24, _ = run("2024-09-13", record=True)
    nav24 = nav_frame(res_t24)
    cut2 = "2024-09-13"
    fa = got[got.index <= cut2].copy()
    fb = nav24.copy()
    n_common = min(len(fa), len(fb))
    same2 = bool(list(fa.index[:n_common]) == list(fb.index[:n_common])) and len(fa) == len(fb)
    diffs = {c: float(np.nanmax(np.abs(fa[c].astype(float).to_numpy()[:n_common]
                                     - fb[c].astype(float).to_numpy()[:n_common])))
             for c in num_cols}
    ok2 = same2 and all(diffs[c] <= (NAVTOL if c in ("nav", "cash", "market_value") else RATTOL)
                        for c in num_cols)
    P(f"  rows: full-prefix={len(fa)} truncated={len(fb)} dates_align={same2}")
    for c in num_cols:
        P(f"      {c:20s} max abs diff {diffs[c]:.3e}")
    ta24 = ta[ta["date"] <= cut2].reset_index(drop=True)
    tb24 = res_t24["trades"].copy()
    tb24["date"] = tb24["date"].astype(str)
    tb24 = tb24[tb24["date"] <= cut2].reset_index(drop=True)
    t_ok2 = len(ta24) == len(tb24) and bool(ta24[cols].astype(str).equals(tb24[cols].astype(str)))
    P(f"  trades through {cut2}: full={len(ta24)} truncated={len(tb24)} identical:"
      f" {'PASS' if t_ok2 else 'FAIL'}")
    P(f"  NAV prefix identical: {'PASS' if ok2 else 'FAIL'}")

    # --------------------------------------------------------- fill timing check
    P("\n[E] FILL TIMING: every trade executed at trade-date open with 10bp slippage")
    from engine.data import DataBundle
    bundle = DataBundle.load(REPO / "qrun_1416" / "data", START, END, warmup_days=340)
    t = res_full["trades"].copy()
    t = t[t["side"].isin(["buy", "sell"])].reset_index(drop=True)
    open_mat = bundle.field("open")
    code_idx = bundle.code_index
    n_open, n_near, n_other = 0, 0, 0
    misses = []
    for _, r in t.iterrows():
        code = str(r["code"])
        if code not in code_idx:
            n_other += 1
            continue
        try:
            i = bundle.i_of(str(r["date"]))
        except Exception:
            n_other += 1
            continue
        if i is None or i < 0:
            n_other += 1
            continue
        raw = float(open_mat[i, code_idx[code]])
        px = float(r["price"])
        if not np.isfinite(raw) or raw <= 0:
            n_other += 1
            continue
        exp = raw * (1.0 + (10.0 / 1e4) * (1 if r["side"] == "buy" else -1))
        err = abs(px - exp) / raw
        if err <= 2e-3:      # 20bp band: limit-price clamps / 2-dec rounding
            n_open += 1
            if err <= 1e-5:
                n_near += 1
        else:
            n_other += 1
            if len(misses) < 10:
                misses.append(dict(date=str(r["date"]), code=code, side=r["side"],
                                   price=px, open=raw, exp=exp, err=err))
    P(f"  trades checked={len(t)}  at OPEN*(1+/-10bp) within 20bp={n_open / max(len(t), 1):.4%}"
      f" ({n_open})  exact 1e-5={n_near}  mismatch={n_other}")
    if misses:
        for mm in misses:
            P(f"      mismatch {mm}")
    # month-end scheduling check: distribution of trade months vs rebalance days
    P(f"  first trade {t['date'].iloc[0] if len(t) else None};"
      f" last trade {t['date'].iloc[-1] if len(t) else None}")
    # regime action timing: exits on E/C state must happen the next open
    aud = getattr(strat_full, "audit_days", [])
    ac = getattr(strat_full, "audit_reb", [])
    from collections import Counter
    P(f"  recorded on_bar days={len(aud)}; recorded rebalances={len(ac)}")
    P(f"  rebalance reasons: {dict(Counter(r['reason'] for r in ac))}")
    P(f"  overlay/immediate rebalances recorded:"
      f" {sum(1 for r in ac if r['reason'] == 'overlay')} (OverlayVariant rebalances only")
    P("      on >15pp desired-exposure changes; B<->N is 10pp -> no off-month rebalance)")
    P("  risk-off exits (E/C): handled immediately in Variant.on_bar (desired<=0 ->")
    P("      _exit_all), fills next open; re-entry is month-end only in frozen mode.")

    # ------------------------------------------------------------------ summary
    P("\n" + "=" * 100)
    P("ITEM 2 SUMMARY")
    P(f"  calibration to published nav CSV : {'PASS' if ok else 'FAIL'}")
    P(f"  truncation 2019-12-31 invariance : {'PASS' if (ok_nav and trade_ok and pos_ok) else 'FAIL'}")
    P(f"  truncation 2024-09-13 invariance : {'PASS' if (ok2 and t_ok2) else 'FAIL'}")
    P(f"  open fills (slippage band)        : {n_open / max(len(t), 1):.2%} of trades")
    P("=" * 100)

    payload = dict(
        calibration=dict(max_abs_diff=maxdiff, ok=ok,
                         metrics=m, phaseG_row=row.to_dict(),
                         metrics_match_phaseG=calc.get("metrics_match_phaseG")),
        truncation_2019=dict(diffs=trunc, nav_prefix_ok=ok_nav,
                             dates_align=same_dates, trades_ok=trade_ok,
                             positions_ok=pos_ok,
                             n_trades_full=len(ta19), n_trades_trunc=len(tb19)),
        truncation_2024_09_13=dict(diffs=diffs, dates_align=same2, nav_prefix_ok=ok2,
                                   trades_ok=t_ok2, n_trades_full=len(ta24),
                                   n_trades_trunc=len(tb24)),
        fill_timing=dict(n_trades=int(len(t)), n_open=int(n_open), n_near_exact=int(n_near),
                         n_other=int(n_other), misses=misses),
        audit=dict(n_days=len(aud), n_reb=len(ac),
                   reasons={k: int(v) for k, v in Counter(r["reason"] for r in ac).items()}),
    )
    (VER / "v2_lookahead.json").write_text(json.dumps(payload, indent=1, default=str),
                                           encoding="utf-8")
    (VER / "v2_lookahead.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\nwrote", VER / "v2_lookahead.log", "and v2_lookahead.json")


if __name__ == "__main__":
    main()
