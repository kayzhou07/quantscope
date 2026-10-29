"""End-to-end smoke run of the qrun_1416 engine on whatever data is published.

Print the data-contract status, then:
  1. always (once indices are published): index buy-and-hold proxy via `run_backtest`
     (`require_panel=False`, works before the stock panel lands);
  2. if the stock panel is published: `EqualWeight(top_n=20)` and optionally
     `MomentumTopN(top_n=20)` reference runs with timing;
  3. regenerate/print the frozen regime label counts if the labels file exists;
  4. write a JSON result to engine/out/ and print its size.

Usage:
    python engine/run_api_smoke.py                 # auto-detect data
    python engine/run_api_smoke.py --start 2014-01-01 --end 2026-10-19
    python engine/run_api_smoke.py --full          # also run the momentum reference
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import ENGINE_VERSION                                    # noqa: E402
from engine.api import (                                             # noqa: E402
    BuyHold,
    Cash,
    EqualWeight,
    MomentumTopN,
    print_summary,
    run_backtest,
    save_result_json,
)
from engine.data import DEFAULT_DATA_DIR, data_status                 # noqa: E402
from engine.regime import OUT_DEFAULT, yearly_counts                 # noqa: E402


def _fmt_status(status: dict) -> str:
    lines = [f"engine {ENGINE_VERSION} | data_dir={status['data_dir']}"]
    lines.append(f"  manifest: {'yes' if status['manifest']['exists'] else 'NO'}  "
                 f"calendar: {'yes' if status['calendar']['exists'] else 'NO'}  "
                 f"universe: {'yes' if status['universe']['exists'] else 'NO'}")
    lines.append(f"  index_daily: {'yes' if status['index_ready'] else 'NO'}  "
                 f"panel raw years: {status['raw_years'] or 'none'}  "
                 f"qfq years: {status['qfq_years'] or 'none'}")
    return "\n".join(lines)


def _nav_rows(result: dict, n: int = 3) -> str:
    nav = result["nav"]
    cols = [c for c in ("nav", "cash", "market_value", "exposure", "ret") if c in nav.columns]
    head = nav[cols].head(n).to_string()
    tail = nav[cols].tail(n).to_string()
    return f"--- first {n} rows ---\n{head}\n--- last {n} rows ---\n{tail}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="qrun_1416 engine smoke run")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--start", default="2014-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--index-code", default="sh000300")
    parser.add_argument("--json", default=str(ROOT / "engine" / "out" / "smoke_result.json"))
    parser.add_argument("--full", action="store_true",
                        help="also run the MomentumTopN(full-universe) reference strategy")
    args = parser.parse_args(argv)

    status = data_status(args.data_dir)
    print("=" * 78)
    print("qrun_1416 engine API smoke test")
    print("=" * 78)
    print(_fmt_status(status))
    if not status["exists"]:
        print("\nDATA NOT READY: data directory does not exist yet. "
              "Engine tests can be run with synthetic fixtures instead.")
        return 0
    if not status["index_ready"]:
        print("\nDATA NOT READY: index_daily.csv.gz not published yet, so the end-to-end "
              "run cannot start. Re-run this script once data-runner publishes indices.")
        print("  (engine unit tests: python -m unittest discover -s engine/tests -v)")
        return 0

    panel_ready = status["panel_ready"]
    end = args.end
    if end is None:
        try:
            import pandas as pd
            idx = pd.read_csv(Path(args.data_dir) / "panel" / "index_daily.csv.gz",
                              usecols=["date"], dtype={"date": str})
            end = str(idx["date"].max())
        except Exception:
            end = None
    start = args.start
    print(f"\nwindow: {start} .. {end or 'last published'}  panel_ready={panel_ready}")

    failures = 0
    t0 = time.time()
    # 1) index buy & hold proxy (works before the stock panel)
    try:
        r = run_backtest(BuyHold(args.index_code), start=start, end=end,
                         data_dir=args.data_dir, require_panel=panel_ready,
                         name=f"smoke_BuyHold({args.index_code})")
        print(f"\n[1] index buy&hold proxy OK in {time.time() - t0:.1f}s")
        print_summary(r, label=f"BuyHold({args.index_code})")
        print(_nav_rows(r))
        save_result_json(r, args.json)
        size = Path(args.json).stat().st_size
        print(f"JSON export ok: {args.json} ({size / 1024:.0f} KB)")
    except Exception:
        failures += 1
        print("\n[1] index buy&hold FAILED:")
        traceback.print_exc()

    # 2) all-cash baseline
    try:
        r_cash = run_backtest(Cash(), start=start, end=end, data_dir=args.data_dir,
                              require_panel=panel_ready, name="smoke_Cash")
        m = r_cash["metrics"]
        print(f"\n[2] cash baseline OK: final_nav={m['final_nav']:.2f} "
              f"all_cash_days={m['all_cash_days']} trades={m['trade_count']}")
    except Exception:
        failures += 1
        print("\n[2] cash baseline FAILED:")
        traceback.print_exc()

    # 3) multi-stock runs when the panel is published
    if panel_ready:
        for label, strat in (("EqualWeight(top20)", EqualWeight(top_n=20)),
                             ("MomentumTopN", MomentumTopN(top_n=20)) if args.full else None):
            if strat is None:
                continue
            try:
                t = time.time()
                r2 = run_backtest(strat, start=start, end=end, data_dir=args.data_dir,
                                  name=f"smoke_{label}")
                dt = time.time() - t
                print(f"\n[3] {label} OK in {dt:.1f}s")
                print_summary(r2, label=label)
                print(_nav_rows(r2, n=2))
                seg = r2.get("regime_segments")
                if seg is not None and len(seg):
                    print("regime segments:")
                    print(seg.to_string(index=False))
            except Exception:
                failures += 1
                print(f"\n[3] {label} FAILED:")
                traceback.print_exc()

    # 4) regime labels status
    print("\n[4] regime labels")
    if OUT_DEFAULT.exists():
        try:
            import pandas as pd
            labels = pd.read_csv(OUT_DEFAULT, dtype={"date": str})
            print(f"found {OUT_DEFAULT} ({len(labels)} rows)")
            print(yearly_counts(labels).to_string())
        except Exception:
            print(f"could not read {OUT_DEFAULT}")
    else:
        print(f"not built yet: run `python -m engine.regime --out {OUT_DEFAULT}` "
              f"(engine/RULES.md is frozen)")

    print("\n" + "=" * 78)
    print(f"SMOKE {'PASS' if failures == 0 else 'FAIL'} ({failures} failure(s))")
    print("=" * 78)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
