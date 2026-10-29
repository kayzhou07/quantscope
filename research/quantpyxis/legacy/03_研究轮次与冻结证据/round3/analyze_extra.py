# -*- coding: utf-8 -*-
"""Extra diagnostics for the round-3 report.

1. Benchmark total returns over the same window.
2. S0 vs S1 capital utilisation: target exposure vs realised exposure after fills.
3. Per-rebalance execution: how many chosen names actually got filled at the next open,
   and which were dropped/blocked.  For S0 this quantifies the truncated-then-filtered bug;
   for S1 it shows whether the backfill survives the next open (gap-up affordability).
4. Affordability test at the NEXT OPEN price: for each chosen name we reconstruct the
   execution-day open from the trade fill price (fill = open*(1+slippage)); a name whose
   one-lot cost at that open exceeds the per-name budget would have been unaffordable even
   though the signal-day raw close passed the test.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "out"
CAP = 100_000.0
SLIP = 0.001  # default 10bp one-side


def load_nav(tag):
    return pd.read_csv(OUT / f"{tag}_nav.csv", index_col=0)


def main():
    res = {}
    navs = {t: load_nav(t) for t in ("S0", "S1", "S1b", "S0_20bp", "S1_stress")}

    # 1. benchmarks ---------------------------------------------------------
    bench = {}
    n = navs["S0"]
    for col in ("bench_sh000300", "bench_sz399006"):
        if col in n:
            bench[col] = float(n[col].iloc[-1] / CAP - 1.0)
    # calendar-year benchmark returns
    by_year = {}
    for col in ("bench_sh000300", "bench_sz399006"):
        s = n[col].astype(float)
        prev = s.shift(1)
        prev.iloc[0] = CAP
        df = pd.DataFrame({"nav": s, "prev": prev})
        df["year"] = [d[:4] for d in df.index]
        by_year[col] = {y: float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1.0)
                        for y, g in df.groupby("year")}
    res["benchmarks"] = {"total_return": bench, "yearly": by_year}

    # 2/3/4. per-rebalance execution ---------------------------------------
    rb_all = pd.read_csv(OUT / "REBALANCES.csv", dtype={"date": str})
    sel_all = pd.read_csv(OUT / "REBALANCE_SELECTIONS.csv",
                          dtype={"date": str, "code": str})
    trades_all = {t: pd.read_csv(OUT / f"TRADES_{t}.csv",
                                  dtype={"date": str, "code": str})
                  for t in ("S0", "S1")}
    pos_all = {t: pd.read_csv(OUT / f"POSITIONS_{t}.csv",
                              dtype={"date": str, "code": str})
               for t in ("S0", "S1")}
    dates = list(navs["S0"].index)
    date_pos = {d: i for i, d in enumerate(dates)}

    per_tag = {}
    for tag in ("S0", "S1"):
        rb = rb_all[(rb_all["tag"] == tag) & (rb_all["note"] == "target_set")]
        sel = sel_all[sel_all["tag"] == tag]
        tr = trades_all[tag]
        pos_t = pos_all[tag]
        rows = []
        for _, r in rb.iterrows():
            d = r["date"]
            i = date_pos.get(d)
            if i is None or i + 1 >= len(dates):
                continue
            exec_day = dates[i + 1]
            chosen = sel[(sel["date"] == d) & (sel["decision"] == "chosen")]["code"].tolist()
            held_before = set(pos_t.loc[pos_t["date"] == d, "code"])
            budget = float(r["budget_per_name"])
            execution = tr[(tr["date"] == exec_day) & (tr["reason"] == "rebalance")]
            buys = execution[execution["side"] == "buy"]
            filled = set(buys["code"].astype(str)) | held_before
            n_filled = len([c for c in chosen if c in filled])
            # exposure realised: max exposure over the next 3 sessions
            realized = float(navs[tag]["exposure"].iloc[i + 1: i + 4].max())
            target = float(r["exposure"])
            # next-open affordability of the names actually bought (fill price = open*1.001)
            open_px = buys["price"] / (1.0 + SLIP)
            one_lot = open_px * 100.0
            est_cost = one_lot * 1.001 + np.maximum(5.0, one_lot * 1.001 * 0.00025) + one_lot * 1.001 * 0.00001
            affordable_at_open = int((est_cost <= budget + 1e-9).sum())
            rows.append(dict(date=d, exec_day=exec_day, state=r["state"], target=target,
                             realized_exposure=realized, utilization=realized / target if target else np.nan,
                             n_chosen=len(chosen), n_filled=n_filled,
                             n_not_filled=len(chosen) - n_filled,
                             n_bought_affordable_at_open=affordable_at_open,
                             n_bought_unaffordable_at_open=int(len(buys) - affordable_at_open)))
        df = pd.DataFrame(rows)
        filt = df[df["n_chosen"] > 0]
        agg = dict(
            n_rebalances=int(len(df)),
            mean_chosen=float(filt["n_chosen"].mean()) if len(filt) else 0,
            mean_filled=float(filt["n_filled"].mean()) if len(filt) else 0,
            fill_rate_mean=float((filt["n_filled"] / filt["n_chosen"]).mean()) if len(filt) else None,
            total_chosen=int(filt["n_chosen"].sum()) if len(filt) else 0,
            total_filled=int(filt["n_filled"].sum()) if len(filt) else 0,
            mean_utilization=float(df["utilization"].mean()) if len(df) else None,
            mean_target=float(df["target"].mean()),
            mean_realized=float(df["realized_exposure"].mean()),
            n_bought=int(filt["n_bought_affordable_at_open"].sum() + filt["n_bought_unaffordable_at_open"].sum()),
            n_bought_unaffordable_at_open=int(filt["n_bought_unaffordable_at_open"].sum()),
        )
        # split by state
        for st in ("B", "N"):
            sub = filt[filt["state"] == st]
            agg[f"fill_rate_{st}"] = float((sub["n_filled"] / sub["n_chosen"]).mean()) if len(sub) else None
            agg[f"mean_chosen_{st}"] = float(sub["n_chosen"].mean()) if len(sub) else None
            agg[f"mean_utilization_{st}"] = float(sub["utilization"].mean()) if len(sub) else None
        per_tag[tag] = dict(agg=agg, rows=df)
        df.to_csv(OUT / f"EXECUTION_{tag}.csv", index=False)
    res["execution"] = {t: v["agg"] for t, v in per_tag.items()}

    # 5. S0 dropped-unaffordable detail ------------------------------------
    sel_s0 = sel_all[sel_all["tag"] == "S0"]
    dropped = sel_s0[sel_s0["decision"] == "dropped_unaffordable"]
    res["s0_dropped"] = {
        "n_dropped": int(len(dropped)),
        "mean_raw_price": float(dropped["raw_price"].mean()) if len(dropped) else None,
        "median_raw_price": float(dropped["raw_price"].median()) if len(dropped) else None,
        "min_price": float(dropped["raw_price"].min()) if len(dropped) else None,
        "max_price": float(dropped["raw_price"].max()) if len(dropped) else None,
        "by_state": dropped.groupby("state").size().to_dict() if len(dropped) else {},
        "by_reason": dropped.groupby("reason").size().to_dict() if len(dropped) else {},
        "total_one_lot_cost": float(dropped["one_lot_cost"].sum()) if len(dropped) else None,
    }
    # dropped names that WERE affordable by up to what margin? none by construction.
    # what did S0 actually hold on average?
    for tag in ("S0", "S1"):
        d = pd.read_csv(OUT / f"DAILY_AUDIT_{tag}.csv", index_col=0)
        res.setdefault("positions", {})[tag] = dict(
            mean_positions=float(d["n_positions"].mean()),
            pct_days_0=int((d["n_positions"] == 0).sum()),
            pct_days_1_3=int(((d["n_positions"] >= 1) & (d["n_positions"] <= 3)).sum()),
            pct_days_10=int((d["n_positions"] >= 10).sum()),
            max_positions=int(d["n_positions"].max()),
        )
    (OUT / "extra_diagnostics.json").write_text(json.dumps(res, indent=1, default=str),
                                                encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
