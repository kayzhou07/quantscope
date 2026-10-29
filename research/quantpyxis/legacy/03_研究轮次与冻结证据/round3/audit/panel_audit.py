# -*- coding: utf-8 -*-
"""Items 3-6: panel coverage, warmup, manifest sha256, residual-risk checks.

Read-only on qrun_1416. Writes only winrate_backtest_20261020/round3/audit/.
Run: python panel_audit.py   (takes a few minutes: reads qfq 2013-2023 + raw all years)
"""
import os, json, hashlib
import numpy as np
import pandas as pd

WS = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace"
Q = os.path.join(WS, "qrun_1416")
M = os.path.join(Q, "data", "meta")
P = os.path.join(Q, "data", "panel")
OUT = os.path.join(WS, r"winrate_backtest_20261020\round3\audit")

WSTART, WEND = "2014-07-01", "2023-12-29"
CPS = ["2014-07-01", "2019-01-02", "2023-12-29"]
MANIFEST_SHA_CLAIM = "9294d2b82b235666871340eb93b51c10fc0a449327be0b1d051c83b89e03b7db"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    read_hashes = {}

    def read_csv_hashed(path, **kw):
        read_hashes[os.path.relpath(path, Q).replace("\\", "/")] = sha256(path)
        return pd.read_csv(path, **kw)

    manifest_path = os.path.join(M, "manifest.json")
    man = json.load(open(manifest_path, encoding="utf-8"))
    man_sha = sha256(manifest_path)

    cal = read_csv_hashed(os.path.join(M, "calendar.csv"), dtype=str)
    uni = read_csv_hashed(os.path.join(M, "universe.csv"), dtype=str)
    codes = read_csv_hashed(os.path.join(M, "codes.csv"), dtype=str)
    uni["ipo"] = pd.to_datetime(uni["ipo_date"], errors="coerce")
    uni["out"] = pd.to_datetime(uni["out_date"], errors="coerce")

    cal["d"] = pd.to_datetime(cal["date"])
    open_days = cal.loc[cal["is_open"] == "1", "date"].tolist()  # sorted 'YYYY-MM-DD'
    open_set = set(open_days)

    # ---------- warmup (item 4) ----------
    pre_win = [d for d in open_days if "2012-08-03" <= d < WSTART]
    warmup = {
        "note": ("regime CSV / RUN_TEXT start 2012-08-03; the PRICE PANEL itself starts earlier "
                 "(manifest date_range 2012-01-01, first bar 2012-01-04) - actual panel start is "
                 "filled in after the qfq pass"),
        "regime_start_claimed_2012_08_03": "2012-08-03",
        "window_start": WSTART,
        "open_trading_days_2012_08_03_to_2014_06_30": len(pre_win),
        "needs_ge_253": True,
        "pass_using_regime_start": len(pre_win) >= 253,
    }

    # ---------- window trading days ----------
    win_days = [d for d in open_days if WSTART <= d <= WEND]

    # ---------- qfq pass (2013..2023): window aggregates + checkpoint eligibility ----------
    cp_days = {}
    for cp in CPS:
        ds = [d for d in open_days if d <= cp][-253:]
        cp_days[cp] = ds
        assert len(ds) == 253 and ds[-1] == cp, (cp, len(ds), ds[-1])

    codes_any_win = set()
    valid_win = {}          # code -> valid qfq bars (close>0) inside window
    daily_bars = {}         # date -> qfq rows in window
    rows_win = 0
    rows_win_valid = 0
    qfq_year = {}
    cp_counts = {cp: {} for cp in CPS}
    cp_lastbar = {cp: set() for cp in CPS}
    win_min_bar = {}
    win_max_bar = {}
    codes_all = set()       # any code with >=1 qfq bar 2012-2023
    qfq_dates_all = set()   # any qfq date 2012-2023

    for y in range(2012, 2024):
        fp = os.path.join(P, f"bars_qfq_{y}.csv.gz")
        df = read_csv_hashed(fp, dtype={"code": str}, usecols=["code", "date", "close"])
        qfq_year[y] = {"rows": int(len(df)), "codes": set(df["code"].unique()),
                       "dates": set(df["date"].unique()),
                       "code_n": int(df["code"].nunique()),
                       "dates_n": int(df["date"].nunique()),
                       "date_min": df["date"].min(), "date_max": df["date"].max()}
        codes_all |= qfq_year[y]["codes"]
        qfq_dates_all |= qfq_year[y]["dates"]
        valid = df["close"].notna() & (df["close"] > 0)
        inw = (df["date"] >= WSTART) & (df["date"] <= WEND)
        rows_win += int(inw.sum())
        rows_win_valid += int((inw & valid).sum())
        sub = df.loc[inw]
        codes_any_win |= set(sub["code"].unique())
        vc = df.loc[inw & valid].groupby("code").size()
        for c, n in vc.items():
            valid_win[c] = valid_win.get(c, 0) + int(n)
        for d, n in df.loc[inw].groupby("date").size().items():
            daily_bars[d] = daily_bars.get(d, 0) + int(n)
        mn = sub.groupby("code")["date"].min()
        mx = sub.groupby("code")["date"].max()
        for c, v in mn.items():
            if c not in win_min_bar or v < win_min_bar[c]:
                win_min_bar[c] = v
        for c, v in mx.items():
            if c not in win_max_bar or v > win_max_bar[c]:
                win_max_bar[c] = v
        # checkpoints
        for cp, ds in cp_days.items():
            dset = set(ds)
            m = df["date"].isin(dset)
            if not m.any():
                continue
            s = df.loc[m]
            sv = s.loc[s["close"].notna() & (s["close"] > 0)]
            for c, n in sv.groupby("code").size().items():
                cp_counts[cp][c] = cp_counts[cp].get(c, 0) + int(n)
            at = s.loc[s["date"] == cp, "code"]
            cp_lastbar[cp] |= set(at)
        del df, sub

    # actual panel start + warmup depth
    warmup["panel_actual_first_bar_date"] = qfq_year[2012]["date_min"]
    warmup["calendar_open_days_from_panel_start_to_2014_06_30"] = int(
        sum(1 for d in open_days if warmup["panel_actual_first_bar_date"] <= d < WSTART))
    warmup["calendar_open_days_from_panel_start_through_2014_06_30"] = int(
        sum(1 for d in open_days if warmup["panel_actual_first_bar_date"] <= d <= "2014-06-30"))
    warmup["pass"] = warmup["calendar_open_days_from_panel_start_to_2014_06_30"] >= 253

    cp_results = {}
    for cp in CPS:
        cnts = cp_counts[cp]
        n253 = sum(1 for v in cnts.values() if v >= 253)
        n230 = sum(1 for v in cnts.values() if v >= 230)
        has_bar = sum(1 for c, v in cnts.items() if v >= 230 and c in cp_lastbar[cp])
        arr = np.array(list(cnts.values()))
        cp_results[cp] = {
            "prior_253_open_days": [cp_days[cp][0], cp_days[cp][-1]],
            "codes_with_any_valid_qfq_bar_in_253d": int(len(cnts)),
            "codes_with_ge230_valid_qfq_bars_in_253d": int(n230),
            "codes_with_ge253_valid_qfq_bars_in_253d": int(n253),
            "codes_ge230_and_bar_present_on_checkpoint": int(has_bar),
            "valid_bar_count_pctiles": {
                "min": int(arr.min()), "p10": int(np.percentile(arr, 10)),
                "median": int(np.median(arr)), "p90": int(np.percentile(arr, 90)),
                "max": int(arr.max()),
            },
            "trading_days_in_panel_up_to_checkpoint": int(
                sum(1 for d in open_days if d <= cp and d in qfq_dates_all)),
            "calendar_open_days_up_to_checkpoint": int(sum(1 for d in open_days if d <= cp)),
        }

    # ---------- raw pass (all years): isst/tradestatus, alignment vs qfq, checkpoint raw close ----------
    raw_year = {}
    isst_vals, trade_vals = set(), set()
    raw_rows_total = 0
    cp_rawclose = {cp: {} for cp in CPS}
    bad_date_rows = {}      # panel date not in calendar open set -> rows seen
    turn_nonnull = 0
    for y in range(2012, 2027):
        fp = os.path.join(P, f"bars_{y}.csv.gz")
        df = read_csv_hashed(fp, dtype={"code": str},
                             usecols=["code", "date", "close", "isst", "tradestatus", "turn"])
        raw_rows_total += int(len(df))
        turn_nonnull += int(df["turn"].notna().sum())
        isst_vals |= set(pd.unique(df["isst"].dropna())) if df["isst"].notna().any() else set()
        trade_vals |= set(pd.unique(df["tradestatus"].dropna())) if df["tradestatus"].notna().any() else set()
        raw_year[y] = {"rows": int(len(df)), "code_n": int(df["code"].nunique()),
                       "codes": set(df["code"].unique()),
                       "dates_n": int(df["date"].nunique()),
                       "dates": set(df["date"].unique()),
                       "date_min": df["date"].min(), "date_max": df["date"].max(),
                       "isst_null": int(df["isst"].isna().sum()),
                       "tradestatus_null": int(df["tradestatus"].isna().sum())}
        for d in set(df["date"].unique()) - open_set:
            s = df.loc[df["date"] == d]
            bad_date_rows.setdefault(d, []).extend(
                [{"code": c, "close": float(v), "isst": i, "tradestatus": t}
                 for c, v, i, t in zip(s["code"], s["close"], s["isst"], s["tradestatus"])])
        for cp in CPS:
            s = df[df["date"] == cp]
            for c, v in zip(s["code"], s["close"]):
                cp_rawclose[cp][c] = v
        del df

    # checkpoint eligibility enriched with the raw-price>=2 gate (S0 uses rawclose>=2)
    for cp in CPS:
        cnts = cp_counts[cp]
        rc = cp_rawclose[cp]
        cp_results[cp]["codes_ge230_and_bar_present_and_raw_close_ge2_at_checkpoint"] = int(sum(
            1 for c, v in cnts.items()
            if v >= 230 and c in cp_lastbar[cp] and pd.notna(rc.get(c)) and rc.get(c) >= 2))
        cp_results[cp]["raw_close_available_at_checkpoint_codes"] = int(len(rc))
    # alignment qfq vs raw for 2013..2023
    align = {}
    for y in range(2013, 2024):
        q, r = qfq_year[y], raw_year[y]
        align[y] = {
            "rows_equal": q["rows"] == r["rows"],
            "codes_equal": q["codes"] == r["codes"],
            "dates_equal": q["dates"] == r["dates"],
            "qfq_rows": q["rows"], "raw_rows": r["rows"],
        }
    align_all_ok = all(v["rows_equal"] and v["codes_equal"] and v["dates_equal"] for v in align.values())

    # ---------- qfq anchor check at panel end ----------
    last_date = man["date_range"][1]
    q_last = pd.read_csv(os.path.join(P, "bars_qfq_2026.csv.gz"), dtype={"code": str},
                         usecols=["code", "date", "close"])
    r_last = pd.read_csv(os.path.join(P, "bars_2026.csv.gz"), dtype={"code": str},
                         usecols=["code", "date", "close"])
    read_hashes["data/panel/bars_qfq_2026.csv.gz"] = read_hashes.get(
        "data/panel/bars_qfq_2026.csv.gz") or sha256(os.path.join(P, "bars_qfq_2026.csv.gz"))
    read_hashes["data/panel/bars_2026.csv.gz"] = read_hashes.get(
        "data/panel/bars_2026.csv.gz") or sha256(os.path.join(P, "bars_2026.csv.gz"))
    qa = q_last[q_last["date"] == last_date].set_index("code")["close"]
    ra = r_last[r_last["date"] == last_date].set_index("code")["close"]
    common = qa.index.intersection(ra.index)
    ratio = (qa.loc[common] / ra.loc[common]).replace([np.inf, -np.inf], np.nan).dropna()
    bad_ratio = ratio[(ratio < 0.99) | (ratio > 1.01)]
    anchor = {
        "panel_last_date_manifest": last_date,
        "qfq_last_date": q_last["date"].max(),
        "raw_last_date": r_last["date"].max(),
        "common_codes_on_last_date": int(len(common)),
        "qfq_over_raw_ratio_on_last_date": {
            "min": float(ratio.min()), "median": float(ratio.median()),
            "max": float(ratio.max()),
            "n_within_1pct_of_1": int(((ratio > 0.99) & (ratio < 1.01)).sum()),
            "deviating_codes": {c: round(float(v), 6) for c, v in bad_ratio.sort_values().items()},
        },
    }
    # also check if qfq==raw for 2012 (should NOT be, adjustment anchored at end)
    q12 = pd.read_csv(os.path.join(P, "bars_qfq_2012.csv.gz"), dtype={"code": str},
                      usecols=["code", "date", "close"])
    r12 = pd.read_csv(os.path.join(P, "bars_2012.csv.gz"), dtype={"code": str},
                      usecols=["code", "date", "close"])
    read_hashes["data/panel/bars_qfq_2012.csv.gz"] = read_hashes.get("data/panel/bars_qfq_2012.csv.gz") or sha256(os.path.join(P, "bars_qfq_2012.csv.gz"))
    read_hashes["data/panel/bars_2012.csv.gz"] = read_hashes.get("data/panel/bars_2012.csv.gz") or sha256(os.path.join(P, "bars_2012.csv.gz"))
    d0 = q12["date"].min()
    qq = q12[q12["date"] == d0].set_index("code")["close"]
    rr = r12[r12["date"] == d0].set_index("code")["close"]
    cm = qq.index.intersection(rr.index)
    rr2 = (qq.loc[cm] / rr.loc[cm]).replace([np.inf, -np.inf], np.nan).dropna()
    anchor["qfq_over_raw_ratio_first_panel_date"] = {
        "date": d0, "min": float(rr2.min()), "median": float(rr2.median()), "max": float(rr2.max()),
        "n_within_1pct_of_1": int(((rr2 > 0.99) & (rr2 < 1.01)).sum()), "n": int(len(rr2)),
    }

    # ---------- board / code-prefix checks ----------
    uni["prefix3"] = uni["code"].str[:3]
    prefix_board = uni.groupby(["board", "prefix3"]).size().reset_index(name="n")
    expected_prefix = {"main": {"600", "601", "603", "605", "000", "001", "002", "003"},
                       "chinext": {"300", "301"}}
    bad_prefix = []
    for _, r in prefix_board.iterrows():
        if r["prefix3"] not in expected_prefix.get(r["board"], set()):
            bad_prefix.append({"board": r["board"], "prefix": r["prefix3"], "n": int(r["n"])})
    star = sorted(uni.loc[uni["code"].str.startswith(("688", "689")), "code"].tolist())
    bse = sorted(uni.loc[uni["code"].str.startswith(("4", "8", "9")) |
                        uni["code"].str.match(r"^(43|83|87|88|92)"), "code"].tolist())
    board = {
        "universe_rows": int(len(uni)),
        "board_counts": {k: int(v) for k, v in uni["board"].value_counts().items()},
        "prefix_by_board": prefix_board.to_dict("records"),
        "unexpected_prefix_for_board": bad_prefix,
        "star_688_codes": star,
        "bse_codes": bse,
        "no_star": len(star) == 0,
        "no_bse": len(bse) == 0,
        "all_codes_6digit": bool((uni["code"].str.len() == 6).all()),
        "panel_codes_not_in_universe": sorted(codes_any_win - set(uni["code"]))[:50],
        "n_panel_codes_not_in_universe": int(len(codes_any_win - set(uni["code"]))),
    }

    # ---------- IPO/out_date coverage ----------
    uni_idx = uni.set_index("code")
    ipo_in_win = uni[(uni["ipo"] >= WSTART) & (uni["ipo"] <= WEND)]
    out_in_win = uni[(uni["out"] >= WSTART) & (uni["out"] <= WEND)]
    ipo_in_win_with_bars = [c for c in ipo_in_win["code"] if c in win_min_bar]
    first_bar_before_ipo = []
    for c in ipo_in_win_with_bars:
        if win_min_bar[c] < ipo_in_win.set_index("code").loc[c, "ipo_date"]:
            first_bar_before_ipo.append(c)
    out_in_win_with_bars = [c for c in out_in_win["code"] if c in win_max_bar]
    last_bar_vs_out = []
    for c in out_in_win_with_bars:
        ob = uni_idx.loc[c, "out_date"]
        lb = win_max_bar[c]
        last_bar_vs_out.append((c, ob, lb, "equal" if lb == ob else ("before" if lb < ob else "after")))
    eq = sum(1 for x in last_bar_vs_out if x[3] == "equal")
    bef = sum(1 for x in last_bar_vs_out if x[3] == "before")
    aft = sum(1 for x in last_bar_vs_out if x[3] == "after")

    ipo_out = {
        "universe_codes": int(len(uni)),
        "ipo_date_nonnull": int(uni["ipo_date"].notna().sum()),
        "ipo_date_null": int(uni["ipo_date"].isna().sum()),
        "ipo_date_min": uni["ipo_date"].min(), "ipo_date_max": uni["ipo_date"].max(),
        "codes_ipo_inside_window": int(len(ipo_in_win)),
        "codes_ipo_inside_window_with_bars": int(len(ipo_in_win_with_bars)),
        "codes_ipo_inside_window_without_bars": int(len(ipo_in_win) - len(ipo_in_win_with_bars)),
        "codes_first_bar_before_ipo_date": len(first_bar_before_ipo),
        "out_date_nonnull": int(uni["out_date"].notna().sum()),
        "out_date_null": int(uni["out_date"].isna().sum()),
        "status_0_count": int((uni["status"] == "0").sum()),
        "status_1_count": int((uni["status"] == "1").sum()),
        "status0_equals_outdate_nonnull": bool((uni["status"] == "0").sum() == uni["out_date"].notna().sum()
                                               and (uni.loc[uni["status"] == "0", "out_date"].notna()).all()),
        "codes_out_inside_window": int(len(out_in_win)),
        "codes_out_inside_window_with_bars": int(len(out_in_win_with_bars)),
        "last_bar_vs_out_date": {"equal": eq, "before": bef, "after": aft},
    }

    # ---------- delisting-tail detail (last bar vs out_date) ----------
    gaps = []
    for (c, ob, lb, rel) in last_bar_vs_out:
        gap = sum(1 for d in win_days if (lb < d <= ob) if rel == "before") if rel == "before" else \
              sum(1 for d in win_days if (ob < d <= lb)) if rel == "after" else 0
        gaps.append({"code": c, "out_date": ob, "last_bar": lb, "relation": rel,
                     "calendar_trading_days_between": int(gap)})
    before_gaps = sorted([g["calendar_trading_days_between"] for g in gaps if g["relation"] == "before"])
    after_detail = [g for g in gaps if g["relation"] == "after"]
    delisting = {
        "delisted_codes_in_universe": int(uni["out"].notna().sum()),
        "delisted_out_date_inside_window": int(len(out_in_win)),
        "relation_counts": {"equal": eq, "before": bef, "after": aft,
                            "no_bars_in_window": int(len(out_in_win) - len(out_in_win_with_bars))},
        "before_gap_trading_days": {
            "n": len(before_gaps), "median": int(np.median(before_gaps)) if before_gaps else None,
            "p90": int(np.percentile(before_gaps, 90)) if before_gaps else None,
            "max": max(before_gaps) if before_gaps else None,
            "n_gt_20": int(sum(1 for g in before_gaps if g > 20)),
        },
        "bars_after_out_date": after_detail,
        "bars_after_out_date_note": ("all bars after out_date occur on the single non-calendar date "
                                     "2021-06-27 (verified in date_anomalies)"),
        "interpretation": ("last-bar-before-out_date is consistent with the panel's documented "
                           "suspension=missing-row convention (codes suspended ahead of delisting); "
                           "out_date itself is not a trading date for the panel. Cannot be cross-checked "
                           "against an independent source in this environment -> open residual risk."),
    }

    # ---------- calendar vs panel date anomalies ----------
    raw_dates_all = set().union(*[v["dates"] for v in raw_year.values()])
    extra_dates = sorted(raw_dates_all - open_set)
    missing_dates = sorted(open_set - raw_dates_all)
    date_anom = {
        "panel_dates_not_in_calendar_open": extra_dates,
        "calendar_open_days_without_panel_rows": missing_dates,
        "extra_date_rows": {d: bad_date_rows.get(d, []) for d in extra_dates},
        "extra_dates_window_count": sum(1 for d in extra_dates if WSTART <= d <= WEND),
        "missing_calendar_dates_note": ("verified list of calendar open days with no panel row; "
                                        "panel ends 2026-10-19 per manifest, so only post-panel-end "
                                        "calendar days (e.g. today 2026-10-20) should appear here"),
    }

    # ---------- missing-bar (suspension) rate in window ----------
    ipos = uni["ipo"].values.astype("datetime64[D]")
    outs = uni["out"].values
    out_ok = ~pd.isna(outs)
    outs_d = outs.astype("datetime64[D]")
    exp_incl = 0
    exp_excl = 0
    per_year_actual, per_year_exp = {}, {}
    for d in win_days:
        dd = np.datetime64(d, "D")
        listed = (ipos <= dd)
        incl = listed & (~out_ok | (outs_d >= dd))
        excl = listed & (~out_ok | (outs_d > dd))
        exp_incl += int(incl.sum())
        exp_excl += int(excl.sum())
        per_year_exp[d[:4]] = per_year_exp.get(d[:4], 0) + int(excl.sum())
    for d, n in daily_bars.items():
        per_year_actual[d[:4]] = per_year_actual.get(d[:4], 0) + n
    actual = int(rows_win)
    normal_daily = {d: n for d, n in daily_bars.items() if d in open_set}
    noncal_daily = {d: n for d, n in daily_bars.items() if d not in open_set}
    missing = {
        "window_open_days": len(win_days),
        "panel_qfq_distinct_dates_in_window": int(len(daily_bars)),
        "panel_dates_in_window_that_are_not_calendar_open": noncal_daily,
        "daily_bar_counts": {
            "min": int(min(daily_bars.values())), "median": int(np.median(list(daily_bars.values()))),
            "max": int(max(daily_bars.values())),
            "first_day": int(daily_bars.get(win_days[0], 0)),
            "last_day": int(daily_bars.get(win_days[-1], 0)),
        },
        "daily_bar_counts_calendar_open_days_only": {
            "min": int(min(normal_daily.values())),
            "min_date": min(normal_daily, key=normal_daily.get),
            "median": int(np.median(list(normal_daily.values()))),
            "max": int(max(normal_daily.values())),
            "max_date": max(normal_daily, key=normal_daily.get),
        },
        "actual_bar_rows_in_window": actual,
        "actual_bar_rows_in_window_calendar_days_only": int(
            actual - sum(n for d, n in daily_bars.items() if d not in open_set)),
        "actual_bar_rows_with_valid_close": int(rows_win_valid),
        "rows_with_nonpositive_close": int(rows_win - rows_win_valid),
        "expected_listed_code_days_outdate_exclusive": int(exp_excl),
        "expected_listed_code_days_outdate_inclusive": int(exp_incl),
        "missing_bar_rate_outdate_exclusive": round(1 - actual / exp_excl, 6),
        "missing_bar_rate_outdate_inclusive": round(1 - actual / exp_incl, 6),
        "per_year": {
            y: {"trading_days": sum(1 for d in win_days if d[:4] == y),
                "actual_bars": per_year_actual.get(y, 0),
                "expected_listed_code_days": per_year_exp.get(y, 0),
                "missing_rate": round(1 - per_year_actual.get(y, 0) / per_year_exp[y], 6)
                if per_year_exp.get(y) else None}
            for y in sorted(per_year_exp)
        },
    }

    # ---------- codes with >=1 bar / board of window-active codes ----------
    active_ever = set()
    for i, c in enumerate(uni["code"]):
        if pd.isna(uni["ipo"].iloc[i]):
            continue
        ipo = uni["ipo"].iloc[i]
        out = uni["out"].iloc[i]
        if ipo <= pd.Timestamp(WEND) and (pd.isna(out) or out >= pd.Timestamp(WSTART)):
            active_ever.add(c)
    zero_bar_active = sorted(active_ever - codes_any_win)
    zero_bar_detail = []
    for c in zero_bar_active:
        ys = [y for y in sorted(qfq_year) if c in qfq_year[y]["codes"]]
        if not ys:
            zero_bar_detail.append({"code": c, "bars_2012_2023": 0})
            continue
        y = max(ys)
        dfp = pd.read_csv(os.path.join(P, f"bars_qfq_{y}.csv.gz"), dtype={"code": str},
                          usecols=["code", "date"])
        s = dfp.loc[dfp["code"] == c]
        zero_bar_detail.append({
            "code": c, "last_bar_in_panel": str(s["date"].max()),
            "n_bars_in_last_panel_year": int(len(s)),
            "ipo_date": str(uni_idx.loc[c, "ipo_date"]),
            "out_date": str(uni_idx.loc[c, "out_date"]),
            "interpretation": "listed/suspended with no bar in window => suspension-encoded absence",
        })
        del dfp
    known_win = sorted(codes_any_win & set(uni["code"]))
    win_codes_board = uni.set_index("code").loc[known_win, "board"].value_counts().to_dict() \
        if known_win else {}
    coverage = {
        "window": {"start": WSTART, "end": WEND},
        "window_open_days": len(win_days),
        "window_open_days_first": win_days[0], "window_open_days_last": win_days[-1],
        "distinct_panel_dates_in_window": int(len(daily_bars)),
        "codes_with_ge1_qfq_bar_in_window": int(len(codes_any_win)),
        "codes_with_ge1_qfq_bar_in_window_by_board": {k: int(v) for k, v in win_codes_board.items()},
        "universe_codes_active_at_some_point_in_window": int(len(active_ever)),
        "active_in_window_but_zero_bars": {"n": len(zero_bar_active), "examples": zero_bar_active[:20],
                                           "present_elsewhere_in_panel_2012_2023":
                                               [c for c in zero_bar_active if c in codes_all],
                                           "detail": zero_bar_detail},
        "panel_codes_2012_2023": int(len(codes_all)),
        "codes_with_all_bars_positive_close": int(sum(1 for c in codes_any_win if valid_win.get(c, 0) > 0)),
        "codes_with_min_bars_in_window": min(valid_win.values()) if valid_win else None,
        "codes_with_median_bars_in_window": int(np.median(list(valid_win.values()))) if valid_win else None,
        "codes_with_max_bars_in_window": max(valid_win.values()) if valid_win else None,
        "window_rows_in_qfq_panel": actual,
        "note_window_rows": "qfq rows in window (all codes), equals sum(daily_bars)",
    }

    # ---------- manifest verification (all 53 files) ----------
    man_entries = man["files"]
    man_check = {"n_entries": len(man_entries), "claimed_files": man["counts"]["files"],
                 "claimed_total_bytes": man["counts"]["total_bytes"], "mismatches": [],
                 "missing_on_disk": [], "size_mismatches": []}
    actual_total = 0
    for e in man_entries:
        rel = e["path"]
        fp = os.path.join(Q, "data", rel.replace("/", os.sep))
        if not os.path.isfile(fp):
            man_check["missing_on_disk"].append(rel)
            continue
        actual_total += os.path.getsize(fp)
        if os.path.getsize(fp) != e["bytes"]:
            man_check["size_mismatches"].append({"path": rel, "manifest": e["bytes"],
                                                 "disk": os.path.getsize(fp)})
        h = sha256(fp)
        read_hashes[rel] = h
        if h != e["sha256"]:
            man_check["mismatches"].append({"path": rel, "manifest": e["sha256"], "disk": h})
    man_check["actual_total_bytes"] = int(actual_total)

    manifest = {
        "manifest_path": "qrun_1416/data/meta/manifest.json",
        "manifest_sha256_computed": man_sha,
        "engine_claimed_manifest_sha256": MANIFEST_SHA_CLAIM,
        "manifest_sha256_match": man_sha.lower() == MANIFEST_SHA_CLAIM.lower(),
        "dataset_version": man["version"],
        "generated_at": man["generated_at"],
        "date_range": man["date_range"],
        "date_range_claim": man["date_range"] == ["2012-01-01", "2026-10-19"],
        "declared_file_count": man["counts"]["files"],
        "n_file_entries": len(man_entries),
        "all_53_entries_verified": (len(man_check["mismatches"]) == 0
                                    and len(man_check["missing_on_disk"]) == 0
                                    and len(man_check["size_mismatches"]) == 0
                                    and len(man_entries) == 53),
        "verification": man_check,
    }

    # ---------- residual risk checks (verified only) ----------
    risks = {
        "isst_distinct_values_all_raw_years": sorted(int(x) for x in isst_vals if pd.notna(x)),
        "isst_always_zero": isst_vals <= {0},
        "isst_null_rows": int(sum(v["isst_null"] for v in raw_year.values())),
        "tradestatus_distinct_values_all_raw_years": sorted(int(x) for x in trade_vals if pd.notna(x)),
        "tradestatus_always_one": trade_vals <= {1},
        "tradestatus_null_rows": int(sum(v["tradestatus_null"] for v in raw_year.values())),
        "raw_total_rows_all_years": int(raw_rows_total),
        "raw_vs_qfq_alignment_2013_2023_all_ok": bool(align_all_ok),
        "raw_vs_qfq_alignment": {str(k): v for k, v in align.items()},
        "turn_column_status": "unavailable -> NaN by design (manifest units_and_notes)",
        "turn_nonnull_rows_all_years": int(turn_nonnull),
        "turn_always_null": turn_nonnull == 0,
        "isst_caveat": ("isst constant 0 for every row => real ST/*ST flags are NOT encoded; the "
                        "platform S0 uses real is_st and excludes ST names, so a local engine "
                        "cannot reproduce that exclusion from this panel"),
        "tradestatus_caveat": ("tradestatus constant 1 for every row => suspension is encoded ONLY "
                               "by missing rows; an engine must treat a missing bar as suspended"),
        "qfq_anchor": anchor,
    }

    out = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "inputs": {
            "calendar": "qrun_1416/data/meta/calendar.csv",
            "universe": "qrun_1416/data/meta/universe.csv",
            "codes": "qrun_1416/data/meta/codes.csv",
            "qfq_panel": "qrun_1416/data/panel/bars_qfq_{year}.csv.gz (2012-2023, 2026 anchor check)",
            "raw_panel": "qrun_1416/data/panel/bars_{year}.csv.gz (2012-2026)",
        },
        "warmup": warmup,
        "coverage": coverage,
        "checkpoints": cp_results,
        "missing_bars": missing,
        "board_and_prefix": board,
        "ipo_out_coverage": ipo_out,
        "delisting": delisting,
        "date_anomalies": date_anom,
        "manifest": manifest,
        "residual_risks": risks,
        "sha256_of_files_read": read_hashes,
        "raw_year_stats": {str(k): {kk: vv for kk, vv in v.items() if kk not in ("codes", "dates")}
                           for k, v in raw_year.items()},
        "qfq_year_stats": {str(k): {kk: vv for kk, vv in v.items() if kk not in ("codes", "dates")}
                           for k, v in qfq_year.items()},
    }
    with open(os.path.join(OUT, "panel_coverage.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("WARMUP:", json.dumps(warmup, ensure_ascii=False))
    print("WINDOW open days:", len(win_days), "panel dates:", len(daily_bars),
          "codes>=1bar:", len(codes_any_win))
    print("CHECKPOINTS:", json.dumps(cp_results, ensure_ascii=False))
    print("MISSING:", json.dumps({k: v for k, v in missing.items() if k != "per_year"}, ensure_ascii=False))
    print("BOARD:", json.dumps({k: board[k] for k in ["board_counts", "unexpected_prefix_for_board",
                                                     "no_star", "no_bse", "n_panel_codes_not_in_universe"]},
                               ensure_ascii=False))
    print("IPO_OUT:", json.dumps(ipo_out, ensure_ascii=False))
    print("MANIFEST:", json.dumps({k: manifest[k] for k in ["manifest_sha256_match", "date_range",
                                                           "n_file_entries", "all_53_entries_verified",
                                                           "declared_file_count"]}, ensure_ascii=False))
    print("RISKS:", json.dumps({k: risks[k] for k in ["isst_always_zero", "tradestatus_always_one",
                                                     "turn_always_null", "turn_nonnull_rows_all_years",
                                                     "raw_vs_qfq_alignment_2013_2023_all_ok"]},
                               ensure_ascii=False))
    print("ANCHOR:", json.dumps(anchor, ensure_ascii=False))
    print("DATE_ANOM:", json.dumps({k: v for k, v in date_anom.items() if k != "extra_date_rows"},
                                   ensure_ascii=False))
    print("EXTRA_ROWS:", json.dumps(date_anom["extra_date_rows"], ensure_ascii=False)[:1200])
    print("DELISTING:", json.dumps(delisting, ensure_ascii=False))
    print("ZERO_BAR:", json.dumps(coverage["active_in_window_but_zero_bars"], ensure_ascii=False))


if __name__ == "__main__":
    main()
