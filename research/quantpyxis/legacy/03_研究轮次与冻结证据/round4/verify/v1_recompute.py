# -*- coding: utf-8 -*-
"""Round-4 verification item 1 + 5: recompute metrics from raw nav CSVs and compare.

Read-only over round4/out.  Writes nothing except verify/ outputs.
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R4 = HERE.parent
OUT = R4 / "out"
VER = HERE

CAP = 100_000.0
RTOL = 1e-6      # absolute tolerance for return / ratio style numbers
NAVTOL = 1e-4    # absolute tolerance for CNY nav levels

SEGS = [
    ("bull_2014_2015", "2014-07-01", "2015-06-12"),
    ("crash_2015_2016", "2015-06-15", "2016-01-28"),
    ("bear_2018", "2018-01-02", "2018-12-31"),
    ("bull_2019_2021", "2019-01-02", "2021-02-10"),
    ("bear_2021_2024", "2021-02-18", "2024-09-13"),
    ("bull_2024_now", "2024-09-18", "2026-10-19"),
]
LATEST = ("2024-09-18", "2026-10-19")


def load_nav(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"date": str})
    assert df["date"].is_monotonic_increasing, path
    return df


def metrics(df: pd.DataFrame) -> dict:
    n = df["nav"].astype(float)
    ret = n.pct_change().fillna(0.0)
    dd = n / n.cummax() - 1.0
    trough = dd.idxmin()
    peak = n.loc[:trough].idxmax()
    cagr = float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1.0)
    mdd = float(dd.min())
    out = dict(
        trading_days=int(len(n)), first_date=str(df["date"].iloc[0]),
        last_date=str(df["date"].iloc[-1]),
        final_nav=float(n.iloc[-1]),
        total_return=float(n.iloc[-1] / CAP - 1.0),
        max_drawdown=mdd,
        maxdd_peak_date=str(df["date"].iloc[peak]),
        maxdd_trough_date=str(df["date"].iloc[trough]),
        cagr=cagr, ann_vol=float(ret.std() * math.sqrt(252.0)),
        sharpe=float(ret.mean() / ret.std() * math.sqrt(252.0)),
        calmar=float(cagr / abs(mdd)) if mdd < 0 else None,
        avg_exposure=float(df["exposure"].astype(float).mean()),
        all_cash_days=int((df["market_value"].astype(float) <= .01).sum()),
        sum_fees=float(df["fees"].astype(float).sum()),
    )
    yr = {}
    d = pd.DataFrame({"nav": n, "date": df["date"].values})
    d["prev"] = d["nav"].shift(1)
    d.iloc[0, d.columns.get_loc("prev")] = CAP
    d["year"] = [x[:4] for x in d["date"]]
    for y, g in d.groupby("year"):
        yr[y] = float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1.0)
    out["yearly"] = yr
    return out


def engine_vol_sharpe(df: pd.DataFrame) -> dict:
    """Engine/metrics.py convention: nav.pct_change(fill_method=None).dropna() (drops the
    first-day NaN, so n-1 returns), ddof=1.  Phase A / A2 summaries took ann_vol/sharpe
    straight from result['metrics'], i.e. this convention; phase B..H recomputed from
    nav.pct_change().fillna(0.0)."""
    n = df["nav"].astype(float)
    r = n.pct_change(fill_method=None).dropna()
    return dict(ann_vol=float(r.std(ddof=1) * math.sqrt(252.0)),
                sharpe=float(r.mean() / r.std(ddof=1) * math.sqrt(252.0)))


def seg_stats(nav: pd.Series, dates: pd.Series, s: str, e: str) -> dict:
    m = (dates >= s) & (dates <= e)
    sub = nav[m]
    if len(sub) < 2:
        return {}
    return dict(ret=float(sub.iloc[-1] / sub.iloc[0] - 1.0),
                dd=float((sub / sub.cummax() - 1.0).min()),
                days=int(len(sub)))


def seg_series(df: pd.DataFrame):
    return df["nav"].astype(float), df["date"].astype(str)


class Cmp:
    def __init__(self, name):
        self.name = name
        self.rows = []
        self.fails = []

    def add(self, item, rec, rep, tol):
        diff = None if (rec is None or rep is None) else abs(float(rec) - float(rep))
        ok = diff is not None and (diff <= tol)
        self.rows.append(dict(scope=self.name, item=item, recomputed=rec, reported=rep,
                              abs_diff=diff, tol=tol, ok=bool(ok)))
        if not ok:
            self.fails.append(self.rows[-1])
        return ok

    def add_nav(self, item, rec, rep, tol=NAVTOL):
        diff = None if (rec is None or rep is None) else abs(float(rec) - float(rep))
        ok = diff is not None and (diff <= tol)
        self.rows.append(dict(scope=self.name, item=item, recomputed=rec, reported=rep,
                              abs_diff=diff, tol=tol, ok=bool(ok)))
        if not ok:
            self.fails.append(self.rows[-1])
        return ok


def cmp_metric_row(cmp: Cmp, rec: dict, row: dict, fields=(
        "total_return", "max_drawdown", "cagr", "ann_vol", "sharpe",
        "calmar", "avg_exposure", "latest_ret", "latest_dd",
        "y2024", "y2025", "y2026")):
    for f in fields:
        if f not in row:
            continue
        rep = row.get(f)
        if rep is None or (isinstance(rep, float) and not np.isfinite(rep)):
            continue
        recv = rec.get(f)
        if recv is None:
            continue
        cmp.add(f, recv, rep, RTOL)
    return cmp


def main():
    log = []
    def P(*a):
        s = " ".join(str(x) for x in a)
        log.append(s)
        print(s)

    P("=" * 100)
    P("ITEM 1: recompute from raw nav CSVs vs summary CSVs")
    P(f"tolerances: ratios abs<= {RTOL:g}, nav CNY abs<= {NAVTOL:g}")
    P("=" * 100)
    all_rows = []
    all_fails = []

    # ---------------------------------------------------------------- claimed set
    CLAIMED = {
        "V_base": (OUT / "full_V_base_nav.csv",
                   dict(total_return=0.6869, max_drawdown=-0.1448)),
        "b100_n30_top15": (OUT / "full_b100_n30_top15_nav.csv",
                           dict(total_return=1.7456, max_drawdown=-0.2398,
                                sharpe=0.82, latest_ret=0.1496)),
        "n90_top20_novolcap": (OUT / "full_n90_top20_novolcap_nav.csv",
                               dict(total_return=2.4819, max_drawdown=-0.3002,
                                    sharpe=0.76, latest_ret=0.2200, latest_dd=-0.1904)),
        "n90_top15_novolcap": (OUT / "full_n90_top15_novolcap_nav.csv",
                               dict(total_return=2.8511, max_drawdown=-0.3202,
                                    sharpe=0.80, latest_ret=0.2162)),
        "REF_none_100": (OUT / "full_REF_none_100_nav.csv",
                         dict(total_return=1.7634, max_drawdown=-0.5619)),
        "bh_csi300": (OUT / "full_bh_csi300_nav.csv",
                      dict(total_return=0.9239, max_drawdown=-0.4670, latest_ret=0.3165)),
        "bh_chinext": (OUT / "full_bh_chinext_nav.csv",
                       dict(total_return=0.9485, max_drawdown=-0.6961, latest_ret=0.7871)),
    }
    recomputed = {}
    loaded = {}
    for tag, (path, claims) in CLAIMED.items():
        df = load_nav(path)
        loaded[tag] = df
        m = metrics(df)
        n_s, d_s = seg_series(df)
        m["latest_ret"] = seg_stats(n_s, d_s, *LATEST).get("ret")
        m["latest_dd"] = seg_stats(n_s, d_s, *LATEST).get("dd")
        m["segments"] = {name: seg_stats(n_s, d_s, s, e) for name, s, e in SEGS}
        recomputed[tag] = m
        c = Cmp(f"claim:{tag}")
        # the Lead's claim table quotes 4 decimals for returns/DDs and 2 for Sharpe;
        # compare at the precision actually stated (half a unit in the last place)
        prec = {"total_return": 5e-5, "max_drawdown": 5e-5, "latest_ret": 5e-5,
                "latest_dd": 5e-5, "sharpe": 5e-3}
        for f, v in claims.items():
            c.add(f, m.get(f), v, prec.get(f, RTOL))
        all_rows += c.rows
        all_fails += c.fails
        P(f"\n[{tag}] {path.name}")
        P(f"  window {m['first_date']}..{m['last_date']}  trading_days={m['trading_days']}")
        P(f"  final_nav={m['final_nav']:.6f}  total_return={m['total_return']:.10f}"
          f"  maxDD={m['max_drawdown']:.10f} (peak {m['maxdd_peak_date']} -> trough {m['maxdd_trough_date']})")
        P(f"  cagr={m['cagr']:.10f} ann_vol={m['ann_vol']:.10f} sharpe={m['sharpe']:.10f}"
          f" calmar={m['calmar']}")
        P(f"  avg_exposure={m['avg_exposure']:.6f} all_cash_days={m['all_cash_days']}"
          f" sum_fees={m['sum_fees']:.2f}")
        P("  yearly: " + "  ".join(f"{y}={v:.6f}" for y, v in sorted(m["yearly"].items())))
        for name, s, e in SEGS:
            st = m["segments"][name]
            P(f"    seg {name:16s} {s}..{e}  ret={st['ret']:+.10f} dd={st['dd']:+.10f} days={st['days']}")

    # ---------------------------------------------------- compare with full_summary
    P("\n" + "-" * 100)
    P("A) vs out/full_summary.csv (phase B candidates, 2014-07-01..2026-10-19)")
    P("-" * 100)
    fs = pd.read_csv(OUT / "full_summary.csv")
    for _, row in fs.iterrows():
        tag = row["tag"]
        path = OUT / f"full_{tag}_nav.csv"
        if not path.exists():
            P(f"  [MISS] {tag}: nav csv missing")
            continue
        df = load_nav(path)
        m = metrics(df)
        n_s, d_s = seg_series(df)
        m["latest_ret"] = seg_stats(n_s, d_s, *LATEST).get("ret")
        m["latest_dd"] = seg_stats(n_s, d_s, *LATEST).get("dd")
        m["y2024"], m["y2025"], m["y2026"] = m["yearly"].get("2024"), m["yearly"].get("2025"), m["yearly"].get("2026")
        c = Cmp(f"full_summary:{tag}")
        cmp_metric_row(c, m, row.to_dict())
        all_rows += c.rows
        all_fails += c.fails
        P(f"  {tag:22s} fails={len(c.fails)}" + (
            "" if not c.fails else "  " + "; ".join(
                f"{f['item']} rec={f['recomputed']!r} rep={f['reported']!r} d={f['abs_diff']!r}"
                for f in c.fails)))

    # -------------------------------------------------- full_summary.json benchmarks
    P("\n" + "-" * 100)
    P("B) vs out/full_summary.json benchmarks block")
    P("-" * 100)
    fj = json.load(io.open(OUT / "full_summary.json", encoding="utf-8"))
    for tag, rep in fj["benchmarks"].items():
        path = OUT / f"full_{tag}_nav.csv"
        if not path.exists():
            P(f"  [MISS] {tag}: nav csv missing")
            continue
        df = load_nav(path)
        m = metrics(df)
        n_s, d_s = seg_series(df)
        m["latest_ret"] = seg_stats(n_s, d_s, *LATEST).get("ret")
        m["latest_dd"] = seg_stats(n_s, d_s, *LATEST).get("dd")
        c = Cmp(f"full_summary.json.bench:{tag}")
        for f in ("total_return", "max_drawdown", "latest_ret", "latest_dd"):
            c.add(f, m.get(f), rep.get(f), RTOL)
        all_rows += c.rows
        all_fails += c.fails
        P(f"  {tag:16s} fails={len(c.fails)}" + (
            "" if not c.fails else "  " + "; ".join(
                f"{f['item']} rec={f['recomputed']!r} rep={f['reported']!r} d={f['abs_diff']!r}"
                for f in c.fails)))

    # ----------------------------------------------------- vs phase*_summary CSVs
    P("\n" + "-" * 100)
    P("C) vs phaseA/A2 (2014-2023) and phaseC/D/E/F/G/H (full window) summaries")
    P("-" * 100)
    phase_files = ["phaseA_summary.csv", "phaseA2_summary.csv", "phaseC_summary.csv",
                   "phaseD_summary.csv", "phaseE_summary.csv", "phaseF_summary.csv",
                   "phaseG_summary.csv", "phaseH_summary.csv"]
    for fn in phase_files:
        p = OUT / fn
        if not p.exists():
            P(f"  [MISS] {fn}")
            continue
        df_s = pd.read_csv(p)
        prefix = "" if fn.startswith("phaseA") else "full_"
        nfail_before = len(all_fails)
        checked = 0
        for _, row in df_s.iterrows():
            tag = row["tag"]
            path = OUT / f"{prefix}{tag}_nav.csv"
            if not path.exists():
                P(f"  [MISS] {fn}: nav csv for {tag} missing")
                continue
            df = load_nav(path)
            m = metrics(df)
            if fn.startswith("phaseA"):
                # phase A/A2 summaries report engine metrics convention
                m.update(engine_vol_sharpe(df))
            n_s, d_s = seg_series(df)
            m["latest_ret"] = seg_stats(n_s, d_s, *LATEST).get("ret")
            m["latest_dd"] = seg_stats(n_s, d_s, *LATEST).get("dd")
            m["y2024"], m["y2025"], m["y2026"] = m["yearly"].get("2024"), m["yearly"].get("2025"), m["yearly"].get("2026")
            c = Cmp(f"{fn}:{tag}")
            cmp_metric_row(c, m, row.to_dict())
            checked += 1
            all_rows += c.rows
            all_fails += c.fails
            if c.fails:
                P(f"  {fn} {tag:24s} FAILS: " + "; ".join(
                    f"{f['item']} rec={f['recomputed']!r} rep={f['reported']!r} d={f['abs_diff']!r}"
                    for f in c.fails))
        P(f"  {fn:24s} tags_checked={checked} new_fails={len(all_fails) - nfail_before}")

    # --------------------------------------------------- vs full_segments / yearly
    P("\n" + "-" * 100)
    P("D) vs out/full_segments.csv and out/full_yearly.csv (phase B tags)")
    P("-" * 100)
    seg_rep = pd.read_csv(OUT / "full_segments.csv")
    n_seg_fail = 0
    for _, row in seg_rep.iterrows():
        tag, name, s, e = row["tag"], row["segment"], row["start"], row["end"]
        path = OUT / f"full_{tag}_nav.csv"
        if not path.exists():
            continue
        df = load_nav(path)
        n_s, d_s = seg_series(df)
        st = seg_stats(n_s, d_s, s, e)
        c = Cmp(f"full_segments:{tag}:{name}")
        c.add("ret", st.get("ret"), row["ret"], RTOL)
        c.add("dd", st.get("dd"), row["dd"], RTOL)
        all_rows += c.rows
        all_fails += c.fails
        if c.fails:
            n_seg_fail += 1
            P(f"  SEGFAIL {tag:22s} {name:16s} " + "; ".join(
                f"{f['item']} rec={f['recomputed']!r} rep={f['reported']!r} d={f['abs_diff']!r}"
                for f in c.fails))
    P(f"  full_segments.csv rows={len(seg_rep)} segment_fail_rows={n_seg_fail}")
    yr_rep = pd.read_csv(OUT / "full_yearly.csv")
    n_yr_fail = 0
    for _, row in yr_rep.iterrows():
        tag, year = row["tag"], str(row["year"])
        path = OUT / f"full_{tag}_nav.csv"
        if not path.exists():
            continue
        df = load_nav(path)
        m = metrics(df)
        c = Cmp(f"full_yearly:{tag}:{year}")
        c.add("ret", m["yearly"].get(year), row["ret"], RTOL)
        all_rows += c.rows
        all_fails += c.fails
        if c.fails:
            n_yr_fail += 1
            P(f"  YEARFAIL {tag:22s} {year} " + "; ".join(
                f"{f['item']} rec={f['recomputed']!r} rep={f['reported']!r} d={f['abs_diff']!r}"
                for f in c.fails))
    P(f"  full_yearly.csv rows={len(yr_rep)} year_fail_rows={n_yr_fail}")

    # --------------------------------------- phaseC_segments / phaseD_segments
    P("\n" + "-" * 100)
    P("E) vs out/phaseC_segments.csv and out/phaseD_segments.csv")
    P("-" * 100)
    for fn in ("phaseC_segments.csv", "phaseD_segments.csv"):
        p = OUT / fn
        if not p.exists():
            P(f"  [MISS] {fn}")
            continue
        rep = pd.read_csv(p)
        nf = 0
        for _, row in rep.iterrows():
            tag, name, s, e = row["tag"], row["segment"], row["start"], row["end"]
            path = OUT / f"full_{tag}_nav.csv"
            if not path.exists():
                continue
            df = load_nav(path)
            n_s, d_s = seg_series(df)
            st = seg_stats(n_s, d_s, s, e)
            c = Cmp(f"{fn}:{tag}:{name}")
            c.add("ret", st.get("ret"), row["ret"], RTOL)
            c.add("dd", st.get("dd"), row["dd"], RTOL)
            all_rows += c.rows
            all_fails += c.fails
            if c.fails:
                nf += 1
                P(f"  SEGFAIL {fn} {tag:24s} {name:16s} " + "; ".join(
                    f"{f['item']} rec={f['recomputed']!r} rep={f['reported']!r} d={f['abs_diff']!r}"
                    for f in c.fails))
        P(f"  {fn:24s} rows={len(rep)} segment_fail_rows={nf}")

    # ------------------------------------------------------------- item 5 sanity
    P("\n" + "=" * 100)
    P("ITEM 5: V_base full-window run == round-3 S0 on the 2014-07-01..2023-12-29 sub-window?")
    P("=" * 100)
    df = loaded["V_base"]
    sub = df[(df["date"] >= "2014-07-01") & (df["date"] <= "2023-12-29")].reset_index(drop=True)
    msub = metrics(sub)
    P(f"V_base full run sub-window 2014-07-01..2023-12-29: days={msub['trading_days']}"
      f" final_nav={msub['final_nav']:.6f} total={msub['total_return']:.10f}"
      f" maxDD={msub['max_drawdown']:.10f} ann_vol={msub['ann_vol']:.10f}"
      f" sharpe={msub['sharpe']:.10f} avg_exposure={msub['avg_exposure']:.6f}")
    r3 = json.load(io.open(R4.parent / "round3" / "out" / "metrics.json", encoding="utf-8"))["variants"]["S0"]
    c = Cmp("round3-S0-vs-Vbase-subwindow")
    c.add("total_return", msub["total_return"], r3["total_return"], 1e-9)
    c.add("max_drawdown", msub["max_drawdown"], r3["max_drawdown"], 1e-9)
    c.add("ann_vol", msub["ann_vol"], r3["ann_vol"], 1e-9)
    c.add("sharpe", msub["sharpe"], r3["sharpe"], 1e-9)
    c.add("cagr", msub["cagr"], r3["cagr"], 1e-9)
    c.add("avg_exposure", msub["avg_exposure"], r3["avg_exposure"], 1e-9)
    c.add("trading_days", msub["trading_days"], r3["trading_days"], 0)
    all_rows += c.rows
    all_fails += c.fails
    P(f"  round3 S0 reported: total={r3['total_return']:.10f} maxDD={r3['max_drawdown']:.10f}"
      f" ann_vol={r3['ann_vol']:.10f} sharpe={r3['sharpe']:.10f} cagr={r3['cagr']:.10f}"
      f" days={r3['trading_days']}")
    for f in c.rows:
        P(f"  {f['item']:14s} recomputed={f['recomputed']!r} round3={f['reported']!r}"
          f" diff={f['abs_diff']!r} {'OK' if f['ok'] else 'FAIL'}")
    # also compare with the phase-A separate run of V_base (same 2014-2023 window)
    pa = load_nav(OUT / "V_base_nav.csv")
    P(f"  phaseA V_base_nav.csv days={len(pa)} first={pa['date'].iloc[0]} last={pa['date'].iloc[-1]}")
    same_len = len(pa) == len(sub)
    nav_match = same_len and bool(np.allclose(pa["nav"].astype(float).to_numpy(),
                                             sub["nav"].astype(float).to_numpy(), rtol=0, atol=1e-9))
    P(f"  full-run-subwindow vs phaseA-run nav identical: {nav_match}  (n={len(pa)} vs {len(sub)})")
    all_rows.append(dict(scope="phaseA-vs-fullsub", item="nav_identical",
                         recomputed=nav_match, reported=True, abs_diff=0 if nav_match else None,
                         tol=0, ok=nav_match))
    if not nav_match:
        all_fails.append(all_rows[-1])
    # trading-day counts
    P(f"  full window trading days: {len(loaded['V_base'])}"
      f"  (2014-07-01..2026-10-19) vs 2014-2023 sub-window: {len(sub)}")

    # ----------------------------------------------------------------- summary
    P("\n" + "=" * 100)
    P(f"TOTAL comparisons: {len(all_rows)}  FAILS: {len(all_fails)}")
    if all_fails:
        for f in all_fails:
            P(f"  FAIL {f['scope']} :: {f['item']} recomputed={f['recomputed']!r}"
              f" reported={f['reported']!r} abs_diff={f['abs_diff']!r} tol={f['tol']!r}")
    P("=" * 100)

    payload = dict(
        generated="round4-verify-item1",
        tolerances=dict(ratio=RTOL, nav=NAVTOL),
        recomputed_claim_set={k: {kk: vv for kk, vv in v.items() if kk != "segments"}
                              for k, v in recomputed.items()},
        recomputed_segments={k: v["segments"] for k, v in recomputed.items()},
        comparisons=all_rows,
        fails=all_fails,
    )
    (VER / "v1_recompute.json").write_text(
        json.dumps(payload, indent=1, default=str), encoding="utf-8")
    (VER / "v1_recompute.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\nwrote", VER / "v1_recompute.log", "and v1_recompute.json")


if __name__ == "__main__":
    main()
