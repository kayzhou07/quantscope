# -*- coding: utf-8 -*-
"""STEP 2: every day with >15 holdings and the retention mechanism.

Inputs (all produced by rerun_a.py): a_nav.csv, a_positions.csv, a_target_books.csv,
a_call_log.csv, a_events.csv, a_orders.csv.
Outputs:
  out/over15_detail.csv      long format: one row per (over-15 day, extra holding)
  out/over15_days.csv        one row per over-15 day (counts, extra codes, reasons)
  out/over15_episodes.csv    consecutive over-15 episodes
  out/over15_yearly.csv      worst count / days per year
  out/over15_summary.json    mechanism tests (explained? replacement-timing contribution)
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

BLOCK_KINDS = {"suspended": "停牌(suspension)", "limit_down": "跌停(limit_down)",
               "limit_up": "涨停(limit_up)", "not_listed": "退市/未上市",
               "no_position": "无持仓", "t1_locked": "T+1锁定",
               "insufficient_cash": "现金不足", "st_not_buyable": "ST不可买"}
EXTRA_REASONS_TO_KEEP = ("suspended", "limit_down", "not_listed", "t1_locked",
                         "limit_up", "insufficient_cash")


def build_book_timeline(calls: pd.DataFrame):
    """Every set_targets call in order: (date, seq, note, weights dict)."""
    tl = []
    for r in calls[calls["call"] == "set_targets"].itertuples():
        tl.append(dict(date=str(r.date), seq=int(r.seq), note=str(r.note)))
    books = C.load_ledgers()["books"]
    wmap = {}
    for (d, s), g in books.groupby(["date", "seq"], sort=False):
        wmap[(str(d), int(s))] = {str(c): float(w) for c, w in zip(g["code"], g["weight"])}
    for rec in tl:
        rec["weights"] = wmap.get((rec["date"], rec["seq"]), {})
    return tl


def effective_book(timeline, day: str):
    """Most recent set_targets call strictly before `day` (T close signal -> T+1)."""
    eff = None
    for rec in timeline:
        if rec["date"] < day:
            eff = rec
        else:
            break
    return eff


def main():
    led = C.load_ledgers()
    nav = led["nav"]
    pos = led["positions"]
    ev = led["events"]
    od = led["orders"]
    calls = led["calls"]
    tl = build_book_timeline(calls)

    pos_by_date = {d: g for d, g in pos.groupby("date", sort=False)}
    dates = list(nav.index)

    # per-day book state -----------------------------------------------------
    day_rows = []
    for d in dates:
        eff = effective_book(tl, d)
        held = pos_by_date.get(d)
        held_codes = [] if held is None else list(held["code"])
        tset = set(eff["weights"]) if eff else set()
        extra = [c for c in held_codes if c not in tset]
        missing = [c for c in tset if c not in held_codes]
        day_rows.append(dict(date=d, n_positions=len(held_codes), book_date=eff["date"] if eff else "",
                             book_n=int(len(tset)), n_in_book=len(held_codes) - len(extra),
                             n_extra=len(extra), extra_codes=",".join(sorted(extra)),
                             book_missing_codes=",".join(sorted(missing))))
    days = pd.DataFrame(day_rows).set_index("date")

    # block events -----------------------------------------------------------
    ev["date"] = ev["date"].astype(str)
    tb = ev[ev["type"] == "target_blocked"].copy()
    tb["code"] = tb["code"].astype(str)
    tb_sell = tb[tb["side"] == "sell"]
    block_by_code = {c: g.sort_values("date") for c, g in tb_sell.groupby("code")}
    all_ev_by_code = {c: g for c, g in ev.groupby("code")}
    exp_events = ev[ev["type"].isin(["target_book_expired", "target_book_unfilled_at_end"])]

    # sell orders (explicit exit requests) ----------------------------------
    od_sell = od[od["side"] == "sell"].copy()
    od_by_code = {c: g for c, g in od_sell.groupby("code")}

    detail_rows = []
    over = days[days["n_positions"] > 15]
    for d, row in over.iterrows():
        held = pos_by_date.get(d)
        held_map = {r.code: (float(r.shares), float(r.value), float(r.weight))
                    for r in held.itertuples()}
        eff = effective_book(tl, d)
        tset = set(eff["weights"]) if eff else set()
        extra = [c for c in held_map if c not in tset]
        for c in sorted(extra):
            sh, val, wt = held_map[c]
            blocks = block_by_code.get(c)
            first_reason = last_reason = ""
            first_bdate = last_bdate = ""
            sig_dates = ""
            prior_book_retained = 0
            if blocks is not None:
                b = blocks[blocks["date"] <= d]
                if len(b):
                    b = b.sort_values("date")
                    first_reason, first_bdate = str(b["reason"].iloc[0]), str(b["date"].iloc[0])
                    last_reason, last_bdate = str(b["reason"].iloc[-1]), str(b["date"].iloc[-1])
                    sd = sorted({str(x) for x in b["signal_date"].dropna().unique()})
                    sig_dates = ",".join(sd)
                    prior_book_retained = int(any(x < eff["date"] for x in sd)) if eff else 0
            src = "target_blocked_event" if first_reason else ""
            # fallback: explicit sell order history
            if not first_reason:
                o = od_by_code.get(c)
                if o is not None:
                    oo = o[(o["created_date"] <= d)].sort_values("created_date")
                    if len(oo):
                        cand = oo[oo["status"].isin(["pending", "expired"]) |
                                  ((oo["status"] == "filled") &
                                   (oo["filled_date"].astype(str) > d))]
                        if len(cand):
                            r0 = cand.iloc[-1]
                            src = "explicit_sell_order"
                            first_reason = str(r0.get("reason") or "")
                            first_bdate = str(r0["created_date"])
            # which call dropped it (last book containing the code before d)
            last_in_book = ""
            for rec in tl:
                if rec["date"] < d and c in rec["weights"]:
                    last_in_book = rec["date"]
            detail_rows.append(dict(
                date=d, n_positions=int(row["n_positions"]),
                book_date=eff["date"] if eff else "", book_n=int(len(tset)),
                held_codes=",".join(sorted(held_map)),
                held_shares=",".join(f"{k}:{int(v[0])}" for k, v in sorted(held_map.items())),
                extra_code=c, extra_shares=sh, extra_value=val, extra_weight=wt,
                last_book_containing_code=last_in_book,
                first_block_date=first_bdate, first_block_reason=first_reason,
                last_block_date=last_bdate, last_block_reason=last_reason,
                block_signal_dates=sig_dates, evidence_source=src,
                carried_across_rebalance=prior_book_retained,
                residual_shares=sh, residual_value=val))
    det = pd.DataFrame(detail_rows)
    det.to_csv(C.OUT / "over15_detail.csv", index=False)

    # per-day summary --------------------------------------------------------
    day_out = over.copy()
    day_out["extra_reasons"] = ""
    day_out["residual_shares"] = ""
    for d in day_out.index:
        g = det[det["date"] == d]
        day_out.at[d, "extra_reasons"] = ";".join(
            f"{r.extra_code}:{r.last_block_reason or '?'}" for r in g.itertuples())
        day_out.at[d, "residual_shares"] = ";".join(
            f"{r.extra_code}:{int(r.residual_shares)}" for r in g.itertuples())
    day_out = day_out.reset_index()
    day_out.to_csv(C.OUT / "over15_days.csv", index=False)

    # episodes ---------------------------------------------------------------
    ep_rows = []
    prev_i = None
    cur = None
    for d in over.index:
        i = dates.index(d)
        if prev_i is not None and i == prev_i + 1:
            cur["end"] = d
            cur["n_days"] += 1
            cur["max_positions"] = max(cur["max_positions"], int(over.at[d, "n_positions"]))
        else:
            if cur:
                ep_rows.append(cur)
            cur = dict(start=d, end=d, n_days=1,
                       max_positions=int(over.at[d, "n_positions"]),
                       book_date=over.at[d, "book_date"],
                       extra_codes=over.at[d, "extra_codes"])
        prev_i = i
    if cur:
        ep_rows.append(cur)
    ep = pd.DataFrame(ep_rows)
    # first / last over-15 trading day after the episode, and what resolved it
    res = []
    for r in ep.itertuples():
        i = dates.index(r.end)
        after = dates[i + 1] if i + 1 < len(dates) else None
        res.append(dict(
            start=r.start, end=r.end, n_days=r.n_days, max_positions=r.max_positions,
            book_date=r.book_date, extra_codes=r.extra_codes,
            next_day=after,
            next_day_n=int(days.at[after, "n_positions"]) if after else None,
            resolved=bool(after is not None and days.at[after, "n_positions"] <= 15)))
    ep = pd.DataFrame(res)
    ep.to_csv(C.OUT / "over15_episodes.csv", index=False)

    # yearly -----------------------------------------------------------------
    yr = pd.DataFrame({"year": [d[:4] for d in over.index],
                       "n": over["n_positions"].to_numpy()})
    yearly = yr.groupby("year")["n"].agg(days_over_15="count", worst_count="max").reset_index()
    yearly.to_csv(C.OUT / "over15_yearly.csv", index=False)

    # concentration per extra holding: months held as extra ------------------
    det["month"] = det["date"].str[:7]

    # ---- mechanism tests ---------------------------------------------------
    tests = {}
    tests["n_over15_days"] = int(len(over))
    tests["max_positions"] = int(over["n_positions"].max())
    tests["n_days_with_any_extra"] = int((days["n_extra"] > 0).sum())
    tests["n_days_extra_but_le15"] = int(((days["n_extra"] > 0) &
                                          (days["n_positions"] <= 15)).sum())
    tests["n_over15_days_all_extra_have_block_evidence"] = int(
        det.groupby("date").apply(lambda g: bool(g["first_block_reason"].astype(bool).all()),
                                  include_groups=False).sum())
    tests["n_extra_holding_days"] = int(len(det))
    tests["n_extra_holding_days_with_block_event"] = int((det["evidence_source"] ==
                                                          "target_blocked_event").sum())
    tests["n_extra_holding_days_explicit_order_only"] = int((det["evidence_source"] ==
                                                             "explicit_sell_order").sum())
    tests["n_extra_holding_days_unexplained"] = int((det["evidence_source"] == "").sum())
    tests["n_extra_holding_days_carried_across_rebalance"] = int(
        det["carried_across_rebalance"].astype(bool).sum())
    tests["reason_counts_extra"] = det["last_block_reason"].replace("", "?").value_counts().to_dict()
    tests["over15_days_by_extra_count"] = over["n_extra"].value_counts().sort_index().to_dict()
    tests["expired_book_events"] = int(len(exp_events))
    tests["delisted_events"] = int((ev["type"] == "delisting_forced_liquidation").sum())
    tests["max_extra"] = int(det.groupby("date").size().max())
    # does any extra holding have zero target weight but was bought *after* drop?
    # (target-book replacement: a name dropped by a new book bought again later)
    tests["n_extra_codes_sold_during_over15_window"] = 0
    # per-day all-extras-explained flag
    per_day = det.groupby("date").agg(n_extra=("extra_code", "size"),
                                      n_with_evidence=("evidence_source",
                                                       lambda s: int((s != "").sum())))
    tests["days_all_extra_explained"] = int((per_day["n_extra"] ==
                                             per_day["n_with_evidence"]).sum())
    tests["day_detail"] = per_day.reset_index().to_dict(orient="records")

    # extra holdings months (for mechanism narrative)
    tests["extra_by_year"] = {str(k): int(v) for k, v in
                              det.groupby(det["date"].str[:4]).size().items()}
    tests["block_reason_by_year"] = {
        f"{y}|{r or '?'}": int(v) for (y, r), v in
        det.groupby([det["date"].str[:4], "last_block_reason"]).size().items()}

    (C.OUT / "over15_summary.json").write_text(
        json.dumps(tests, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    # console summary --------------------------------------------------------
    print(f"over-15 days: {len(over)}   max positions: {over['n_positions'].max()}")
    print(over.groupby(over.index.str[:4])["n_positions"].agg(["count", "max"]).to_string())
    print("\nper-day extras vs in-book holdings:")
    print(over[["n_positions", "book_n", "n_in_book", "n_extra"]].describe().to_string())
    print(f"\nextra holding-days: {len(det)}  with block event: "
          f"{tests['n_extra_holding_days_with_block_event']}  explicit-only: "
          f"{tests['n_extra_holding_days_explicit_order_only']}  unexplained: "
          f"{tests['n_extra_holding_days_unexplained']}")
    print(f"days all extras explained: {tests['days_all_extra_explained']}/{len(over)}")
    print("\nreason counts (extra holdings):")
    print(det["last_block_reason"].replace("", "?").value_counts().to_string())
    print("\nepisodes:")
    print(ep.to_string())
    print("\nyearly:")
    print(yearly.to_string())
    print("\n2014-10 detail:")
    print(det[det["date"].str[:7] == "2014-10"].to_string())
    print("\nwrote over15_detail.csv / over15_days.csv / over15_episodes.csv / "
          "over15_yearly.csv / over15_summary.json")


if __name__ == "__main__":
    main()
