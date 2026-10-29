# -*- coding: utf-8 -*-
"""No-lookahead perturbation test for the round-3 S0 port (audit, read-only on qrun_1416).

Two independent perturbations, both comparing everything up to a fixed cutoff date T*:

  A (reference)  : the deliverable window 2014-07-01 .. 2023-12-29, unperturbed panel.
                   Its NAV/trades are also required to reproduce out/S0_nav.csv and
                   out/S0_trades.csv bit-for-bit, which proves this harness runs the same
                   engine configuration as the official runner.
  B (truncation) : same strategy, same bundle, run stops at T* = 2019-12-31.
                   If any signal silently used rows after the run window, the past would
                   change -> A and B must agree on every day <= T*.
  C (future-data): same full window, but every panel row after T* is perturbed
                   (prices x1.7, amount x3, volume x2, all consistently so no fake
                   corporate action is created).  If any signal reads a future row,
                   A and C must diverge on some day <= T*.

Why this is not circular: the strategy only ever receives a `ctx`; the engine's
accessors are truncated at `ctx.i` (see verify/NO_LOOKAHEAD.md).  B tests the window
handling, C tests that even the data actually present after T* is never consulted.

Output: verify/lookahead_perturb.log (stdout). Exit code 0 iff every equality check passes.

Run:
  python winrate_backtest_20261020/round3/verify/lookahead_perturb.py [--cutoff 2019-12-31]
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROUND3 = HERE.parent
WORKSPACE = ROUND3.parents[1]
REPO = WORKSPACE / "qrun_1416"
for p in (str(REPO), str(ROUND3)):
    if p not in sys.path:
        sys.path.insert(0, p)

import s0s1  # noqa: E402
from engine.core import Backtest, FeeModel  # noqa: E402
from engine.data import DataBundle  # noqa: E402

START = "2014-07-01"
END_FULL = "2023-12-29"          # deliverable window (run_round3.START/END)
DATA_DIR = REPO / "data"
OUT = ROUND3 / "out"

# fields perturbed in run C and the multiplicative factor
PERTURB = {
    "open": 1.7, "high": 1.7, "low": 1.7, "close": 1.7, "preclose": 1.7,
    "qfq_open": 1.7, "qfq_close": 1.7, "qfq_high": 1.7, "qfq_low": 1.7,
    "amount": 3.0, "volume": 2.0,
}

FAILS: list[str] = []
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}", flush=True)


def load_labels() -> dict:
    reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    m = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}
    return {d: m[s] for d, s in zip(reg["date"], reg["state"])}


def run_once(bundle: DataBundle, start_i: int, end_i: int, labels: dict,
             slippage_bps: float, tag: str) -> dict:
    strat = s0s1.PlatformPort(mode="S0", name=f"lookahead-{tag}")
    bt = Backtest(
        bundle, strat, start_i, end_i,
        capital=100_000.0, fees=FeeModel(), slippage_bps=slippage_bps,
        allow_st_buy=False, allow_ipo_day_trade=False, max_pending_days=20,
        exec_timing="next_open", dividend_mode="qfq_reinvest",
        dividend_min_pct=0.003, rebalance="M", regime_labels=labels,
        run_name=f"lookahead-{tag}",
    )
    t0 = time.time()
    res = bt.run()
    res["_strategy"] = strat
    res["_elapsed"] = time.time() - t0
    print(f"[run {tag}] {res['start']}..{res['end']} days={res['n_trading_days']} "
          f"final_nav={res['final_nav']:.2f} trades={len(res['trades'])} "
          f"events={len(res['events'])} {res['_elapsed']:.1f}s", flush=True)
    return res


def nav_slice(res: dict, cutoff: str) -> pd.DataFrame:
    nav = res["nav"]
    return nav.loc[nav.index <= cutoff].copy()


def compare_past(name: str, ref: dict, other: dict, cutoff: str) -> None:
    a = nav_slice(ref, cutoff)
    b = nav_slice(other, cutoff)
    if len(a) != len(b):
        check(f"{name}: same number of days <= {cutoff}", False, f"{len(a)} vs {len(b)}")
        return
    check(f"{name}: same number of days <= {cutoff}", True, f"{len(a)} days")
    if not (a.index.to_numpy() == b.index.to_numpy()).all():
        check(f"{name}: identical date index", False)
        return
    check(f"{name}: identical date index", True)
    for col in ("nav", "cash", "market_value", "exposure", "ret"):
        if col not in a.columns:
            continue
        xa = a[col].to_numpy(dtype=float)
        xb = b[col].to_numpy(dtype=float)
        bad = np.abs(xa - xb)
        n_bad = int(np.sum(~np.isclose(xa, xb, rtol=0.0, atol=1e-9, equal_nan=True)))
        check(f"{name}: {col} identical (atol=1e-9)", n_bad == 0,
              f"max|diff|={np.nanmax(bad) if len(bad) else 0:.3e} n_diff={n_bad}")
    # positions / holdings per day (engine holding_records -> results['positions'])
    pa = ref["positions"]
    pb = other["positions"]
    pa = pa[pa["date"] <= cutoff].reset_index(drop=True)
    pb = pb[pb["date"] <= cutoff].reset_index(drop=True)
    check(f"{name}: identical daily position ledger", pa.equals(pb),
          f"rows {len(pa)} vs {len(pb)}")
    # trades up to the cutoff
    ta = ref["trades"]
    tb = other["trades"]
    ta = ta[ta["date"] <= cutoff].reset_index(drop=True)
    tb = tb[tb["date"] <= cutoff].reset_index(drop=True)
    cols = [c for c in ta.columns if c in tb.columns]
    ok = len(ta) == len(tb) and ta[cols].astype(str).equals(tb[cols].astype(str))
    check(f"{name}: identical trade ledger", ok, f"rows {len(ta)} vs {len(tb)}")
    # orders up to cutoff
    # orders up to cutoff.  Orders still pending when a run stops are flushed with
    # status 'unfilled_at_end', and an order filled on the first day *after* the cutoff
    # is filled in run A but unfinished in run B; both are end-of-run artifacts, so
    # compare only orders that reached a decision by the cutoff.
    def _orders_to_cut(df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        d["created_date"] = d["created_date"].astype(str)
        d["filled_date"] = d["filled_date"].astype(str)
        d = d[d["created_date"] <= cutoff]
        d = d[d["status"].astype(str) != "unfilled_at_end"]
        d = d[~((d["status"].astype(str) == "filled") & (d["filled_date"] > cutoff))]
        return d.reset_index(drop=True)

    oa = _orders_to_cut(ref["orders"])
    ob = _orders_to_cut(other["orders"])
    cols = [c for c in oa.columns if c in ob.columns]
    ok = len(oa) == len(ob) and oa[cols].astype(str).equals(ob[cols].astype(str))
    check(f"{name}: identical order ledger", ok, f"rows {len(oa)} vs {len(ob)}")
    # rebalance decisions recorded by the strategy
    ra = [dict(r) for r in ref["_strategy"].audit_rebalances if str(r.get("date")) <= cutoff]
    rb = [dict(r) for r in other["_strategy"].audit_rebalances if str(r.get("date")) <= cutoff]
    ok = len(ra) == len(rb) and all(
        str(x.get("date")) == str(y.get("date"))
        and int(x.get("n_selected", -1)) == int(y.get("n_selected", -1))
        and int(x.get("n_candidates", -1)) == int(y.get("n_candidates", -1))
        and int(x.get("n_unaffordable", -1)) == int(y.get("n_unaffordable", -1))
        for x, y in zip(ra, rb))
    check(f"{name}: identical rebalance decisions", ok, f"{len(ra)} rebalances")


def mask_future(bundle: DataBundle, cutoff_i: int) -> DataBundle:
    """Return a *new* bundle whose panel rows after cutoff_i are perturbed in RAM.

    The source cache is mmap-read-only; every touched field is copied into RAM first so
    qrun_1416 data/cache files are never modified.
    """
    n_copied = 0
    for name, factor in PERTURB.items():
        if name not in bundle.panel.fields:
            continue
        arr = np.array(bundle.panel.fields[name], dtype=np.float32, copy=True)
        arr[cutoff_i + 1:] *= np.float32(factor)
        bundle.panel.fields[name] = arr
        n_copied += 1
    print(f"[perturb] replaced {n_copied} panel fields for rows > {bundle.date_at(cutoff_i)}", flush=True)
    return bundle


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2019-12-31")
    ap.add_argument("--slippage-bps", type=float, default=10.0,
                    help="engine slippage; does not affect the equality claim")
    args = ap.parse_args(argv)
    cutoff = args.cutoff
    labels = load_labels()
    print(f"regime labels: {len(labels)} dates, states={sorted(set(labels.values()))}", flush=True)
    print(f"repo={REPO}\ncutoff={cutoff} slippage_bps={args.slippage_bps}", flush=True)

    t0 = time.time()
    bundle_a = DataBundle.load(data_dir=DATA_DIR, start=START, end=END_FULL,
                               warmup_days=340, require_panel=True)
    print(f"[bundle] loaded {bundle_a.dates[0]}..{bundle_a.dates[-1]} "
          f"T={len(bundle_a.dates)} N={len(bundle_a.codes)} in {time.time()-t0:.1f}s", flush=True)
    bundle_c = DataBundle.load(data_dir=DATA_DIR, start=START, end=END_FULL,
                               warmup_days=340, require_panel=True)

    start_i = bundle_a.i_of(START)
    end_full = bundle_a.i_of(END_FULL)
    cutoff_i = bundle_a.i_of(cutoff)
    print(f"[indices] start_i={start_i} cutoff_i={cutoff_i} end_full={end_full}", flush=True)

    ref = run_once(bundle_a, start_i, end_full, labels, args.slippage_bps, "A-full")

    # A must be the official run: compare with the runner's raw output for S0
    print("\n=== A vs out/S0_nav.csv (official runner, same window/config) ===", flush=True)
    official_nav = OUT / "S0_nav.csv"
    if official_nav.exists():
        off = pd.read_csv(official_nav, index_col=0)
        off.index = off.index.astype(str)
        off = off.sort_index()
        mine = ref["nav"].copy()
        mine.index = mine.index.astype(str)
        mine = mine.sort_index()
        if len(off) == len(mine) and (off.index.to_numpy() == mine.index.to_numpy()).all():
            for col in ("nav", "cash", "market_value", "exposure"):
                if col not in off.columns:
                    continue
                d = np.abs(off[col].to_numpy(dtype=float) - mine[col].to_numpy(dtype=float))
                nb = int(np.sum(d > 1e-9))
                check(f"A reproduces official S0_nav.{col} (atol=1e-9)", nb == 0,
                      f"max|diff|={d.max():.3e} n_diff={nb}")
        else:
            check("A reproduces official S0_nav length/index", False,
                  f"{len(off)} vs {len(mine)}")
    else:
        check("A reproduces official S0_nav.csv", False, "out/S0_nav.csv missing")

    official_tr = OUT / "S0_trades.csv"
    if official_tr.exists():
        off_t = pd.read_csv(official_tr, dtype={"code": str, "date": str})
        mine_t = ref["trades"].copy()
        mine_t["code"] = mine_t["code"].astype(str)
        cols = [c for c in off_t.columns if c in mine_t.columns]
        bad = []

        def _norm(s: pd.Series) -> pd.Series:
            # CSV round-trip turns an empty-string note into NaN; compare as text.
            # pandas may keep NA as a real NaN after astype(str), so fillna first.
            out = s.fillna("").astype(str).str.strip()
            return out.where(~out.isin(["nan", "NaN", "None", "<NA>"]), "")

        for c in cols:
            a = off_t[c].reset_index(drop=True)
            b = mine_t[c].reset_index(drop=True)
            if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
                same = np.allclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float),
                                   rtol=0.0, atol=1e-9, equal_nan=True)
            else:
                same = _norm(a).equals(_norm(b))
            if not same:
                bad.append(c)
        check("A reproduces official S0_trades.csv",
              len(off_t) == len(mine_t) and not bad,
              f"rows {len(off_t)} vs {len(mine_t)}; differing cols={bad}")
    else:
        check("A reproduces official S0_trades.csv", False, "out/S0_trades.csv missing")

    trunc = run_once(bundle_a, start_i, cutoff_i, labels, args.slippage_bps, "B-truncated")

    mask_future(bundle_c, cutoff_i)
    pert = run_once(bundle_c, start_i, end_full, labels, args.slippage_bps, "C-future-perturbed")

    print("\n=== A (full) vs B (run truncated at cutoff) ===", flush=True)
    compare_past("truncation", ref, trunc, cutoff)
    print("\n=== A (full) vs C (future rows perturbed) ===", flush=True)
    compare_past("future-perturbation", ref, pert, cutoff)

    # sanity: the perturbation must actually bite after the cutoff, otherwise C is vacuous
    a_after = ref["nav"].loc[ref["nav"].index > cutoff, "nav"].to_numpy(dtype=float)
    c_after = pert["nav"].loc[pert["nav"].index > cutoff, "nav"].to_numpy(dtype=float)
    n = min(len(a_after), len(c_after))
    differs = (n == 0) or not np.allclose(a_after[:n], c_after[:n], rtol=0, atol=1e-9)
    check("sanity: run C's NAV after the cutoff is NOT identical to run A (perturbation is effective)",
          differs, f"n_after={n}")

    ok = not FAILS
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} — {len(CHECKS) - len(FAILS)}/{len(CHECKS)} checks passed", flush=True)
    if FAILS:
        print("failed checks:", FAILS, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
