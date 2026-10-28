# -*- coding: utf-8 -*-
"""Independent validation of published data/panel bars (Lead-published, Sina KLC source).

Produces data/qa/panel_validation.json + sample CSVs + data/tmp/panel_validation_diff.csv.
Checks:
 1. per-year rows/codes/date ranges, emptiness, duplicates, sort, NaN/OHLC sanity
 2. volume/amount unit sanity via implied VWAP = amount/volume
 3. universe coverage per year (listed expected vs present), delisted coverage, completeness guard
 4. cross-validation vs independent Tencent raw parts (code 'sh.600000'->'600000'; volume ratio ~100)
 5. cross-validation vs baostock on N=30 stocks (single serialized session; close/volume/preclose/pctchg units)
 6. qfq/raw consistency: ratio piecewise constant; jumps only on baostock dividend dates; anchor check
 7. spot checks on 2015-06-12, 2018-12-28, 2024-09-30
Env: QA_SKIP_BAOSTOCK=1 to skip baostock cross-validation (dry run).
"""
import glob
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
sys.path.insert(0, os.path.join(ROOT, "libs"))  # vendored baostock
DATA = os.path.join(ROOT, "data")
TMP = os.path.join(DATA, "tmp")
QA = os.path.join(DATA, "qa")
PANEL = os.path.join(DATA, "panel")
START, END = "2012-01-01", "2026-10-19"
RAW_SPEC = ["code", "date", "open", "high", "low", "close", "preclose", "volume",
            "amount", "turn", "pctchg", "isst", "tradestatus"]
QFQ_SPEC = ["code", "date", "open", "high", "low", "close"]
SPOT_DATES = ["2015-06-12", "2018-12-28", "2024-09-30"]
SPOT_CODES = ["600000", "000001", "300033", "600519", "002415"]


def canon(s):
    return re.sub(r"^(sh|sz)\.?", "", str(s))


def load_years(prefix):
    out = {}
    for p in sorted(glob.glob(os.path.join(PANEL, "%s_20*.csv.gz" % prefix))):
        base = os.path.basename(p)
        year = base.split("_")[-1].split(".")[0]
        if os.path.getsize(p) < 200:
            continue
        try:
            df = pd.read_csv(p, dtype={"code": str, "date": str})
        except Exception as e:  # noqa: BLE001
            print("READ FAIL", p, e, flush=True)
            continue
        if len(df) == 0:
            continue
        df["code"] = df["code"].map(canon)
        out[year] = df
    return out


def load_universe():
    p = os.path.join(DATA, "meta", "universe.csv")
    if not os.path.exists(p):
        return None
    u = pd.read_csv(p, dtype=str)
    if len(u) < 10:
        return None
    u["code"] = u["code"].map(canon)
    for c in ["ipo_date", "out_date"]:
        if c in u.columns:
            u[c] = u[c].fillna("")
    return u


def load_factors():
    p = os.path.join(TMP, "factors", "factors_all.csv.gz")
    if not os.path.exists(p):
        return None
    try:
        fac = pd.read_csv(p, dtype={"code": str, "dividOperateDate": str})
    except Exception as e:  # noqa: BLE001
        print("factor read fail", e, flush=True)
        return None
    fac["code"] = fac["code"].map(canon)
    fac = fac.dropna(subset=["foreAdjustFactor"])
    return fac


def load_tx_parts():
    frames = []
    for p in sorted(glob.glob(os.path.join(TMP, "tx_parts", "raw", "shard*.csv.gz"))):
        try:
            frames.append(pd.read_csv(p, dtype={"code": str, "date": str}))
        except Exception:  # noqa: BLE001
            continue
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["code", "date"], keep="last")
    df["code"] = df["code"].map(canon)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df  # volume in Tencent 手


def login_retry(max_try=8):
    import baostock as bs
    for k in range(max_try):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0 + 1.2 * k)
    return False


def bs_code(code6):
    return ("sh." if code6.startswith(("60", "68")) else "sz.") + code6


def bs_query(code6, fields, start, end, fq="3", max_try=3):
    import baostock as bs
    c = bs_code(code6)
    for k in range(max_try):
        try:
            rs = bs.query_history_k_data_plus(c, fields, start_date=start, end_date=end,
                                              frequency="d", adjustflag=fq)
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code == "0":
                return rows
        except Exception:  # noqa: BLE001
            pass
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass
        login_retry(3)
        time.sleep(0.4)
    return None


