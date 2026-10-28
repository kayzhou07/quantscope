# -*- coding: utf-8 -*-
"""STEP 3a/3b/3c: account-level attribution.

3a  worst 20 account days with an exact per-stock P&L decomposition and an
    overnight/intraday/trade-execution/fee split (qfq open/close where available,
    raw open/close fallback; corporate-action residual isolated).
3b  three largest non-overlapping drawdown episodes (peak/trough/recovery, depth,
    duration, underwater days).
3c  top-5 stock contributions over the full window (exact daily contribution =
    value change + trade cash flows, i.e. mark-to-market + realized effects) and the
    "exclude the single largest winner, no reselection" NAV-path sensitivity.

Outputs: out/daily_contrib_long.csv, out/daily_attrib_summary.csv,
         out/worst20_days.csv, out/worst20_contrib_long.csv,
         out/drawdowns_top3.csv, out/top5_stock_contrib.csv,
         out/exclude_top1_sensitivity.csv, out/step3abc_summary.json
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


def main():
    led = C.load_ledgers()
    panel = C.load_panel_cache()
    nav_df = led["nav"]
    nav = nav_df["nav"].astype(float)
    ret = C.daily_returns(nav)
    print("computing exact daily attribution ...")
    att = C.compute_daily_attribution(led, panel)
    long = att["long"]
    mat = att["mat"]
    recon = att["recon"]

    # ---- write the audit trail ------------------------------------------
    long.to_csv(C.OUT / "daily_contrib_long.csv", index=False)
    daily = recon.copy()
    for k in ("overnight", "intraday", "trade_exec", "ca_residual", "fee"):
        daily[k] = mat[k].sum(axis=1).reindex(daily["date"]).to_numpy()
    daily["exposure"] = nav_df["exposure"].reindex(daily["date"]).to_numpy()
    daily["regime"] = nav_df["regime"].reindex(daily["date"]).to_numpy()
    daily["n_positions"] = nav_df["n_positions"].reindex(daily["date"]).to_numpy()
    daily["check"] = (daily["overnight"] + daily["intraday"] + daily["trade_exec"]
                      + daily["ca_residual"] - daily["fee"])
    daily["check_gap"] = daily["check"] - daily["dnav"]
    daily.to_csv(C.OUT / "daily_attrib_summary.csv", index=False)
    print(f"reconciliation: max |gap| = {recon['gap'].abs().max():.6f} CNY, "
          f"mean |gap| = {recon['gap'].abs().mean():.6f} CNY")
    print(f"split check: max |gap| = {daily['check_gap'].abs().max():.6f} CNY")

    # =====================================================================
    # 3a  worst 20 account days
    # =====================================================================
    pnl = nav.diff()
    pnl.iloc[0] = nav.iloc[0] - C.CAP
    bench_ret = {}
    for b in ("bench_sh000300", "bench_sz399006"):
        if b in nav_df.columns:
            bench_ret[b] = nav_df[b].astype(float).pct_change(fill_method=None)
    days = pd.DataFrame({"date": nav.index, "nav": nav.to_numpy(),
                         "pnl": pnl.to_numpy(), "ret": ret.reindex(nav.index).to_numpy(),
                         "exposure": nav_df["exposure"].to_numpy(),
                         "regime": nav_df["regime"].to_numpy(),
                         "n_positions": nav_df["n_positions"].to_numpy(),
                         "fees": nav_df["fees"].to_numpy()}).set_index("date")
    for b, s in bench_ret.items():
        days[b + "_ret"] = s.reindex(days.index).to_numpy()
    worst = days.dropna(subset=["ret"]).sort_values("ret").head(20).copy()

    contrib = mat["contrib_gross"]
    rows = []
    long_rows = []
    for d in worst.index:
        c_row = contrib.loc[d]
        c_row = c_row[c_row.abs() > 1e-9].sort_values()
        top_loss = c_row.head(3)
        top_gain = c_row.tail(2).iloc[::-1]
        drv = " | ".join([f"{c} {v:+.0f}" for c, v in
                          list(top_loss.items()) + list(top_gain.items())])
        rows.append(dict(
            date=d, daily_pnl=float(pnl.loc[d]), daily_return=float(ret.loc[d]),
            nav=float(nav.loc[d]), exposure=float(nav_df.at[d, "exposure"]),
            regime=nav_df.at[d, "regime"], n_positions=int(nav_df.at[d, "n_positions"]),
            overnight=float(daily.set_index("date").at[d, "overnight"]),
            intraday=float(daily.set_index("date").at[d, "intraday"]),
            trade_exec=float(daily.set_index("date").at[d, "trade_exec"]),
            ca_residual=float(daily.set_index("date").at[d, "ca_residual"]),
            fees=float(nav_df.at[d, "fees"]),
            cash_interest=0.0,
            bench_csi300_ret=float(days.at[d, "bench_sh000300_ret"]) if "bench_sh000300_ret" in days else np.nan,
            drivers=drv))
        for c, v in c_row.items():
            long_rows.append(dict(date=d, code=c, contrib_gross=float(v),
                                  fee=float(mat["fee"].at[d, c]),
                                  overnight=float(mat["overnight"].at[d, c]),
                                  intraday=float(mat["intraday"].at[d, c]),
                                  trade_exec=float(mat["trade_exec"].at[d, c]),
                                  ca_residual=float(mat["ca_residual"].at[d, c])))
    worst_out = pd.DataFrame(rows)
    worst_out.to_csv(C.OUT / "worst20_days.csv", index=False)
    pd.DataFrame(long_rows).to_csv(C.OUT / "worst20_contrib_long.csv", index=False)

    # =====================================================================
    # 3b  three largest drawdowns
    # =====================================================================
    mdd = C.maxdd_stats(nav)
    ep = C.drawdown_episodes(nav, top=10)
    ep.to_csv(C.OUT / "drawdown_episodes_all.csv", index=False)
    top3 = ep.head(3).copy()
    # add the benchmark (CSI300) return over the same peak->recovery window
    context = []
    for r in top3.itertuples():
        end = r.recovery_date or nav.index[-1]
        if "bench_sh000300" in nav_df.columns:
            bench = nav_df["bench_sh000300"].astype(float)
            idx_ret = float(bench.loc[end] / bench.loc[r.peak_date] - 1.0)
        else:
            idx_ret = float("nan")
        context.append(dict(index_ret_during=idx_ret,
                            nav_ret_during=float(nav.loc[end] / nav.loc[r.peak_date] - 1.0)))
    top3 = pd.concat([top3.reset_index(drop=True), pd.DataFrame(context)], axis=1)
    top3.to_csv(C.OUT / "drawdowns_top3.csv", index=False)

    # =====================================================================
    # 3c  top-5 stock contributions + exclude-top1 sensitivity
    # =====================================================================
    pnl_actual = float(nav.iloc[-1] - C.CAP)
    gross_by_code = mat["contrib_gross"].sum(axis=0)
    fee_by_code = mat["fee"].sum(axis=0)
    net_by_code = gross_by_code - fee_by_code
    total = pd.DataFrame({"code": net_by_code.index,
                          "contrib_gross": gross_by_code.to_numpy(),
                          "fees": fee_by_code.to_numpy(),
                          "contrib_net": net_by_code.to_numpy()})
    total = total.sort_values("contrib_net", ascending=False)
    total["pct_of_total_pnl"] = total["contrib_net"] / pnl_actual
    total["contrib_gross_share"] = total["contrib_gross"] / gross_by_code.sum()
    total.to_csv(C.OUT / "stock_contrib_all.csv", index=False)
    top5 = total.head(5).copy().reset_index(drop=True)
    top5.to_csv(C.OUT / "top5_stock_contrib.csv", index=False)

    top_code = str(top5.iloc[0]["code"])
    # do we ever hold this code? check it has a materially positive net contribution
    c_series = mat["contrib_gross"].get(top_code) - mat["fee"].get(top_code)
    c_series = c_series.reindex(nav.index).fillna(0.0)
    r = ret.copy()
    r.iloc[0] = np.nan
    base = nav.iloc[0]
    n = len(nav)
    # rebuild NAV path zeroing that stock's net contribution
    path = [float(nav.iloc[0])]
    for i in range(1, n):
        prev = path[-1]
        adj_ret = (nav.iloc[i] - nav.iloc[i - 1] - float(c_series.iloc[i])) / nav.iloc[i - 1]
        path.append(prev * (1.0 + adj_ret))
    path = pd.Series(path, index=nav.index)
    sens = pd.DataFrame([
        dict(case="baseline A", total_return=float(nav.iloc[-1] / C.CAP - 1.0),
             max_drawdown=float(C.maxdd_series(nav).min()),
             final_nav=float(nav.iloc[-1]),
             cagr=float((nav.iloc[-1] / C.CAP) ** (252.0 / n) - 1.0)),
        dict(case=f"exclude top-1 winner ({top_code}), no reselection",
             total_return=float(path.iloc[-1] / C.CAP - 1.0),
             max_drawdown=float(C.maxdd_series(path).min()),
             final_nav=float(path.iloc[-1]),
             cagr=float((path.iloc[-1] / C.CAP) ** (252.0 / n) - 1.0)),
    ])
    sens.to_csv(C.OUT / "exclude_top1_sensitivity.csv", index=False)
    path.to_csv(C.OUT / "exclude_top1_nav.csv")

    bucket_totals = {k: float(mat[k].to_numpy().sum()) for k in
                     ("overnight", "intraday", "trade_exec", "ca_residual", "fee")}
    summary = {
        "reconciliation_max_abs_gap_cny": float(recon["gap"].abs().max()),
        "split_max_abs_gap_cny": float(daily["check_gap"].abs().max()),
        "bucket_totals_cny": bucket_totals,
        "overnight_share_of_abs": float(abs(bucket_totals["overnight"]) /
                                        (abs(bucket_totals["overnight"]) +
                                         abs(bucket_totals["intraday"]) +
                                         abs(bucket_totals["trade_exec"]) +
                                         abs(bucket_totals["ca_residual"]))),
        "worst20_ret_sum": float(worst["ret"].sum()),
        "worst_day": dict(date=str(worst.index[0]), ret=float(worst.iloc[0]["ret"]),
                          pnl=float(worst.iloc[0]["pnl"])),
        "maxdd": mdd,
        "top3_drawdowns": top3.to_dict(orient="records"),
        "top5": top5.to_dict(orient="records"),
        "pnl_actual": pnl_actual,
        "gross_sum_check": float(gross_by_code.sum()),
        "exclude_top1": sens.to_dict(orient="records"),
        "contrib_positive_codes": int((net_by_code > 0).sum()),
        "contrib_negative_codes": int((net_by_code < 0).sum()),
        "n_codes_held": int(len(net_by_code)),
        "top5_share_of_total_pnl": float(top5["contrib_net"].sum() / pnl_actual),
    }
    (C.OUT / "step3abc_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    # console -------------------------------------------------------------
    print("\n=== 3a worst 20 days ===")
    print(worst_out[["date", "daily_pnl", "daily_return", "exposure", "overnight",
                     "intraday", "trade_exec", "fees", "drivers"]].to_string(index=False))
    print("\n=== 3b drawdowns ===")
    print(top3.to_string(index=False))
    print(f"\nmaxdd check: {mdd['max_drawdown']:.10f} peak {mdd['peak_date']} "
          f"trough {mdd['trough_date']}")
    print("\n=== 3c top5 ===")
    print(top5.to_string(index=False))
    print(f"pnl actual {pnl_actual:,.2f}; sum of stock net contributions "
          f"{net_by_code.sum():,.2f}; gross sum {gross_by_code.sum():,.2f}")
    print("\nexclude top1 sensitivity:")
    print(sens.to_string(index=False))
    print("\nwrote step3a/b/c outputs")


if __name__ == "__main__":
    main()
