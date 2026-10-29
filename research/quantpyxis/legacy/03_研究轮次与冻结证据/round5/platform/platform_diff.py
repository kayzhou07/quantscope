# -*- coding: utf-8 -*-
"""First-divergence locator: local S0 vs platform exports (P2-2).

Inputs (all CSV, UTF-8, header required):
  --platform-daily    date,nav,cash,market_value,positions_count
  --platform-positions (optional) date,code,shares,market_value
  --platform-trades    (optional) date,code,side,shares,price,amount,fees
  --local-daily        default round3/out/DAILY_AUDIT_S0.csv (date index, nav column)
  --local-positions    (optional) round3/out/POSITIONS_S0.csv
  --local-trades       (optional) round3/out/TRADES_S0.csv

Output: first_divergence.json + a layer table skeleton.  The tool never guesses a cause: layers
without platform evidence are reported as UNVERIFIED.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

WS = Path(__file__).resolve().parents[3]
R3 = WS / "winrate_backtest_20261020" / "round3" / "out"


def _load_daily(path: Path, col: str = "nav") -> pd.Series:
    df = pd.read_csv(path, dtype={"date": str})
    if "date" not in df.columns:
        df = pd.read_csv(path, dtype={"date": str}, index_col=0)
        df.index = df.index.astype(str)
        return df[col].astype(float)
    return pd.Series(df[col].astype(float).to_numpy(), index=df["date"].astype(str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform-daily", required=True)
    ap.add_argument("--platform-positions")
    ap.add_argument("--platform-trades")
    ap.add_argument("--local-daily", default=str(R3 / "DAILY_AUDIT_S0.csv"))
    ap.add_argument("--local-positions", default=str(R3 / "POSITIONS_S0.csv"))
    ap.add_argument("--local-trades", default=str(R3 / "TRADES_S0.csv"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "first_divergence.json"))
    ap.add_argument("--tol", type=float, default=1e-6)
    args = ap.parse_args(argv)

    plat = _load_daily(Path(args.platform_daily))
    local = _load_daily(Path(args.local_daily))
    common = plat.index.intersection(local.index)
    if len(common) == 0:
        print("no common dates"); return 2
    diff = (plat.reindex(common) - local.reindex(common)).abs()
    over = diff[diff > args.tol]
    first = over.index[0] if len(over) else None
    out = {"first_divergence": first, "n_common_days": int(len(common)),
           "n_days_beyond_tol": int(len(over)), "max_abs_diff": float(diff.max()),
           "layer_table": [], "status": "OK" if first else "NO_DIVERGENCE_FOUND"}
    if first is not None:
        d_prev_candidates = [d for d in local.index if d < first]
        d_prev = d_prev_candidates[-1] if d_prev_candidates else None
        layers = [
            ("data", "compare raw/qfq close, suspension flags and corporate actions on the first divergent day"),
            ("universe", "compare PIT universe membership (platform get_all_securities vs local universe.csv)"),
            ("features", "compare 260-day window validity (>=230 bars), MA60/MA120, 61-return vol"),
            ("ranks", "compare the cross-sectional score ranks of the top candidates"),
            ("candidates", "compare top-10 selection and the rank<20 retention buffer"),
            ("lot", "compare per-name budget and one-lot affordability"),
            ("target_shares", "compare target share counts"),
            ("open_fill", "compare raw open, slippage, limit-up/down and suspension block"),
            ("fees", "compare commission/stamp/transfer"),
            ("corporate_action", "compare dividend/split share multipliers"),
        ]
        for name, todo in layers:
            out["layer_table"].append({"layer": name, "status": "UNVERIFIED",
                                       "evidence": todo})
        out["prev_day"] = d_prev
        out["local_prev_positions"] = _positions_on(Path(args.local_positions), d_prev)
        if args.platform_positions:
            out["platform_prev_positions"] = _positions_on(Path(args.platform_positions), d_prev)
        out["local_first_day_trades"] = _trades_on(Path(args.local_trades), first)
        if args.platform_trades:
            out["platform_first_day_trades"] = _trades_on(Path(args.platform_trades), first)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("first_divergence", "n_days_beyond_tol",
                                          "max_abs_diff", "status")}, indent=1))
    return 0


def _positions_on(path: Path, date: str | None):
    if date is None or not path.exists():
        return None
    df = pd.read_csv(path, dtype={"date": str, "code": str})
    sub = df[df["date"] == date]
    if len(sub) == 0:
        return None
    return sub.set_index("code")["shares"].astype(float).sort_index().to_dict()


def _trades_on(path: Path, date: str):
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"date": str, "code": str})
    sub = df[df["date"] == date]
    if len(sub) == 0:
        return []
    cols = [c for c in ("code", "side", "shares", "price", "amount", "fee_total") if c in sub]
    return sub[cols].to_dict("records")


if __name__ == "__main__":
    raise SystemExit(main())
