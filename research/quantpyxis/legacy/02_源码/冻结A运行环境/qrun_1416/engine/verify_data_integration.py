"""Real-data integration audit for the published contract (engine-dev).

Run after data-runner publishes `data/panel/bars_{year}.csv.gz` + `bars_qfq_{year}.csv.gz`:

    python engine/verify_data_integration.py                     # audit + reference runs
    python engine/verify_data_integration.py --no-runs           # audit only
    python engine/verify_data_integration.py --regime            # also rebuild full regime labels
    python engine/verify_data_integration.py --baostock --write-report engine/out/REAL_DATA_INTEGRATION.md

Checks / reports
  1. data_status + panel shape, load time (cache cold/warm), warnings
  2. column coverage, suspension counts, universe/panel code coverage
  3. preclose convention detection: ex-date preclose = ex-rights reference price vs plain
     previous close, cross-checked against the qfq total-return implied multiplier
  4. corporate-action audit: branch counts (preclose_reference vs qfq_ratio) over a random
     sample of names (BuyHold each), multiplier distribution, suspicious events,
     P&L reconciliation gap (must be ~0)
  5. data-defect scan: extreme daily moves, price levels, qfq anchor factor, optional
     baostock raw/qfq spot cross-check
  6. reference strategy runs with timing (BuyHold stock, EqualWeight(top20),
     MomentumTopN momentum full-universe) - perf target < 60 s for 13 years
  7. optional: full-rule regime labels on real data (per-year counts)
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from engine import ENGINE_VERSION                                     # noqa: E402
from engine.api import BuyHold, EqualWeight, MomentumTopN, run_backtest  # noqa: E402
from engine.data import (                                             # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_DATA_DIR,
    DataBundle,
    data_status,
)

START, END = "2014-01-01", "2026-10-19"


def peak_rss_gb() -> float:
    """Peak working set (GB) on Windows via psapi; 0.0 if unavailable."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]
        counters = PMC()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        fn = getattr(ctypes.windll.psapi, "GetProcessMemoryInfo")
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        if fn(handle, ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize / 1024.0 / 1024.0 / 1024.0
    except Exception:  # noqa: BLE001
        pass
    return 0.0


class Audit:
    """Collects every printed line so the integration report can be written verbatim."""

    def __init__(self):
        self.lines: list[str] = []
        self.facts: dict = {}

    def log(self, msg: str = ""):
        print(msg)
        self.lines.append(msg)

    def section(self, title: str):
        self.log("\n" + "=" * 100)
        self.log(title)
        self.log("=" * 100)


def audit_status(a: Audit, data_dir: Path) -> dict:
    a.section("DATA STATUS")
    status = data_status(data_dir)
    a.log(f"engine {ENGINE_VERSION}")
    for k in ("exists", "panel_ready", "qfq_ready", "index_ready", "raw_years",
              "qfq_years", "manifest"):
        a.log(f"  {k}: {status.get(k)}")
    a.facts["status"] = {k: status.get(k) for k in
                         ("panel_ready", "qfq_ready", "index_ready",
                          "raw_years", "qfq_years", "manifest")}
    return status


def audit_panel(a: Audit, bundle: DataBundle) -> dict:
    p = bundle.panel
    T, N = len(bundle.dates), len(bundle.codes)
    close = p.fields["close"]
    ts = p.fields["tradestatus"]
    qfq = p.fields["qfq_close"]
    pre = p.fields["preclose"]
    a.section("PANEL AUDIT")
    a.log(f"panel: {T} dates x {N} codes; qfq_available={p.qfq_available}")
    a.log(f"window: {bundle.date_at(0)} .. {bundle.date_at(T - 1)}; "
          f"run range: {bundle.date_at(bundle._start_i)} .. {bundle.date_at(bundle._end_i)}")
    finite = np.isfinite(close)
    a.log(f"finite raw close cells: {int(finite.sum()):,} / {T * N:,} "
          f"({finite.mean():.2%})")
    traded = ts >= 0.5
    a.log(f"stock-days suspended/missing: {int((~traded).sum()):,} ({(~traded).mean():.2%})")
    uni_codes = set(str(c) for c in bundle.universe.codes[bundle.universe.known])
    panel_codes = set(str(c) for c in bundle.codes)
    a.log(f"universe known codes: {len(uni_codes)}, panel codes: {len(panel_codes)}, "
          f"universe missing from panel: {len(uni_codes - panel_codes)}, "
          f"panel codes not in universe: {len(panel_codes - uni_codes)}")
    n_never = int((~finite).all(axis=0).sum())
    a.log(f"codes with no bars in loaded window: {n_never}")
    # corporate-action candidate mask (reference preclose != prev close)
    prev_close = np.full_like(close, np.nan)
    prev_close[1:] = close[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        g = qfq[1:] / qfq[:-1]
        r = close[1:] / close[:-1]
        m_qfq = g / r
        m_ref = prev_close[1:] / pre[1:]
    ref_mask = (np.isfinite(pre[1:]) & np.isfinite(prev_close[1:])
                & (np.abs(pre[1:] - prev_close[1:]) > 0.0105) & traded[1:])
    ref_valid = ref_mask & np.isfinite(m_qfq) & np.isfinite(m_ref)
    # engine branch logic (frozen thresholds): path1 reference; else path2 qfq ratio
    path1 = (ref_mask & (m_ref >= 0.2) & (m_ref <= 3.0) & (np.abs(m_ref - 1.0) >= 5e-4))
    q0 = qfq[:-1]
    q1 = qfq[1:]
    tol = np.maximum(0.0025,
                     0.6 * (0.01 / np.maximum(q0, 1e-6) + 0.01 / np.maximum(q1, 1e-6)))
    path2 = (~path1) & np.isfinite(m_qfq) & (np.abs(m_qfq - 1.0) > tol) & traded[1:]
    a.log(f"\ncorporate-action candidate rows (reference preclose != prev close): "
          f"{int(ref_mask.sum()):,}")
    a.log(f"engine branch-1 (preclose_reference) rows: {int(path1.sum()):,}")
    a.log(f"engine branch-2 (qfq_ratio) rows: {int(path2.sum()):,}")
    if ref_valid.sum():
        ratio = m_ref[ref_valid] / m_qfq[ref_valid]
        within2 = float(np.mean(np.abs(ratio - 1) <= 0.02))
        a.log(f"m_ref vs qfq-implied agreement: median={np.median(ratio):.6f}, "
              f"p1={np.percentile(ratio, 1):.6f}, p99={np.percentile(ratio, 99):.6f}, "
              f"within 2%={within2:.4%}")
        a.log(f"  -> preclose convention: "
              f"{'EX-RIGHTS REFERENCE PRICE (branch 1 authoritative)' if within2 > 0.9 else 'MIXED/plain prev-close'}")
    # qfq anchor check: factor should be 1.0 on the last date
    with np.errstate(invalid="ignore", divide="ignore"):
        fac = qfq / close
    last = fac[-1]
    finite_last = np.isfinite(last)
    anchor_ok = float(np.mean(np.abs(last[finite_last] - 1.0) <= 1e-4)) \
        if finite_last.any() else 0.0
    a.log(f"qfq anchor on last date: {anchor_ok:.2%} of codes with factor==1.0 "
          f"(median factor={np.nanmedian(last):.6f})")
    # extreme daily moves on non-corporate days
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = close[1:] / close[:-1] - 1.0
    clean = np.where(ref_mask, np.nan, ret)
    extreme = np.isfinite(clean) & (np.abs(clean) > 0.21)
    a.log(f"extreme non-corporate daily moves (|ret|>21%): {int(extreme.sum()):,}")
    if extreme.sum():
        rows = []
        for t, j in np.argwhere(extreme)[:10]:
            rows.append({"date": bundle.date_at(int(t) + 1),
                         "code": str(bundle.codes[j]),
                         "ret": round(float(clean[t, j]), 4)})
        a.log("  examples: " + str(rows))
    # price-level sanity: last/first across loaded window
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio_life = np.where(finite[-1], close[-1] / close[0], np.nan)
    a.log(f"lifetime raw price ratio last/first: "
          f"p1={np.nanpercentile(ratio_life, 1):.3f}, median={np.nanmedian(ratio_life):.3f}, "
          f"p99={np.nanpercentile(ratio_life, 99):.3f}")
    a.facts["panel"] = {"T": T, "N": N, "finite_pct": float(finite.mean()),
                        "susp_pct": float((~traded).mean()),
                        "ca_ref_rows": int(ref_mask.sum()),
                        "branch1": int(path1.sum()), "branch2": int(path2.sum()),
                        "universe_missing": len(uni_codes - panel_codes),
                        "extreme_moves": int(extreme.sum())}
    return a.facts["panel"]


def audit_corporate_actions_sample(a: Audit, bundle: DataBundle, data_dir: Path,
                                   n_sample: int = 60, seed: int = 1416) -> dict:
    a.section(f"CORPORATE-ACTION BRANCH AUDIT on a random sample of {n_sample} names "
              f"(BuyHold each)")
    rng = np.random.default_rng(seed)
    codes = np.array([str(c) for c in bundle.codes])
    if len(codes) > n_sample:
        codes = rng.choice(codes, size=n_sample, replace=False)
    events = []
    gaps = []
    for code in codes:
        try:
            r = run_backtest(BuyHold(str(code)), start=START, end=END,
                             data_dir=data_dir, name=f"ca_{code}")
        except Exception as exc:  # noqa: BLE001
            a.log(f"  {code}: run failed: {exc}")
            continue
        ev = r["events"]
        corp = ev[ev["type"] == "corporate_action"] if len(ev) else pd.DataFrame()
        for _, e in corp.iterrows():
            events.append({"code": str(code), "date": e["date"],
                           "mult": float(e["multiplier"]), "source": e["source"]})
        gaps.append(r["metrics"]["pnl_reconciliation_gap"])
    ev_df = pd.DataFrame(events)
    a.log(f"runs: {len(codes)}, total corporate actions: {len(ev_df)}")
    if len(ev_df):
        a.log(f"  sources: {ev_df['source'].value_counts().to_dict()}")
        mult = ev_df["mult"]
        a.log(f"  multipliers: min={mult.min():.4f} median={mult.median():.4f} "
              f"p95={mult.quantile(0.95):.4f} max={mult.max():.4f} "
              f"splits(>=1.5)={int((mult >= 1.5).sum())} reverse(<0.95)={int((mult < 0.95).sum())}")
        odd = ev_df[(ev_df["mult"] > 3.0) | (ev_df["mult"] < 0.9)]
        if len(odd):
            a.log("  suspicious multipliers: " + str(odd.head(10).to_dict("records")))
        a.log("  largest: " + str(ev_df.sort_values("mult", ascending=False)
                                    .head(5).to_dict("records")))
    a.log(f"P&L reconciliation: max |gap| = {max(abs(g) for g in gaps):.6f} CNY "
          f"(sum={sum(gaps):.6f})")
    a.facts["corporate_actions"] = {"n_sample": len(codes), "n_events": len(ev_df),
                                    "sources": ev_df["source"].value_counts().to_dict()
                                    if len(ev_df) else {},
                                    "max_abs_gap": float(max(abs(g) for g in gaps))}
    return a.facts["corporate_actions"]


def audit_baostock_spot(a: Audit, bundle: DataBundle, n_codes: int = 12) -> dict:
    """Cross-check raw + qfq closes of a random sample against baostock (if available)."""
    a.section(f"BAOSTOCK SPOT CROSS-CHECK ({n_codes} random codes x 4 dates)")
    try:
        sys.path.insert(0, str(ROOT / "libs"))
        import baostock as bs
    except Exception as exc:  # noqa: BLE001
        a.log(f"baostock not importable ({exc}); skipped")
        return {}
    lg = bs.login()
    if lg.error_code != "0":
        a.log(f"baostock login failed: {lg.error_msg}")
        return {}
    rng = np.random.default_rng(7)
    codes = np.array([str(c) for c in bundle.codes])
    if len(codes) > n_codes:
        codes = rng.choice(codes, size=n_codes, replace=False)
    dates = ["2014-01-02", "2018-01-02", "2021-01-04", "2026-10-19"]
    rows = []
    try:
        for code in codes:
            bs_code = f"sh.{code}" if code.startswith(("6", "9")) else f"sz.{code}"
            for d in dates:
                try:
                    j = bundle.code_index[code]
                    i = bundle.i_of(d)
                except KeyError:
                    continue
                raw_panel = float(bundle.field("close")[i, j])
                qfq_panel = float(bundle.field("qfq_close")[i, j])
                rs = bs.query_history_k_data_plus(bs_code, "date,close", start_date=d,
                                                  end_date=d, frequency="d", adjustflag="3")
                got = rs.get_row_data() if rs.error_code == "0" and rs.next() else None
                raw_bs = float(got[1]) if got and got[1] else np.nan
                rs2 = bs.query_history_k_data_plus(bs_code, "date,close", start_date=d,
                                                   end_date=d, frequency="d", adjustflag="2")
                got2 = rs2.get_row_data() if rs2.error_code == "0" and rs2.next() else None
                qfq_bs = float(got2[1]) if got2 and got2[1] else np.nan
                rows.append({
                    "code": code, "date": d, "raw_panel": raw_panel, "raw_bs": raw_bs,
                    "qfq_panel": round(qfq_panel, 4) if np.isfinite(qfq_panel) else qfq_panel,
                    "qfq_bs": round(qfq_bs, 4) if np.isfinite(qfq_bs) else qfq_bs,
                    "raw_ok": abs(raw_panel - raw_bs) <= 0.02 if np.isfinite(raw_bs) else None,
                    "qfq_ok": abs(qfq_panel / qfq_bs - 1) <= 0.002
                    if np.isfinite(qfq_bs) and qfq_bs else None})
    finally:
        bs.logout()
    df = pd.DataFrame(rows)
    a.log(df.to_string(index=False))
    ok_raw = df["raw_ok"].dropna()
    ok_qfq = df["qfq_ok"].dropna()
    a.log(f"raw close match: {int(ok_raw.sum())}/{len(ok_raw)}; "
          f"qfq relative match (<=0.2%): {int(ok_qfq.sum())}/{len(ok_qfq)}")
    a.facts["baostock_spot"] = {"raw_ok": int(ok_raw.sum()), "raw_n": int(len(ok_raw)),
                                "qfq_ok": int(ok_qfq.sum()), "qfq_n": int(len(ok_qfq))}
    return a.facts["baostock_spot"]


def reference_runs(a: Audit, bundle: DataBundle) -> dict:
    a.section("REFERENCE RUNS (13-year daily, real panel)")
    out = {}
    runs = [
        ("BuyHold(600036)", BuyHold("600036"), dict(rebalance=None)),
        ("EqualWeight(top20)", EqualWeight(top_n=20), dict(rebalance="M")),
        ("MomentumTopN(lb120,top20)", MomentumTopN(lookback=120, top_n=20),
         dict(rebalance="M")),
    ]
    for label, strat, extra in runs:
        t0 = time.time()
        r = run_backtest(strat, start=START, end=END, data_dir=bundle.data_dir,
                         name=label, **extra)
        dt = time.time() - t0
        m = r["metrics"]
        a.log(f"\n{label}: {dt:.1f}s | final_nav={m['final_nav']:,.2f} "
              f"ret={m['total_return']:.2%} cagr={m['cagr']:.2%} vol={m['ann_vol']:.2%} "
              f"sharpe={m['sharpe']:.3f} maxDD={m['max_drawdown']:.2%} "
              f"({m['maxdd_peak_date']}->{m['maxdd_trough_date']})")
        a.log(f"  avg_exposure={m['avg_exposure']:.2%} all_cash_days={m['all_cash_days']} "
              f"trades={m['trade_count']} trade_days={m['trade_days']} "
              f"fees={m['total_fees']:,.2f} turnover/yr={m['turnover_annual']:.2f} "
              f"implicit_div={m['implicit_dividends']:,.2f}")
        a.log(f"  P&L reconciliation gap={m['pnl_reconciliation_gap']:.6f} "
              f"corporate_action_rows={m['fifo_adjust_events']}")
        bc = r["benchmark_compare"]
        if len(bc):
            row = bc.iloc[0]
            a.log(f"  vs {row['benchmark']}: bench_ret={row['benchmark_total_return']:.2%} "
                  f"excess={row['excess_total_return']:+.2%} beta={row['beta']:.2f} "
                  f"corr={row['corr']:.2f}")
        out[label] = {"seconds": dt, "cagr": m["cagr"], "maxdd": m["max_drawdown"],
                      "sharpe": m["sharpe"], "trades": m["trade_count"],
                      "avg_exposure": m["avg_exposure"],
                      "gap": m["pnl_reconciliation_gap"]}
        if label.startswith("Momentum"):
            a.log(f"  peak RSS={peak_rss_gb():.2f} GB")
    if "MomentumTopN(lb120,top20)" in out:
        sec = out["MomentumTopN(lb120,top20)"]["seconds"]
        a.log(f"\nPERF TARGET (<60s after precompute): MomentumTopN 13y = {sec:.1f}s -> "
              f"{'PASS' if sec < 60 else 'FAIL'}")
        a.facts["perf_ok"] = sec < 60
    a.facts["runs"] = out
    return out


def build_regime(a: Audit, bundle: DataBundle):
    a.section("FULL-RULE REGIME LABELS on real data")
    from engine.regime import build_regime_labels, yearly_counts
    t0 = time.time()
    out = ROOT / "engine" / "out" / "regime_labels.csv"
    # warmup_days=340 matches the precompute cache signature (2012-08-03 start):
    # 340 rows before 2014-01-02 is enough for MA250 + 20d-vol percentile + 250d high.
    labels = build_regime_labels(bundle.data_dir, start=START, end=END, out_csv=out,
                                 include_breadth=True, warmup_days=340, verbose=True)
    a.log(f"built {len(labels)} labels in {time.time() - t0:.1f}s "
          f"(variant={labels['variant'].iloc[0] if len(labels) else 'n/a'}) -> {out}")
    counts = yearly_counts(labels)
    a.log(counts.to_string())
    idx_only_path = ROOT / "engine" / "out" / "regime_labels_index_only.csv"
    if idx_only_path.exists():
        idx_df = pd.read_csv(idx_only_path, dtype={"date": str})
        merged = labels[["date", "regime"]].merge(idx_df[["date", "regime"]],
                                                  on="date", suffixes=("_full", "_idx"))
        same = float((merged["regime_full"] == merged["regime_idx"]).mean())
        a.log(f"\nfull vs index-only agreement on overlapping dates: {same:.2%} "
              f"({len(merged)} days)")
        a.facts["regime_full_vs_index_only"] = same
    a.facts["regime"] = {str(k): int(v) for k, v in
                         labels["regime"].value_counts().to_dict().items()}
    return labels


def write_report(a: Audit, data_dir: Path, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# qrun_1416 - real-data engine integration report", "",
            f"Generated by `engine/verify_data_integration.py` on "
            f"{pd.Timestamp.now():%Y-%m-%d %H:%M}.",
            f"Data dir: `{data_dir}`. Engine `{ENGINE_VERSION}`. "
            f"Window {START} .. {END}.", "", "```text"] + a.lines + ["```", ""]
    path.write_text("\n".join(body), encoding="utf-8")
    print(f"\nreport written to {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="qrun_1416 real-data engine audit")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    parser.add_argument("--no-runs", action="store_true", help="audit only, no strategy runs")
    parser.add_argument("--regime", action="store_true", help="also rebuild regime labels")
    parser.add_argument("--baostock", action="store_true", help="cross-check sample vs baostock")
    parser.add_argument("--ca-sample", type=int, default=60,
                        help="number of random names for the corporate-action branch audit")
    parser.add_argument("--clear-cache", action="store_true")
    parser.add_argument("--write-report", default=None)
    args = parser.parse_args(argv)

    a = Audit()
    status = audit_status(a, Path(args.data_dir))
    if not status["panel_ready"]:
        a.log("\nSTOCK PANEL NOT PUBLISHED YET - nothing to audit (index-only runs work via "
              "require_panel=False, see engine/run_api_smoke.py).")
        return 0
    if args.clear_cache:
        import shutil
        cache = Path(DEFAULT_CACHE_DIR)
        if cache.exists():
            shutil.rmtree(cache)
            a.log(f"cleared cache {cache}")
    t0 = time.time()
    bundle = DataBundle.load(args.data_dir, start=args.start, end=args.end,
                             warmup_days=340, verbose=True)
    cold = time.time() - t0
    t1 = time.time()
    _ = DataBundle.load(args.data_dir, start=args.start, end=args.end, warmup_days=340)
    warm = time.time() - t1
    a.log(f"\nloader: cold(build cache)={cold:.1f}s warm(mmap cache)={warm:.1f}s")
    a.facts["loader"] = {"cold": cold, "warm": warm}
    for w in bundle.warnings[:25]:
        a.log(f"  warning: {w}")
    audit_panel(a, bundle)
    try:
        audit_corporate_actions_sample(a, bundle, Path(args.data_dir),
                                       n_sample=args.ca_sample)
    except Exception:  # noqa: BLE001
        a.log("corporate-action sample audit failed:\n" + traceback.format_exc())
    if args.baostock:
        try:
            audit_baostock_spot(a, bundle)
        except Exception:  # noqa: BLE001
            a.log("baostock spot-check failed:\n" + traceback.format_exc())
    if not args.no_runs:
        try:
            reference_runs(a, bundle)
        except Exception:  # noqa: BLE001
            a.log("reference runs failed:\n" + traceback.format_exc())
    if args.regime:
        try:
            build_regime(a, bundle)
        except Exception:  # noqa: BLE001
            a.log("regime build failed:\n" + traceback.format_exc())
    if args.write_report:
        write_report(a, Path(args.data_dir), Path(args.write_report))
    a.section("AUDIT DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
