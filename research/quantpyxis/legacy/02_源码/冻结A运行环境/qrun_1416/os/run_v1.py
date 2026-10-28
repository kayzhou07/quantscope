# -*- coding: utf-8 -*-
"""Run the frozen Bull/Bear OS v1 backtests on the independent 2014-2026 panel.

Usage:
    python os/run_v1.py                       # run all variants, write os/results/
    python os/run_v1.py --variants bullbear_v1,legacy_b_q --no-regime-rebuild
    python os/run_v1.py --list

Design notes
------------
* Signals on the close of T, execution at T+1 open (engine default next_open).
* The regime label attached to date T is the state evaluated on T's close
  (os/regime_v1.py), i.e. exactly the information available when the T-close signal is
  generated.  This is intentionally different from engine.regime's "actionable today"
  convention; the labels are passed to the engine only for segmentation/reporting.
* Every variant runs on the same panel, same fees, same capital, same window.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

from engine.api import (BuyHold, Cash, EqualWeight, run_backtest,  # noqa: E402
                        save_result_json)


def _load_local(name: str, filename: str):
    """Load os/*.py by path (the directory is named 'os', which shadows stdlib import)."""
    spec = importlib.util.spec_from_file_location(name, REPO / "os" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_regime = _load_local("os_regime_v1", "regime_v1.py")
_strategy = _load_local("os_strategy_v1", "strategy_v1.py")
_strategy2 = _load_local("os_strategy_v2", "strategy_v2.py")
_strategy3 = _load_local("os_strategy_final", "strategy_final.py")
compute_regime = _regime.compute_regime
BullBearOS = _strategy.BullBearOS
LegacyBQuarterly = _strategy.LegacyBQuarterly
BullBearOSv2 = _strategy2.BullBearOSv2
BullBearOSv3 = _strategy3.BullBearOSv3

RESULT_DIR = REPO / "os" / "results"
REGIME_CSV = REPO / "os" / "out" / "regime_v1.csv"
START = "2014-01-01"
END = "2026-10-19"


def ensure_regime(rebuild=False):
    if REGIME_CSV.exists() and not rebuild:
        df = pd.read_csv(REGIME_CSV, dtype={"date": str})
        print(f"[regime] using existing {REGIME_CSV.name}: {len(df)} rows "
              f"{df['date'].iloc[0]}..{df['date'].iloc[-1]}", flush=True)
        return df
    df = compute_regime(start="2012-08-01", end=END)
    REGIME_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(REGIME_CSV, index=False, float_format="%.6f")
    print(f"[regime] computed and wrote {REGIME_CSV}: {len(df)} rows", flush=True)
    return df


def build_variants():
    """name -> (strategy, rebalance)"""
    return {
        "bullbear_v1": (BullBearOS(), "M"),
        "bullbear_v3": (BullBearOSv3(), "Q"),
        "bullbear_v3m": (BullBearOSv3(name="BullBearOS-v3-monthly"), "M"),
        "v3_bull_only": (BullBearOSv3(use_regime=False, force_exposure=0.95, name="V3BullOnly-95"), "Q"),
        "bullbear_v2": (BullBearOSv2(), "M"),
        "v2_bull_only": (BullBearOSv2(use_regime=False, force_exposure=0.95, name="V2BullOnly-95"), "M"),
        "v2_neutral_only": (BullBearOSv2(use_regime=False, force_exposure=0.30, name="V2NeutralOnly-30"), "M"),
        "bull_only": (BullBearOS(use_regime=False, force_exposure=0.90,
                                 name="BullOnly-90"), "M"),
        "neutral_only": (BullBearOS(use_regime=False, force_exposure=0.40,
                                    name="NeutralOnly-40"), "M"),
        "legacy_b_q": (LegacyBQuarterly(), "Q"),
        "cash": (Cash(), None),
        "bh_csi300": (BuyHold("sh000300"), None),
        "bh_chinext": (BuyHold("sz399006"), None),
        "ew50": (EqualWeight(top_n=50), "M"),
    }


def load_regime_labels():
    """Engine expects {date: label}; our CSV uses column 'state' (close-based label)."""
    if not REGIME_CSV.exists():
        return {}
    df = pd.read_csv(REGIME_CSV, dtype={"date": str})
    return dict(zip(df["date"], df["state"]))


def run_variant(name, strategy, rebalance, regime_csv, start, end):
    t0 = time.time()
    labels = load_regime_labels() if regime_csv else None
    kw = dict(start=start, end=end, capital=100_000.0, data_dir=REPO / "data",
              benchmark=["sh000300", "sz399006"],
              slippage_bps=10.0, warmup_days=340,
              regime_labels=labels,
              name=name)
    if rebalance is not None:
        kw["rebalance"] = rebalance
    res = run_backtest(strategy, **kw)
    out = RESULT_DIR / f"{name}.json"
    save_result_json(res, out, include_nav=True, include_trades=True)
    s = res.get("summary", {})
    print(f"[run] {name:14s} ret={s.get('total_return', float('nan')):+.2%} "
          f"maxDD={s.get('max_drawdown', 0):.2%} "
          f"trades={s.get('trade_count', '?')} fees={s.get('total_fees', 0):.0f} "
          f"({time.time() - t0:.1f}s)", flush=True)
    return res


def summarise(results: dict):
    rows = []
    for name, res in results.items():
        s = res.get("summary", {})
        row = {"variant": name}
        row.update({k: s.get(k) for k in (
            "start", "end", "final_nav", "total_return", "cagr", "ann_vol", "sharpe",
            "sortino", "max_drawdown", "maxdd_peak_date", "maxdd_trough_date",
            "avg_exposure", "end_exposure", "all_cash_days", "trade_count", "trade_days",
            "total_fees", "total_slippage_cost", "fifo_realized_pnl", "turnover_annual")})
        rows.append(row)
    df = pd.DataFrame(rows)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULT_DIR / "summary.csv", index=False)
    print("\n" + df.to_string(index=False), flush=True)
    return df


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="all")
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    ap.add_argument("--no-regime-rebuild", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    variants = build_variants()
    if args.list:
        print("\n".join(sorted(variants)))
        return 0
    names = sorted(variants) if args.variants == "all" else \
        [x.strip() for x in args.variants.split(",") if x.strip()]
    unknown = [n for n in names if n not in variants]
    if unknown:
        raise SystemExit(f"unknown variants: {unknown}; available: {sorted(variants)}")
    ensure_regime(rebuild=not args.no_regime_rebuild)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in names:
        strategy, rebalance = variants[name]
        try:
            results[name] = run_variant(name, strategy, rebalance, REGIME_CSV,
                                        args.start, args.end)
        except Exception as exc:  # noqa: BLE001
            print(f"[run] {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
            import traceback
            traceback.print_exc()
    if results:
        summarise(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
