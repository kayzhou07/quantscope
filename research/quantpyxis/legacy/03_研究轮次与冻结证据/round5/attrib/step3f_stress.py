# -*- coding: utf-8 -*-
"""STEP 3f: four stress windows, day-by-day ledger reconstruction.

For every trading day in each pre-registered window the table records the chain
    signal date -> target book -> sell requests -> blocked exits -> fills -> NAV
exactly as executed by frozen strategy A (the daily rows are reconstructed from A's
own ledgers: a_nav / a_positions / a_trades / a_orders / a_events / a_target_books /
a_call_log; no rule is changed and no independent replay is claimed).

Outputs: stress_daily_<window>.csv (4 files), stress_summary.csv, stress_summary.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attrib_common as C  # noqa: E402


def book_timeline(calls, books):
    wmap = {}
    for (d, s), g in books.groupby(["date", "seq"], sort=False):
        wmap[(str(d), int(s))] = {str(c): float(w) for c, w in zip(g["code"], g["weight"])}
    tl = []
    for r in calls[calls["call"] == "set_targets"].itertuples():
        tl.append(dict(date=str(r.date), seq=int(r.seq), note=str(r.note),
                       weights=wmap.get((str(r.date), int(r.seq)), {})))
    return tl


def effective(tl, day):
    eff = None
    for rec in tl:
        if rec["date"] < day:
            eff = rec
        else:
            break
    return eff


def main():
    led = C.load_ledgers()
    nav = led["nav"]
    pos = led["positions"]
    tr = led["trades"]
    ev = led["events"]
    od = led["orders"]
    tl = book_timeline(led["calls"], led["books"])
    dates_all = list(nav.index)
    pos_by_date = {d: g for d, g in pos.groupby("date", sort=False)}
    tr_by_date = {d: g for d, g in tr.groupby("date", sort=False)}
    nav_ret = nav["nav"].astype(float).pct_change(fill_method=None)
    ev["date"] = ev["date"].astype(str)

    summary_rows = []
    for tag, ws, we in C.STRESS_WINDOWS:
        sub_dates = [d for d in dates_all if ws <= d <= we]
        if not sub_dates:
            print(f"[skip] {tag}: no dates")
            continue
        # base NAV = day before the window (so the window return is the actual P&L)
        i0 = dates_all.index(sub_dates[0])
        base_nav = float(nav["nav"].iloc[i0 - 1]) if i0 > 0 else float(nav["nav"].iloc[i0])
        i0 = dates_all.index(sub_dates[0])
        pre_day = dates_all[i0 - 1] if i0 > 0 else sub_dates[0]
        held_start = set(pos_by_date.get(pre_day, pd.DataFrame()).get("code", []))
        rows = []
        prev_book = effective(tl, sub_dates[0])
        new_entries = set()
        for d in sub_dates:
            eff = effective(tl, d)
            tset = set(eff["weights"]) if eff else set()
            held = pos_by_date.get(d)
            held_codes = list(held["code"]) if held is not None else []
            extra = [c for c in held_codes if c not in tset]
            # new target names that became effective for this day
            new_targets = sorted(tset - (set(prev_book["weights"]) if prev_book else set()))
            # explicit sell requests issued at this day's close (executed next open)
            sells_req = led["calls"][(led["calls"]["date"] == d) &
                                     (led["calls"]["call"] == "sell")]
            buy_fills = tr_by_date.get(d)
            n_buy = n_sell = 0.0
            buy_amt = sell_amt = 0.0
            if buy_fills is not None and len(buy_fills):
                b = buy_fills[buy_fills["side"] == "buy"]
                s = buy_fills[buy_fills["side"] == "sell"]
                n_buy, n_sell = len(b), len(s)
                buy_amt, sell_amt = float(b["amount"].sum()), float(s["amount"].sum())
                for c in b["code"]:
                    if c not in held_start:
                        new_entries.add(c)
            blk = ev[(ev["date"] == d) & (ev["type"] == "target_blocked")]
            blk_sell = blk[blk["side"] == "sell"]
            blk_buy = blk[blk["side"] == "buy"]
            rows.append(dict(
                date=d, nav=float(nav.at[d, "nav"]),
                daily_pnl=float(nav["nav"].loc[d] - base_nav) if d == sub_dates[0]
                else float(nav["nav"].loc[d] - nav["nav"].loc[sub_dates[sub_dates.index(d) - 1]]),
                daily_ret=float(nav_ret.at[d]) if np.isfinite(nav_ret.at[d]) else 0.0,
                cash=float(nav.at[d, "cash"]), market_value=float(nav.at[d, "market_value"]),
                exposure=float(nav.at[d, "exposure"]), regime=nav.at[d, "regime"],
                n_positions=int(nav.at[d, "n_positions"]),
                signal_date=eff["date"] if eff else "",
                book_n=int(len(tset)),
                target_codes=",".join(sorted(tset)),
                new_targets_effective=",".join(new_targets),
                held_codes=",".join(sorted(held_codes)),
                extra_non_target_codes=",".join(sorted(extra)),
                sell_requests_issued=len(sells_req),
                buy_fills=int(n_buy), sell_fills=int(n_sell),
                buy_amount=buy_amt, sell_amount=sell_amt,
                blocked_sell_count=int(len(blk_sell)),
                blocked_sell_codes=",".join(f"{r.code}:{r.reason}" for r in blk_sell.itertuples()),
                blocked_buy_count=int(len(blk_buy)),
                blocked_buy_codes=",".join(f"{r.code}:{r.reason}" for r in blk_buy.itertuples()),
                fees=float(nav.at[d, "fees"])))
            prev_book = eff if eff else prev_book
        df = pd.DataFrame(rows)
        df.to_csv(C.OUT / f"stress_daily_{tag}.csv", index=False)

        # window stats on the NAV path including the base day
        path = pd.concat([pd.Series({"_base": base_nav}),
                          nav["nav"].astype(float).loc[sub_dates]])
        path = path[["_base"] + sub_dates]
        w_ret = float(path.iloc[-1] / base_nav - 1.0)
        w_dd = float((path / path.cummax() - 1.0).min())
        worst_i = int(np.argmin(df["daily_ret"].to_numpy()))
        blocked = df[df["blocked_sell_count"] > 0]
        blocked_list = sorted({c for s in blocked["blocked_sell_codes"] for c in s.split(",") if c})
        summary_rows.append(dict(
            window=tag, start=ws, end=we, n_days=len(df),
            nav_start=base_nav, nav_end=float(path.iloc[-1]),
            window_return=w_ret, window_maxdd=w_dd,
            worst_day=df.at[worst_i, "date"], worst_day_ret=float(df.at[worst_i, "daily_ret"]),
            avg_exposure=float(df["exposure"].mean()),
            max_positions=int(df["n_positions"].max()),
            over15_days=int((df["n_positions"] > 15).sum()),
            n_rebalance_books=int(df["signal_date"].nunique()),
            n_buy_fills=int(df["buy_fills"].sum()), n_sell_fills=int(df["sell_fills"].sum()),
            n_blocked_sell_events=int(df["blocked_sell_count"].sum()),
            n_blocked_buy_events=int(df["blocked_buy_count"].sum()),
            blocked_sell_codes=",".join(blocked_list),
            n_new_entries=len(new_entries),
            new_entries=",".join(sorted(new_entries)),
            regimes=",".join(sorted({str(r) for r in df["regime"].dropna()})),
        ))
        print(f"[{tag}] {ws}..{we}  ret={w_ret:+.4f}  maxDD={w_dd:.4f}  "
              f"worst={df.at[worst_i, 'date']} ({df.at[worst_i, 'daily_ret']:+.4f})  "
              f"blocked sells={int(df['blocked_sell_count'].sum())}  "
              f"new entries={len(new_entries)}  max pos={df['n_positions'].max()}")

    summ = pd.DataFrame(summary_rows)
    summ.to_csv(C.OUT / "stress_summary.csv", index=False)
    (C.OUT / "stress_summary.json").write_text(
        json.dumps({"windows": summary_rows}, indent=1, ensure_ascii=False, default=str),
        encoding="utf-8")
    print("\n" + summ.to_string(index=False))
    print("\nwrote stress_daily_<window>.csv x4 + stress_summary.csv/.json")


if __name__ == "__main__":
    main()
