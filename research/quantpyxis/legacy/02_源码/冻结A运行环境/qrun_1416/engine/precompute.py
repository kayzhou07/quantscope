"""Pre-build the panel cache (and optionally regime labels) before a long session.

Usage:
    python engine/precompute.py --data-dir data --start 2014-01-01 --end 2026-10-19
    python engine/precompute.py --regime            # also write engine/out/regime_labels.csv
    python engine/precompute.py --clear             # remove the cache directory first

The first run pivots every published `data/panel/bars_{year}.csv.gz` /
`bars_qfq_{year}.csv.gz` into float32 memory-mapped matrices under
`tmp/engine-dev/cache/panel_<signature>/`; later `run_backtest` calls load instantly and
the cache is rebuilt automatically when the underlying files change.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import ENGINE_VERSION                      # noqa: E402
from engine.data import (                              # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_DATA_DIR,
    DataBundle,
    DataNotReadyError,
    data_status,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="qrun_1416 engine precompute")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--start", default="2014-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--warmup-days", type=int, default=320)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--clear", action="store_true", help="delete the cache first")
    parser.add_argument("--regime", action="store_true", help="also build regime labels")
    parser.add_argument("--regime-out", default=None)
    parser.add_argument("--index-code", default="sh000300")
    args = parser.parse_args(argv)

    status = data_status(args.data_dir)
    print(f"engine {ENGINE_VERSION} | data_dir={status['data_dir']}")
    print(f"  calendar={'yes' if status['calendar']['exists'] else 'NO'} "
          f"universe={'yes' if status['universe']['exists'] else 'NO'} "
          f"index={'yes' if status['index_ready'] else 'NO'}")
    print(f"  raw panel years={status['raw_years']}  qfq years={status['qfq_years']}")

    if args.clear:
        cache = Path(args.cache_dir)
        if cache.exists():
            shutil.rmtree(cache)
            print(f"  cleared {cache}")

    if not status["index_ready"] and not status["panel_ready"]:
        print("DATA NOT READY: no index and no stock panel published yet - nothing to precompute.")
        return 0

    include_panel = status["panel_ready"]
    t0 = time.time()
    bundle = DataBundle.load(data_dir=args.data_dir, start=args.start, end=args.end,
                             warmup_days=args.warmup_days, cache_dir=args.cache_dir,
                             require_panel=include_panel, verbose=True)
    dt = time.time() - t0
    print(f"  loaded {len(bundle.dates)} dates x {bundle.n_stocks()} stocks in {dt:.1f}s")
    print(f"  window {bundle.date_at(0)} .. {bundle.date_at(len(bundle.dates) - 1)}, "
          f"run range {bundle.date_at(bundle._start_i)} .. {bundle.date_at(bundle._end_i)}")
    print(f"  qfq available: {bundle.panel.qfq_available}")
    print(f"  indices: {sorted(bundle.index_dict)}")
    if bundle.warnings:
        for w in bundle.warnings[:10]:
            print(f"  warning: {w}")

    if args.regime:
        from engine.regime import export_regime_labels
        t1 = time.time()
        out = args.regime_out
        export_regime_labels(data_dir=args.data_dir, start=args.start, end=args.end,
                             index_code=args.index_code, out=out, verbose=True)
        print(f"  regime labels written in {time.time() - t1:.1f}s")
    else:
        print("  (add --regime to also build engine/out/regime_labels.csv)")
    print("PRECOMPUTE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
