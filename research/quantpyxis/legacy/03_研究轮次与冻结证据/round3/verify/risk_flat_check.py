# -*- coding: utf-8 -*-
"""Independent explanation of the risk-flat days flagged in RISK_FLAT_AUDIT_S0/S1.

Risk-flat day = a day the account was in the market (positions > 0 today or at the prior
close) yet the account NAV did not move by more than 1e-6 CNY (platform S0:124-147,221-224).

For every flagged day this script:
  1. re-derives the held set from POSITIONS_{tag}.csv (shares>0),
  2. compares each holding's raw close with the prior trading day and computes the implied
     P&L contribution  shares * (close_t - close_{t-1}),
  3. checks the day's TRADES ledger (a trade would move cash without a close move),
  4. for "all_held_suspended" rows verifies *directly in the raw panel files* that the code
     has no bar on those dates (so the engine's suspension carry is the real cause),
  5. checks whether the sum of contributions is 0 within the flat threshold.

Inputs (read-only):  qrun_1416/data/panel/bars_YYYY.csv.gz, bars_qfq_YYYY.csv.gz,
                     round3/out/POSITIONS_*.csv, DAILY_AUDIT_*.csv, TRADES_*.csv
Usage: python risk_flat_check.py  ->  prints a report, exit 0 if every day is explained.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROUND3 = HERE.parent
WORKSPACE = ROUND3.parents[1]
PANEL = WORKSPACE / "qrun_1416" / "data" / "panel"
OUT = ROUND3 / "out"
FLAT_EPS = 1e-6
FAILS: list[str] = []


def load_raw_bars(year: int) -> pd.DataFrame:
    path = PANEL / f"bars_{year}.csv.gz"
    df = pd.read_csv(path, dtype={"code": str, "date": str})
    return df


def bars_for(code: str, dates) -> pd.DataFrame:
    frames = []
    for y in sorted({int(d[:4]) for d in dates}):
        df = load_raw_bars(y)
        frames.append(df[df["code"] == str(code)])
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out.sort_values("date")


def main() -> int:
    daily = pd.read_csv(OUT / "DAILY_AUDIT_S0.csv", index_col=0)
    daily.index = daily.index.astype(str)
    pos = pd.read_csv(OUT / "POSITIONS_S0.csv", dtype={"date": str, "code": str})
    trades = pd.read_csv(OUT / "TRADES_S0.csv", dtype={"date": str, "code": str})
    rf = pd.read_csv(OUT / "RISK_FLAT_AUDIT_S0.csv", dtype={"date": str})

    dates = list(daily.index)
    pos_by_date = {d: g.set_index("code") for d, g in pos.groupby("date")}
    print(f"DAILY_AUDIT_S0 days={len(daily)}  positions rows={len(pos)}  "
          f"risk-flat rows={len(rf)}")
    print("=" * 110)

    for _, row in rf.iterrows():
        day = str(row["date"])
        idx = dates.index(day)
        prev = dates[idx - 1] if idx > 0 else None
        print(f"\n### {day}  classification={row['classification']}  "
              f"detail={row['detail']}  nav={row['nav']:.6f}  n_pos={row['n_positions']}")
        nav_prev = float(daily.loc[prev, "nav"]) if prev else 100_000.0
        nav = float(daily.loc[day, "nav"])
        pnl = nav - nav_prev
        print(f"    nav[{prev}]={nav_prev:.6f} -> nav[{day}]={nav:.6f}  pnl={pnl:+.9f} "
              f"(flat threshold {FLAT_EPS})")
        ok_flat = abs(pnl) <= FLAT_EPS
        print(f"    [{'PASS' if ok_flat else 'FAIL'}] day is flat by the 1e-6 definition")
        if not ok_flat:
            FAILS.append(f"{day}: not flat by definition")

        holdings = pos_by_date.get(day, pd.DataFrame())
        prev_hold = pos_by_date.get(prev, pd.DataFrame())
        tday = trades[trades["date"] == day]
        print(f"    holdings today: {list(holdings.index)}   trades today: {len(tday)}")
        if len(tday):
            print(tday[["code", "side", "shares", "price", "amount", "fee_total", "reason"]]
                  .to_string(index=False))

        contrib = {}
        for code, h in holdings.iterrows():
            c1 = h.get("close", np.nan)
            bar = h.get("has_bar", np.nan)
            sh = float(h["shares"])
            c0 = np.nan
            if prev is not None and code in prev_hold.index:
                c0 = prev_hold.loc[code, "close"]
            contrib[code] = dict(shares=sh, has_bar=bool(bar) if pd.notna(bar) else None,
                                 close_prev=c0, close=c1,
                                 pnl=(float(c1) - float(c0)) * sh
                                 if np.isfinite(c0) and np.isfinite(c1) else np.nan)
            pnl_c = contrib[code]["pnl"]
            pnl_s = f"{pnl_c:+.6f}" if np.isfinite(pnl_c) else "nan (no comparable close)"
            print(f"    {code}: shares={sh:.0f} has_bar={bar} close[{prev}]={c0} "
                  f"close[{day}]={c1} contrib={pnl_s}")
        total = np.nansum([c["pnl"] for c in contrib.values()]) if contrib else np.nan
        print(f"    sum of close-move contributions = {total:+.6f}   "
              f"trades on day = {len(tday)}")
        # a trade changes cash by -amount-fees (buy) / +amount-fees (sell); add it in
        trade_cash = 0.0
        for _, t in tday.iterrows():
            if t["side"] == "buy":
                trade_cash -= float(t["amount"]) + float(t["fee_total"])
            elif t["side"] == "sell":
                trade_cash += float(t["amount"]) - float(t["fee_total"])
        explained = total + trade_cash
        ok_expl = np.isfinite(explained) and abs(explained - pnl) <= 1e-4
        print(f"    [{'PASS' if ok_expl else 'FAIL'}] close moves + trade cash explain the "
              f"NAV change (explained={explained:+.9f} vs actual={pnl:+.9f})")
        if not ok_expl:
            FAILS.append(f"{day}: NAV change not explained by closes+trades "
                         f"(explained={explained:.9f}, actual={pnl:.9f})")

        # classification-specific verification
        cls = str(row["classification"])
        if cls == "all_held_suspended":
            for code in holdings.index:
                probe = bars_for(code, [day, prev] if prev else [day])
                has = day in set(probe["date"]) if len(probe) else False
                print(f"    panel probe {code}: raw bar rows around the window = "
                      f"{list(probe['date'])} -> has bar on {day}: {has}")
                ok = not has
                print(f"    [{'PASS' if ok else 'FAIL'}] {code} really has no bar on {day} "
                      f"(suspension carry is the cause)")
                if not ok:
                    FAILS.append(f"{day}: {code} has a bar but was classified suspended")
        elif cls == "prices_all_unchanged":
            for code, c in contrib.items():
                same = np.isfinite(c["close"]) and np.isfinite(c["close_prev"]) and \
                    abs(float(c["close"]) - float(c["close_prev"])) <= 1e-9
                print(f"    [{'PASS' if same else 'FAIL'}] {code} raw close unchanged vs "
                      f"prior day ({c['close_prev']} -> {c['close']})")
                if not same:
                    FAILS.append(f"{day}: {code} price did move but was classified unchanged")
        elif cls == "offsetting_moves":
            vals = {k: v["pnl"] for k, v in contrib.items() if np.isfinite(v["pnl"])}
            print(f"    contribution detail: {vals}   sum={sum(vals.values()):+.9f}")
            ok = abs(sum(vals.values())) <= 1.0
            print(f"    [{'PASS' if ok else 'FAIL'}] the two close moves offset (|sum| <= 1 CNY)")
            if not ok:
                FAILS.append(f"{day}: offsetting moves do not offset (sum={sum(vals.values())})")
        print("-" * 110)

    print(f"\nRESULT: {'PASS' if not FAILS else 'FAIL'} — {len(rf)} risk-flat days, "
          f"{len(FAILS)} problems")
    if FAILS:
        for f in FAILS:
            print("  -", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