def main():
    os.makedirs(QA, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    t0 = time.time()
    skip_bs = os.environ.get("QA_SKIP_BAOSTOCK", "0") == "1"
    res = {}
    raw_years = load_years("bars")
    qfq_years = load_years("bars_qfq")
    res["years_present"] = sorted(raw_years)
    res["years_missing"] = [str(y) for y in range(2012, 2027) if str(y) not in raw_years]
    res["qfq_years_present"] = sorted(qfq_years)
    res["qfq_years_missing"] = [str(y) for y in range(2012, 2027) if str(y) not in qfq_years]
    print("raw years:", sorted(raw_years), "qfq years:", sorted(qfq_years), flush=True)
    if not raw_years:
        res["status"] = "no_panel_data"
        json.dump(res, open(os.path.join(QA, "panel_validation.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, default=float)
        print("NO PANEL DATA", flush=True)
        return
    raw = pd.concat([g.assign(year=y) for y, g in raw_years.items()], ignore_index=True)
    for c in ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctchg"]:
        if c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
    print("raw rows", len(raw), "codes", raw["code"].nunique(), flush=True)
    res["columns_raw"] = list(raw.columns)
    res["columns_missing_vs_spec"] = [c for c in RAW_SPEC if c not in raw.columns]
    res["columns_extra"] = [c for c in raw.columns if c not in RAW_SPEC + ["year"]]
    # --- structure
    struct = {"rows": int(len(raw)), "codes": int(raw["code"].nunique()),
              "date_min": str(raw["date"].min()), "date_max": str(raw["date"].max()),
              "dup_keys": int(raw.duplicated(subset=["code", "date"]).sum())}
    srt = raw[["code", "date"]]
    struct["sorted_by_code_date"] = bool(
        srt.sort_values(["code", "date"]).reset_index(drop=True).equals(srt.reset_index(drop=True)))
    struct["sorted_by_date_code"] = bool(
        srt.sort_values(["date", "code"]).reset_index(drop=True).equals(srt.reset_index(drop=True)))
    struct["nan_ohlc"] = {c: int(raw[c].isna().sum()) for c in ["open", "high", "low", "close"]}
    if "volume" in raw:
        struct["nan_volume"] = int(raw["volume"].isna().sum())
        struct["neg_volume"] = int((raw["volume"] < 0).sum())
    if "amount" in raw:
        struct["nan_amount"] = int(raw["amount"].isna().sum())
        struct["nonpos_amount"] = int((raw["amount"] <= 0).sum())
        badamt = raw[raw["amount"] <= 0]
        struct["nonpos_amount_volume_zero"] = int((badamt["volume"] == 0).sum()) if "volume" in badamt else None
        struct["nonpos_amount_sample"] = badamt[["code", "date", "open", "high", "low", "close",
                                                 "volume", "amount", "tradestatus"]].head(10).to_dict(orient="records")
    struct["high_lt_low"] = int((raw["high"] < raw["low"]).sum())
    struct["close_out_of_range"] = int(((raw["close"] > raw["high"] + 1e-6) | (raw["close"] < raw["low"] - 1e-6)).sum())
    struct["open_out_of_range"] = int(((raw["open"] > raw["high"] + 1e-6) | (raw["open"] < raw["low"] - 1e-6)).sum())
    struct["nonpos_price"] = int((raw[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    struct["preclose_first_row_nan"] = int(raw["preclose"].isna().sum()) if "preclose" in raw else None
    if "amount" in raw and "volume" in raw:
        mask = (raw["volume"] > 0) & (raw["amount"] > 0) & raw["close"].notna()
        sub = raw[mask]
        if len(sub):
            implied = (sub["amount"] / sub["volume"]).replace([np.inf, -np.inf], np.nan).dropna()
            ratio = (implied / sub.loc[implied.index, "close"]).replace([np.inf, -np.inf], np.nan).dropna()
            struct["vwap_sanity"] = {"rows": int(len(ratio)),
                                     "median_implied_vwap_over_close": float(ratio.median()),
                                     "p01": float(ratio.quantile(0.01)), "p99": float(ratio.quantile(0.99))}
    res["structural"] = struct
    # --- internal consistency: close/preclose vs pctchg; preclose vs prev close; extreme returns
    ic = {}
    uni = load_universe()
    fac = load_factors()
    if "preclose" in raw.columns and "pctchg" in raw.columns:
        sub = raw.dropna(subset=["preclose", "pctchg"]).copy()
        sub = sub[sub["preclose"] > 0]
        if len(sub):
            implied = sub["close"] / sub["preclose"] - 1.0
            d = (implied - sub["pctchg"]).abs()
            ic["n_rows"] = int(len(sub))
            ic["pctchg_implied_max_abs_diff"] = float(d.max())
            ic["pctchg_implied_p99_abs_diff"] = float(d.quantile(0.99))
            ic["pctchg_implied_n_gt_1e-3"] = int((d > 1e-3).sum())
            ic["pctchg_implied_n_gt_5e-3"] = int((d > 5e-3).sum())
            worst = sub.assign(d=d).sort_values("d", ascending=False).head(10)
            ic["pctchg_implied_worst"] = worst[["code", "date", "close", "preclose", "pctchg"]].to_dict(orient="records")
    if "preclose" in raw.columns:
        rr = raw.sort_values(["code", "date"]).copy()
        rr["prev_close"] = rr.groupby("code", sort=False)["close"].shift(1)
        rr["prev_date"] = rr.groupby("code", sort=False)["date"].shift(1)
        cmp = rr.dropna(subset=["preclose", "prev_close"])
        if len(cmp):
            diff = (cmp["preclose"] - cmp["prev_close"]).abs()
            ic["preclose_n_rows_compared"] = int(len(cmp))
            ic["preclose_diff_max"] = float(diff.max())
            ic["preclose_diff_n_gt_1e-6"] = int((diff > 1e-6).sum())
            ic["preclose_diff_n_gt_0.01"] = int((diff > 0.01).sum())
            if fac is not None:
                ev = {c: set(g["dividOperateDate"]) for c, g in fac.groupby("code", sort=False)}
                mism = cmp[diff > 0.01]
                n_on = sum(1 for r in mism.itertuples() if r.date in ev.get(r.code, set()))
                ic["preclose_mismatch_n"] = int(len(mism))
                ic["preclose_mismatch_on_event_date"] = int(n_on)
                ic["preclose_mismatch_off_event_sample"] = [
                    (r.code, r.date, float(r.preclose), float(r.prev_close)) for r in mism.head(10).itertuples()]
    if "pctchg" in raw.columns:
        th = np.where(raw["code"].str.startswith(("30", "301")),
                      np.where(raw["date"] >= "2020-08-24", 0.206, 0.106), 0.106)
        ext = raw[np.abs(raw["pctchg"]) > th]
        ic["extreme_return_rows"] = int(len(ext))
        ic["extreme_return_sample"] = ext[["code", "date", "pctchg", "close", "preclose"]].head(10).to_dict(orient="records")
    res["internal_consistency"] = ic
    # --- universe date-range check (rows outside ipo/out range)
    if uni is not None and "ipo_date" in uni.columns:
        m = raw[["code", "date"]].merge(uni[["code", "ipo_date", "out_date"]], on="code", how="left")
        bad_ipo = m[(m["ipo_date"].fillna("") != "") & (m["date"] < m["ipo_date"].fillna("9999-99-99"))]
        bad_out = m[(m["out_date"].fillna("") != "") & (m["date"] > m["out_date"].fillna(""))]
        res["universe_date_range"] = {"rows_before_ipo": int(len(bad_ipo)),
                                      "rows_after_out_date": int(len(bad_out)),
                                      "before_ipo_sample": bad_ipo.head(8).to_dict(orient="records"),
                                      "after_out_sample": bad_out.head(8).to_dict(orient="records")}
    # --- per year
    per_year = {}
    for year, g in raw.groupby("year"):
        per_year[year] = {"rows": int(len(g)), "codes": int(g["code"].nunique()),
                          "date_min": str(g["date"].min()), "date_max": str(g["date"].max()),
                          "dup_keys": int(g.duplicated(subset=["code", "date"]).sum())}
    res["per_year"] = per_year
    # --- universe coverage
    uni = load_universe()
    if uni is not None:
        uni_codes = set(uni["code"])
        span_cov = {}
        for year, g in raw.groupby("year"):
            yend, ystart = year + "-12-31", year + "-01-01"
            try:
                m = (uni["ipo_date"].fillna("") <= yend) & (
                    (uni["out_date"].fillna("") == "") | (uni["out_date"].fillna("") >= ystart))
                exp_codes = set(uni[m]["code"])
            except Exception:  # noqa: BLE001
                exp_codes = uni_codes
            have = set(g["code"].unique())
            span_cov[year] = {"expected": len(exp_codes), "present": len(have),
                              "missing_n": len(exp_codes - have),
                              "extra_n": len(have - exp_codes),
                              "missing_sample": sorted(exp_codes - have)[:15]}
        res["universe_coverage"] = span_cov
        res["universe_n"] = int(len(uni))
        res["completeness_ok"] = all(v["present"] >= 0.5 * max(1, v["expected"]) for v in span_cov.values())
        if "status" in uni.columns:
            de = set(uni[uni["status"].astype(str) == "0"]["code"])
            res["delisted_n"] = len(de)
            res["delisted_with_data"] = len(de & set(raw["code"].unique()))
            res["delisted_missing_sample"] = sorted(de - set(raw["code"].unique()))[:20]
    # --- cross-validation vs Tencent parts
    tx = load_tx_parts()
    cv = {"available": tx is not None}
    if tx is not None and len(tx):
        cols = ["code", "date", "close", "volume", "open", "high", "low"]
        m = raw[cols].merge(tx[cols], on=["code", "date"], how="inner", suffixes=("_p", "_tx"))
        print("tencent overlap rows", len(m), "codes", m["code"].nunique() if len(m) else 0, flush=True)
        cv.update({"rows": int(len(m)), "codes": int(m["code"].nunique()) if len(m) else 0})
        if len(m):
            d = (m["close_p"] - m["close_tx"]).abs()
            cv["close_max_abs_diff"] = float(d.max())
            cv["close_p99_abs_diff"] = float(d.quantile(0.99))
            cv["close_n_gt_0.01"] = int((d > 0.011).sum())
            cv["close_n_gt_0.02"] = int((d > 0.021).sum())
            vr = (m["volume_p"] / m["volume_tx"]).replace([np.inf, -np.inf], np.nan).dropna()
            cv["volume_ratio_gt100_median"] = float(vr.median()) if len(vr) else None
            cv["volume_ratio_p01"] = float(vr.quantile(0.01)) if len(vr) else None
            cv["volume_ratio_p99"] = float(vr.quantile(0.99)) if len(vr) else None
            worst = m.assign(d=d).sort_values("d", ascending=False).head(200)
            worst[["code", "date", "close_p", "close_tx", "volume_p", "volume_tx"]].to_csv(
                os.path.join(QA, "cross_validation_vs_tencent.csv"), index=False)
            cv["worst_sample"] = worst[["code", "date", "close_p", "close_tx"]].head(8).to_dict(orient="records")
    res["cross_validation_tencent"] = cv
    # --- qfq checks
    fac = load_factors()
    if qfq_years:
        q = pd.concat([g.assign(year=y) for y, g in qfq_years.items()], ignore_index=True)
        for c in ["open", "high", "low", "close"]:
            q[c] = pd.to_numeric(q[c], errors="coerce")
        qs = {"rows": int(len(q)), "codes": int(q["code"].nunique()),
              "dup_keys": int(q.duplicated(subset=["code", "date"]).sum()),
              "high_lt_low": int((q["high"] < q["low"]).sum()),
              "columns": list(q.columns),
              "columns_missing_vs_spec": [c for c in QFQ_SPEC if c not in q.columns]}
        lastp = raw.sort_values("date").groupby("code").tail(1).set_index("code")
        lastq = q.sort_values("date").groupby("code").tail(1).set_index("code")
        common = lastp.index.intersection(lastq.index)
        if len(common):
            # compare only codes whose last available dates coincide (otherwise not an anchor test)
            same_date = [c for c in common if lastp.loc[c, "date"] == lastq.loc[c, "date"]]
            qs["anchor_n"] = len(same_date)
            if same_date:
                ad = (lastp.loc[same_date, "close"] - lastq.loc[same_date, "close"]).abs()
                qs["anchor_max_abs_diff"] = float(ad.max())
                qs["anchor_n_gt_0.02"] = int((ad > 0.021).sum())
                qs["anchor_sample_bad"] = [(c, float(lastp.loc[c, "close"]), float(lastq.loc[c, "close"]))
                                           for c in ad.sort_values(ascending=False).head(8).index]
                lr = (lastq.loc[same_date, "close"] / lastp.loc[same_date, "close"]).replace([np.inf, -np.inf], np.nan).dropna()
                if len(lr):
                    qs["last_ratio_median"] = float(lr.median())
                    qs["last_ratio_p01"] = float(lr.quantile(0.01))
                    qs["last_ratio_p99"] = float(lr.quantile(0.99))
                    qs["last_ratio_n_abs_dev_gt_0.01"] = int(((lr - 1.0).abs() > 0.01).sum())
                    qs["last_ratio_n_abs_dev_gt_0.05"] = int(((lr - 1.0).abs() > 0.05).sum())
                    qs["last_ratio_worst"] = [(c, float(lr[c])) for c in (lr - 1.0).abs().sort_values(ascending=False).head(10).index]
            qs["anchor_n_different_last_date"] = len(common) - len(same_date)
        # ratio piecewise constant using dividend event dates from baostock factor table
        qidx = q.set_index(["code", "date"])["close"]
        ridx = raw.set_index(["code", "date"])["close"]
        ratio = (qidx / ridx).dropna().rename("ratio").reset_index()
        # Lead-approved QA criterion: piecewise-constant ratio per code; jumps only on ex-div dates;
        # qfq daily returns == raw daily returns except event dates. Constant per-stock scale is OK.
        merged = (q[["code", "date", "close"]].rename(columns={"close": "qclose"})
                  .merge(raw[["code", "date", "close"]].rename(columns={"close": "rclose"}),
                         on=["code", "date"], how="inner").sort_values(["code", "date"]))
        merged["qret"] = merged.groupby("code", sort=False)["qclose"].pct_change()
        merged["rret"] = merged.groupby("code", sort=False)["rclose"].pct_change()
        merged["ret_diff"] = (merged["qret"] - merged["rret"]).abs()
        ret_chk = merged.dropna(subset=["ret_diff"])
        qs["return_consistency_n"] = int(len(ret_chk))
        qs["return_consistency_max_abs_diff"] = float(ret_chk["ret_diff"].max()) if len(ret_chk) else None
        qs["return_consistency_p99"] = float(ret_chk["ret_diff"].quantile(0.99)) if len(ret_chk) else None
        qs["return_consistency_n_gt_1e-3"] = int((ret_chk["ret_diff"] > 1e-3).sum())
        ratio = ratio.sort_values(["code", "date"])
        ratio["prev_ratio"] = ratio.groupby("code")["ratio"].shift(1)
        ratio["ratio_chg"] = (ratio["ratio"] / ratio["prev_ratio"] - 1.0).abs()
        chk = ratio.dropna(subset=["ratio_chg"])
        jump_small = chk[chk["ratio_chg"] > 1e-7]
        jump = chk[chk["ratio_chg"] > 1e-3]
        qs["n_rows_checked"] = int(len(chk))
        qs["n_ratio_change_gt_1e-7(rounding)"] = int(len(jump_small))
        qs["n_ratio_jumps_gt_1e-3(real)"] = int(len(jump))
        qs["median_ratio_change"] = float(chk["ratio_chg"].median())
        qs["p99_ratio_change"] = float(chk["ratio_chg"].quantile(0.99))
        qs["max_ratio_jump"] = float(jump["ratio_chg"].max()) if len(jump) else None
        if fac is not None:
            ev = {c: set(g["dividOperateDate"]) for c, g in fac.groupby("code", sort=False)}
            import datetime as _dt
            ev_int = {}
            for c, ds in ev.items():
                ev_int[c] = np.array([_dt.date.fromisoformat(d).toordinal() for d in sorted(ds)])
            n_on = n_off = 0
            off_samples = []
            n_off_near = n_off_far = 0
            far_samples = []
            for row in jump.head(300000).itertuples():
                ds = ev.get(row.code)
                if ds and row.date in ds:
                    n_on += 1
                else:
                    n_off += 1
                    arr = ev_int.get(row.code)
                    near = False
                    if arr is not None and len(arr):
                        o = _dt.date.fromisoformat(row.date).toordinal()
                        k = int(np.searchsorted(arr, o))
                        cand = [abs(int(arr[i]) - o) for i in (k - 1, k) if 0 <= i < len(arr)]
                        near = bool(cand) and min(cand) <= 5
                    if near:
                        n_off_near += 1
                    else:
                        n_off_far += 1
                        if len(far_samples) < 12:
                            far_samples.append((row.code, row.date, float(row.ratio_chg)))
                    if len(off_samples) < 12:
                        off_samples.append((row.code, row.date, float(row.ratio_chg)))
            qs["jumps_gt_1e-3_on_baostock_event_date"] = n_on
            qs["jumps_gt_1e-3_not_on_event_date"] = n_off
            qs["jumps_not_on_event_within5days"] = n_off_near
            qs["jumps_not_on_event_gt5days"] = n_off_far
            qs["jumps_gt5days_samples"] = far_samples
            qs["jumps_not_on_event_samples"] = off_samples
            # event coverage for first 2000 codes in factor table
            hit = total_ev = 0
            rmap = {c: dict(zip(g["date"], g["ratio_chg"])) for c, g in ratio.groupby("code", sort=False)}
            for c in list(ev.keys())[:2000]:
                if c not in rmap:
                    continue
                gm = rmap[c]
                for d in ev[c]:
                    if START <= d <= END:
                        total_ev += 1
                        if gm.get(d) is not None and gm.get(d) > 1e-7:
                            hit += 1
            qs["events_in_range_checked"] = total_ev
            qs["events_with_ratio_jump"] = hit
            # Lead-approved criterion: return mismatch only allowed on ex-div dates (covered codes only)
            judged = ret_chk[ret_chk["code"].isin(ev.keys())]
            if len(judged):
                is_ev = np.array([r.date in ev.get(r.code, ()) for r in judged.itertuples()])
                bad = judged[(judged["ret_diff"] > 1e-3) & (~is_ev)]
                bad_ev = judged[(judged["ret_diff"] > 1e-3) & is_ev]
                qs["ret_judged_rows"] = int(len(judged))
                qs["return_mismatch_gt_1e-3_nonevent"] = int(len(bad))
                qs["return_mismatch_gt_1e-3_onevent"] = int(len(bad_ev))
                qs["return_mismatch_nonevent_sample"] = [
                    (r.code, r.date, float(r.qret) if pd.notna(r.qret) else None,
                     float(r.rret) if pd.notna(r.rret) else None)
                    for r in bad.head(10).itertuples()]
            # judged coverage
            qs["codes_with_factor_data"] = int(len(ev))
            qs["codes_judged_for_qfq"] = int(len(set(ratio["code"]) & set(ev.keys())))
        res["qfq_structural"] = qs
    # --- baostock cross-validation (single session; skip via env for dry runs)
    codes_all = sorted(raw["code"].unique())
    rng = np.random.default_rng(1416)
    sample = list(rng.choice(codes_all, size=min(30, len(codes_all)), replace=False))
    bs_stats = {"attempted": not skip_bs, "skipped": skip_bs, "n_sample": len(sample)}
    if not skip_bs:
        import_ok = login_retry()
        bs_stats["login_ok"] = import_ok
        bs_rows = []
        if import_ok:
            ws, we = "2015-01-01", "2015-06-30"
            for code in sample:
                p = raw[(raw["code"] == code) & (raw["date"] >= ws) & (raw["date"] <= we)]
                if p.empty:
                    continue
                br = bs_query(code, "date,close,preclose,volume,amount,pctChg", ws, we, fq="3")
                if br is None:
                    continue
                bmap = {r[0]: r for r in br}
                for row in p.itertuples():
                    b = bmap.get(row.date)
                    if b is None:
                        continue
                    bs_rows.append({"code": code, "date": row.date,
                                    "close_panel": row.close, "close_bs": float(b[1]),
                                    "preclose_panel": row.preclose, "preclose_bs": float(b[2]),
                                    "volume_panel": row.volume, "volume_bs": float(b[3]),
                                    "amount_panel": row.amount, "amount_bs": float(b[4]),
                                    "pctchg_panel": row.pctchg, "pctchg_bs": float(b[5])})
            try:
                import baostock as bs
                bs.logout()
            except Exception:  # noqa: BLE001
                pass
            if bs_rows:
                bdf = pd.DataFrame(bs_rows)
                bdf.to_csv(os.path.join(QA, "cross_validation_vs_baostock.csv.gz"),
                           index=False, compression="gzip")
                da = (bdf["amount_panel"] / bdf["amount_bs"]).replace([np.inf, -np.inf], np.nan).dropna()
                pr = (bdf["pctchg_panel"] / bdf["pctchg_bs"]).replace([np.inf, -np.inf], np.nan)
                pr = pr[np.isfinite(pr)]
                bs_stats.update({
                    "rows": int(len(bdf)), "codes": int(bdf["code"].nunique()),
                    "close_max_abs_diff": float((bdf["close_panel"] - bdf["close_bs"]).abs().max()),
                    "close_p99_abs_diff": float((bdf["close_panel"] - bdf["close_bs"]).abs().quantile(0.99)),
                    "close_n_gt_0.02": int(((bdf["close_panel"] - bdf["close_bs"]).abs() > 0.021).sum()),
                    "preclose_max_abs_diff": float((bdf["preclose_panel"] - bdf["preclose_bs"]).abs().max()),
                    "volume_ratio_median": float((bdf["volume_panel"] / bdf["volume_bs"]).median()),
                    "amount_ratio_median": float(da.median()) if len(da) else None,
                    "pctchg_ratio_median(panel/bs; ~0.01 => panel in fraction)": float(pr.median()) if len(pr) else None,
                })
    res["cross_validation_baostock"] = bs_stats
    # --- spot checks
    spot = {}
    for d in SPOT_DATES:
        g = raw[raw["date"] == d]
        spot[d] = {"rows": int(len(g)), "codes": int(g["code"].nunique())}
        if len(g):
            spot[d]["median_pctchg"] = float(g["pctchg"].median())
            spot[d]["median_amount_yi"] = float(g["amount"].median() / 1e8) if "amount" in g else None
    detail = {}
    for c in SPOT_CODES:
        for d in SPOT_DATES:
            row = raw[(raw["code"] == c) & (raw["date"] == d)]
            if len(row):
                r0 = row.iloc[0]
                detail["%s@%s" % (c, d)] = {k: (float(r0[k]) if k in row.columns and pd.notna(r0[k]) else None)
                                            for k in ["open", "high", "low", "close", "preclose", "volume", "amount"]}
    res["spot_checks"] = {"by_date": spot, "by_code_date": detail}
    # --- diff summary CSV for appendix
    try:
        rows = []
        if tx is not None and len(tx):
            m2 = raw[["code", "date", "close", "volume"]].merge(
                tx[["code", "date", "close", "volume"]], on=["code", "date"], how="inner",
                suffixes=("_p", "_tx"))
            m2["close_diff"] = m2["close_p"] - m2["close_tx"]
            m2["volume_ratio"] = m2["volume_p"] / (m2["volume_tx"] * 100.0)
            g = m2.groupby("code").agg(n=("date", "size"),
                                       close_max_abs_diff=("close_diff", lambda s: float(s.abs().max())),
                                       close_p99_abs_diff=("close_diff", lambda s: float(s.abs().quantile(0.99))),
                                       volume_ratio_median=("volume_ratio", "median")).reset_index()
            g.to_csv(os.path.join(TMP, "panel_validation_diff.csv"), index=False)
            res["diff_csv"] = os.path.join(TMP, "panel_validation_diff.csv")
    except Exception as e:  # noqa: BLE001
        print("diff csv fail", e, flush=True)
    res["elapsed_s"] = round(time.time() - t0, 1)
    with open(os.path.join(QA, "panel_validation.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=float)
    print("VALIDATION DONE in %.1fs" % (time.time() - t0), flush=True)
    for k in ["structural", "cross_validation_tencent", "cross_validation_baostock", "qfq_structural"]:
        if k in res:
            print(k, json.dumps(res[k], ensure_ascii=False, default=float)[:1500], flush=True)


if __name__ == "__main__":
    main()
