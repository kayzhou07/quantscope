# -*- coding: utf-8 -*-
"""STEP 3d/3e: effective diversification and industry-exposure gap.

3d  per-day concentration (HHI, effective N = 1/sum(w^2)) and the average pairwise
    correlation of the *held* stocks using ONLY trailing 252-day daily returns that
    were available at that date (qfq total-return panel; pairwise, min 60 overlapping
    observations).  Holdings whose trailing window contains a long suspension gap
    (>=20 consecutive missing bars) are flagged as low-vol/correlation artifacts and
    reported separately.
3e  industry exposure: the local dataset has NO historical industry classification,
    and using today's labels would be look-ahead fabrication.  The output therefore
    carries a single per-date industry class = UNKNOWN and the gap is documented.

Outputs: diversification_daily.csv, diversification_yearly.csv,
         suspension_flags.csv, industry_exposure.csv, step3de_summary.json
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

LOOKBACK = 252
MIN_OBS = 60
SUSP_GAP = 20          # consecutive missing trading bars => long suspension
SUSP_MISSING_FRAC = 0.25


def max_missing_run(mask: np.ndarray) -> int:
    """Longest run of True values."""
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def main():
    led = C.load_ledgers()
    panel = C.load_panel_cache()
    qc = panel["fields"]["qfq_close"]
    rets = qc.pct_change(fill_method=None)
    nav = led["nav"]
    dates = list(nav.index)
    pos = led["positions"]
    pos_by_date = {d: g for d, g in pos.groupby("date", sort=False)}

    # ---- 3d HHI + pairwise correlation ---------------------------------
    rows = []
    per_code = {}
    for t in dates:
        g = pos_by_date.get(t)
        if g is None or len(g) == 0:
            rows.append(dict(date=t, n_positions=0, hhi=np.nan, eff_n=np.nan,
                             avg_pair_corr=np.nan, n_pairs=0,
                             avg_pair_corr_ex_susp=np.nan, n_pairs_ex_susp=0,
                             n_susp_flagged=0, susp_codes=""))
            continue
        w = g["weight"].astype(float).to_numpy()
        # concentration of the *invested* book: weights normalised to sum 1 so that
        # HHI in [1/N, 1] and effective N = 1/HHI <= number of holdings
        w_sum = float(w.sum())
        w_norm = w / w_sum if w_sum > 0 else w
        hhi_raw = float(np.sum(w ** 2))          # HHI of NAV weights (sum = exposure)
        hhi = float(np.sum(w_norm ** 2))         # HHI of the invested book
        eff_n = float(1.0 / hhi) if hhi > 0 else np.nan
        codes = list(g["code"])
        # trailing window of returns, using only rows <= t (available at t close)
        win = rets.loc[:t].tail(LOOKBACK)
        win = win[[c for c in codes if c in win.columns]]
        # suspension flags per held code
        idx_dates = list(win.index)
        calendar_n = len(idx_dates)
        flags = {}
        for c in codes:
            if c not in win.columns or calendar_n == 0:
                flags[c] = dict(gap=calendar_n, miss=calendar_n, vol=np.nan)
                continue
            miss = win[c].isna().to_numpy()
            gap = max_missing_run(miss)
            miss_n = int(miss.sum())
            vol = float(win[c].dropna().std(ddof=1) * np.sqrt(252.0))
            flags[c] = dict(gap=gap, miss=miss_n, vol=vol,
                            suspended=(gap >= SUSP_GAP or
                                       (calendar_n > 0 and miss_n / calendar_n >= SUSP_MISSING_FRAC)))
        susp_codes = [c for c in codes if flags[c]["suspended"]]
        # pairwise correlation, all pairs
        def mean_pair_corr(frame_codes):
            sub = win[frame_codes].dropna(how="all", axis=1)
            if sub.shape[1] < 2:
                return np.nan, 0
            corr = sub.corr(min_periods=MIN_OBS)
            vals = []
            cc = list(corr.columns)
            for i in range(len(cc)):
                for j in range(i + 1, len(cc)):
                    v = corr.iloc[i, j]
                    if np.isfinite(v):
                        vals.append(float(v))
            return (float(np.mean(vals)) if vals else np.nan), len(vals)

        avg_c, n_p = mean_pair_corr(codes)
        keep = [c for c in codes if c not in susp_codes]
        avg_x, n_px = mean_pair_corr(keep) if len(keep) >= 2 else (np.nan, 0)
        rows.append(dict(date=t, n_positions=len(codes), hhi=hhi, hhi_raw=hhi_raw,
                         eff_n=eff_n,
                         avg_pair_corr=avg_c, n_pairs=n_p,
                         avg_pair_corr_ex_susp=avg_x, n_pairs_ex_susp=n_px,
                         n_susp_flagged=len(susp_codes),
                         susp_codes=",".join(susp_codes)))
        for c in codes:
            d = per_code.setdefault(c, dict(days_held=0, days_flagged=0,
                                            max_gap=0, vols=[]))
            d["days_held"] += 1
            if flags[c]["suspended"]:
                d["days_flagged"] += 1
            d["max_gap"] = max(d["max_gap"], int(flags[c]["gap"]))
            if np.isfinite(flags[c]["vol"]):
                d["vols"].append(flags[c]["vol"])

    div = pd.DataFrame(rows).set_index("date")
    div.to_csv(C.OUT / "diversification_daily.csv")
    # yearly
    div["year"] = [d[:4] for d in div.index]
    yearly = div.groupby("year").agg(
        days=("n_positions", "size"),
        avg_positions=("n_positions", "mean"),
        avg_hhi=("hhi", "mean"),
        avg_hhi_raw=("hhi_raw", "mean"),
        avg_eff_n=("eff_n", "mean"),
        avg_pair_corr=("avg_pair_corr", "mean"),
        avg_pair_corr_ex_susp=("avg_pair_corr_ex_susp", "mean"),
        days_with_susp=("n_susp_flagged", lambda s: int((s > 0).sum())),
    ).reset_index()
    yearly.to_csv(C.OUT / "diversification_yearly.csv", index=False)

    # per-code suspension / low-vol flag table
    pc_rows = []
    for c, d in per_code.items():
        vols = d["vols"]
        pc_rows.append(dict(code=c, days_held=d["days_held"],
                            days_flagged_susp=d["days_flagged"],
                            max_missing_gap_days=d["max_gap"],
                            avg_trailing_vol=float(np.mean(vols)) if vols else np.nan,
                            min_trailing_vol=float(np.min(vols)) if vols else np.nan))
    pc = pd.DataFrame(pc_rows).sort_values(["days_flagged_susp", "days_held"],
                                           ascending=False)
    pc.to_csv(C.OUT / "suspension_flags.csv", index=False)

    # ---- 3e industry exposure (documented gap) -------------------------
    ind = pd.DataFrame({
        "date": nav.index,
        "industry": "UNKNOWN",
        "n_positions": div["n_positions"].to_numpy(),
        "market_value": nav["market_value"].astype(float).to_numpy(),
        "weight_of_nav": nav["exposure"].astype(float).to_numpy(),
        "note": "no historical industry classification in local data; today's labels "
                "not used (would be look-ahead fabrication)",
    })
    ind.to_csv(C.OUT / "industry_exposure.csv", index=False)

    # ---- summary -------------------------------------------------------
    in_mkt = div[div["n_positions"] > 0]
    summary = {
        "n_days": int(len(div)),
        "n_days_with_positions": int(len(in_mkt)),
        "hhi_mean": float(in_mkt["hhi"].mean()),
        "hhi_median": float(in_mkt["hhi"].median()),
        "eff_n_mean": float(in_mkt["eff_n"].mean()),
        "eff_n_median": float(in_mkt["eff_n"].median()),
        "eff_n_p10": float(in_mkt["eff_n"].quantile(0.10)),
        "eff_n_p90": float(in_mkt["eff_n"].quantile(0.90)),
        "avg_pair_corr_mean": float(in_mkt["avg_pair_corr"].mean()),
        "avg_pair_corr_median": float(in_mkt["avg_pair_corr"].median()),
        "avg_pair_corr_p10": float(in_mkt["avg_pair_corr"].quantile(0.10)),
        "avg_pair_corr_p90": float(in_mkt["avg_pair_corr"].quantile(0.90)),
        "avg_pair_corr_ex_susp_mean": float(in_mkt["avg_pair_corr_ex_susp"].mean()),
        "n_days_with_susp_holding": int((in_mkt["n_susp_flagged"] > 0).sum()),
        "susp_flagged_codes": pc[pc["days_flagged_susp"] > 0]["code"].tolist(),
        "min_eff_n": float(in_mkt["eff_n"].min()),
        "max_eff_n": float(in_mkt["eff_n"].max()),
        "max_hhi": float(in_mkt["hhi"].max()),
        "industry_csv_rows": int(len(ind)),
        "industry_unique_values": sorted(ind["industry"].unique().tolist()),
    }
    (C.OUT / "step3de_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    print("=== 3d diversification ===")
    print(f"days with positions {len(in_mkt)}/{len(div)}")
    print(f"HHI mean {summary['hhi_mean']:.4f} median {summary['hhi_median']:.4f} "
          f"-> effective N mean {summary['eff_n_mean']:.2f} "
          f"(median {summary['eff_n_median']:.2f}, p10 {summary['eff_n_p10']:.2f}, "
          f"p90 {summary['eff_n_p90']:.2f})")
    print(f"avg pairwise corr mean {summary['avg_pair_corr_mean']:.4f} "
          f"median {summary['avg_pair_corr_median']:.4f} "
          f"(ex-susp mean {summary['avg_pair_corr_ex_susp_mean']:.4f})")
    print(f"days with a flagged (long-suspension) holding: "
          f"{summary['n_days_with_susp_holding']}")
    print("\nyearly diversification:")
    print(yearly.to_string(index=False))
    print("\ntop suspension-flagged codes:")
    print(pc.head(12).to_string(index=False))
    print("\n=== 3e industry exposure: single class UNKNOWN, rows =", len(ind), "===")
    print("wrote diversification_daily.csv / diversification_yearly.csv / "
          "suspension_flags.csv / industry_exposure.csv / step3de_summary.json")


if __name__ == "__main__":
    main()
