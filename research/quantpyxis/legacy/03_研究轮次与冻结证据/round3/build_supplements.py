# -*- coding: utf-8 -*-
"""Supplementary deliverables flagged as PARTIAL by the independent verifier.

1. S0 with doubled commission (5bp) + doubled slippage (20bp), saved as `supp_S0_stress_*`
   (supplementary: outside the audited primary snapshot).
2. NEXT_OPEN_AFFORDABILITY.csv — per chosen name, the signal-close one-lot test vs the
   actual next-open one-lot cost (reconstructed from the raw panel; for filled names the
   trade price cross-checks open*(1+slippage)).
3. DISPLACEMENT_S0_S1.csv — per rebalance date, the names S1 selects that S0 does not and
   vice versa (the holdings the "fix" displaces).
4. CAPITAL_UTILISATION_SUMMARY.csv — per-tag summary of target vs realised exposure
   (already in EXECUTION_*.csv; this adds the aggregate table the handoff §3 asks for).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s0s1
import run_round3 as R

OUT = HERE / "out"
REPO = s0s1.REPO
CAP = 100_000.0
SLIP = 0.001


def load_panel_opens(years):
    """{(code, 'YYYY-MM-DD'): raw open} for the raw bar files of `years`."""
    cols = ["code", "date", "open"]
    frames = []
    for y in years:
        p = REPO / "data" / "panel" / f"bars_{y}.csv.gz"
        if not p.exists():
            continue
        df = pd.read_csv(p, usecols=cols, dtype={"code": str, "date": str})
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df = df[pd.to_numeric(df["open"], errors="coerce").notna()]
    return {(c, d): float(o) for c, d, o in zip(df["code"], df["date"], df["open"])}


def next_trading_day(dates, d):
    i = dates.index(d)
    return dates[i + 1] if i + 1 < len(dates) else None


def main():
    letters, states = R.load_regime()

    # ---- 1. supplementary S0 stress run ----------------------------------
    tag = "supp_S0_stress"
    res, strat, elapsed = R.run_variant(tag, "S0", letters,
                                        slippage_bps=20.0, commission_bps=5.0,
                                        extra_name="S0_stress")
    df = R.build_daily(res, strat, states)
    df.to_csv(OUT / f"{tag}_daily.csv")
    m = R.metrics_block(tag, res, df, R.positions_table(strat), strat, elapsed)
    (OUT / f"{tag}_metrics.json").write_text(json.dumps(m, indent=1, default=str),
                                             encoding="utf-8")
    print(f"[supp] {tag} ret={m['total_return']:+.4f} dd={m['max_drawdown']:.4f} "
          f"trades={m['trade_count']} fees={m['total_fees']:.1f}")

    # ---- 2/3. selection-based supplements --------------------------------
    sel = pd.read_csv(OUT / "REBALANCE_SELECTIONS.csv", dtype={"date": str, "code": str})
    rb = pd.read_csv(OUT / "REBALANCES.csv", dtype={"date": str})
    nav = pd.read_csv(OUT / "S0_nav.csv", index_col=0)
    dates = [str(d) for d in nav.index]
    opens = load_panel_opens(range(2014, 2024))

    rows = []
    for _, r in sel[sel["decision"] == "chosen"].iterrows():
        d = r["date"]
        ed = next_trading_day(dates, d)
        if ed is None:
            continue
        code = r["code"]
        op = opens.get((code, ed), np.nan)
        budget = np.nan
        m = rb[(rb["tag"] == r["tag"]) & (rb["date"] == d)]
        if len(m):
            budget = float(m["budget_per_name"].iloc[0])
        one_lot = 100.0 * op if np.isfinite(op) else np.nan
        est = (one_lot * 1.001 + max(5.0, one_lot * 1.001 * 0.00025) + one_lot * 1.001 * 0.00001
               if np.isfinite(one_lot) else np.nan)
        rows.append(dict(tag=r["tag"], date=d, exec_day=ed, code=code,
                         cand_rank=r["cand_rank"], signal_raw_price=r["raw_price"],
                         next_open=op, one_lot_value_at_open=one_lot,
                         est_one_lot_cost_at_open=est, budget_per_name=budget,
                         budget_x102=budget * 1.02 if np.isfinite(budget) else np.nan,
                         affordable_at_open=(est <= budget) if np.isfinite(est) and np.isfinite(budget) else None,
                         affordable_by_S0_test=(one_lot <= budget * 1.02)
                         if np.isfinite(one_lot) and np.isfinite(budget) else None))
    aff = pd.DataFrame(rows)
    aff.to_csv(OUT / "NEXT_OPEN_AFFORDABILITY.csv", index=False)
    for tag_ in ("S0", "S1"):
        a = aff[aff["tag"] == tag_]
        n = int(len(a))
        print(f"[supp] {tag_} chosen={n} next-open affordable={int(a['affordable_at_open'].sum())} "
              f"({a['affordable_at_open'].mean():.3%}) "
              f"signal-test_pass_then_open_fail={int(((a['affordable_by_S0_test'] == True) & (a['affordable_at_open'] == False)).sum())}")

    # displacement
    disp = []
    for d, g in sel.groupby("date"):
        s0 = set(g[(g["tag"] == "S0") & (g["decision"] == "chosen")]["code"])
        s1 = set(g[(g["tag"] == "S1") & (g["decision"] == "chosen")]["code"])
        s1b = set(g[(g["tag"] == "S1b") & (g["decision"] == "chosen")]["code"])
        disp.append(dict(date=d, n_S0=len(s0), n_S1=len(s1), n_common_S0_S1=len(s0 & s1),
                         only_S0=len(s0 - s1), only_S1=len(s1 - s0),
                         only_S0_codes=";".join(sorted(s0 - s1)),
                         only_S1_codes=";".join(sorted(s1 - s0)),
                         n_S1b=len(s1b), n_common_S0_S1b=len(s0 & s1b)))
    pd.DataFrame(disp).sort_values("date").to_csv(OUT / "DISPLACEMENT_S0_S1.csv", index=False)
    print("[supp] displacement done")

    # ---- 4. capital utilisation summary ----------------------------------
    rows = []
    for tag_ in ("S0", "S1"):
        ex = pd.read_csv(OUT / f"EXECUTION_{tag_}.csv", dtype={"date": str})
        ex = ex[ex["target"] > 0]
        rows.append(dict(tag=tag_, n_rebalances=len(ex),
                         mean_target=float(ex["target"].mean()),
                         mean_realized=float(ex["realized_exposure"].mean()),
                         mean_utilization=float(ex["utilization"].mean()),
                         median_utilization=float(ex["utilization"].median()),
                         min_utilization=float(ex["utilization"].min()),
                         n_util_below_70pct=int((ex["utilization"] < 0.70).sum())))
        for st in ("B", "N"):
            sub = ex[ex["state"] == st]
            rows.append(dict(tag=f"{tag_}_{st}", n_rebalances=len(sub),
                             mean_target=float(sub["target"].mean()),
                             mean_realized=float(sub["realized_exposure"].mean()),
                             mean_utilization=float(sub["utilization"].mean()),
                             median_utilization=float(sub["utilization"].median()),
                             min_utilization=float(sub["utilization"].min()),
                             n_util_below_70pct=int((sub["utilization"] < 0.70).sum())))
    pd.DataFrame(rows).to_csv(OUT / "CAPITAL_UTILISATION_SUMMARY.csv", index=False)
    print("[supp] capital utilisation summary done")

    # validation: for buys, trade price should equal next_open*(1+slippage)
    tr = pd.read_csv(OUT / "S0_trades.csv", dtype={"date": str, "code": str})
    buys = tr[(tr["side"] == "buy") & (tr["reason"] == "rebalance")]
    checked = 0
    bad = 0
    for _, b in buys.iterrows():
        op = opens.get((b["code"], b["date"]))
        if op is None:
            continue
        checked += 1
        if abs(float(b["price"]) - op * 1.001) > max(1e-6, 0.002 * op):
            bad += 1
    print(f"[supp] next-open cross-check: {checked} buys checked, {bad} mismatches")


if __name__ == "__main__":
    main()
