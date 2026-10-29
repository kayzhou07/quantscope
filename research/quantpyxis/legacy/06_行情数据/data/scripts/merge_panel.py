# -*- coding: utf-8 -*-
"""Build raw + qfq per-year bars panels from Tencent raw parts + baostock adjust factors.

Raw:
  data/panel/bars_{year}.csv.gz columns (exact SPEC):
    code,date,open,high,low,close,preclose,volume,amount,turn,pctchg,isst,tradestatus
  - prices: unadjusted real prices (Tencent fq=nofq)
  - volume: shares (Tencent 手 x100)
  - preclose: previous available raw close; ex-dividend-date preclose corrected with factor table
  - amount: volume_shares*(high+low+close)/3 proxy (Tencent kline has no amount) - documented
  - turn: empty; isst: 0; tradestatus: 1 (rows exist only on traded days)
QFQ:
  data/panel/bars_qfq_{year}.csv.gz columns: code,date,open,high,low,close,preclose,volume,amount,turn,pctchg,isst,tradestatus
  - derived: qfq = raw * foreAdjustFactor(date), where factor date-map uses last event row with
    dividOperateDate <= date, and for dates before the first event uses 1/backAdjustFactor(latest)
    (validated against baostock adjustflag=2 sample in qa).
Also writes data/tmp/panel_all_raw.csv.gz and data/tmp/panel_all_qfq.csv.gz for reuse.
"""
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
DATA = os.path.join(ROOT, "data")
TMP = os.path.join(DATA, "tmp")
PANEL = os.path.join(DATA, "panel")
START, END = "2012-01-01", "2026-10-19"
COLS = ["code", "date", "open", "high", "low", "close", "preclose", "volume",
        "amount", "turn", "pctchg", "isst", "tradestatus"]


def load_raw():
    parts = sorted(glob.glob(os.path.join(TMP, "tx_parts", "raw", "shard*.csv.gz")))
    print("raw parts:", len(parts), flush=True)
    frames = []
    for p in parts:
        frames.append(pd.read_csv(p, dtype={"code": str, "date": str}))
    df = pd.concat(frames, ignore_index=True)
    print("concat rows:", len(df), flush=True)
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["date"] >= START) & (df["date"] <= END)]
    df = df[(df["close"] > 0) & (df["open"] > 0)].copy()
    df.sort_values(["code", "date"], inplace=True, ignore_index=True)
    df["volume"] = df["volume"] * 100.0  # 手 -> shares
    return df


def load_factors():
    path = os.path.join(TMP, "factors", "factors_all.csv.gz")
    if not os.path.exists(path):
        print("WARN: no factors file", flush=True)
        return None
    # require (near-)complete factor coverage, otherwise qfq would be NaN for many codes
    done_path = os.path.join(TMP, "factors", "done_codes.txt")
    n_done = 0
    if os.path.exists(done_path):
        with open(done_path, "r", encoding="utf-8") as f:
            n_done = sum(1 for x in f if x.strip())
    codes_path = os.path.join(TMP, "codes_raw.csv")
    n_codes = 0
    with open(codes_path, "r", encoding="utf-8") as f:
        n_codes = sum(1 for _ in f) - 1
    if n_done < n_codes * 0.98:
        print("WARN: factors incomplete (%d/%d) -> skip qfq this run" % (n_done, n_codes), flush=True)
        return None
    fac = pd.read_csv(path, dtype={"code": str, "dividOperateDate": str})
    fac = fac.dropna(subset=["foreAdjustFactor", "backAdjustFactor"])
    print("factors rows:", len(fac), "codes:", fac["code"].nunique(), flush=True)
    return fac


def build_qfq(raw, fac):
    """Return qfq frame with OHLCX replaced by adjusted values; preclose adjusted consistently."""
    if fac is None:
        return None
    facs = fac.copy()
    facs["fore"] = pd.to_numeric(facs["foreAdjustFactor"], errors="coerce")
    facs["back"] = pd.to_numeric(facs["backAdjustFactor"], errors="coerce")
    facs = facs.sort_values(["code", "dividOperateDate"])
    # latest back factor per code -> factor for dates before first event = 1/back_latest
    latest = facs.groupby("code").agg(back_last=("back", "last"))
    latest["pre_fore"] = 1.0 / latest["back_last"]
    pre_map = latest["pre_fore"].to_dict()
    # event map: code -> (dates list, fore list)
    ev = {}
    for code, g in facs.groupby("code", sort=False):
        ev[code] = (g["dividOperateDate"].tolist(), g["fore"].tolist())
    codes = raw["code"].values
    dates = raw["date"].values
    f = np.empty(len(raw), dtype=np.float64)
    # group by code to vectorize with searchsorted
    raw_pos = pd.RangeIndex(len(raw))
    out_factor = np.ones(len(raw), dtype=np.float64)
    df_tmp = pd.DataFrame({"code": codes, "date": dates, "pos": np.arange(len(raw))})
    for code, g in df_tmp.groupby("code", sort=False):
        if code not in ev:
            out_factor[g["pos"].values] = np.nan
            continue
        dlist, flist = ev[code]
        arr_d = np.array(dlist)
        arr_f = np.array(flist)
        idx = np.searchsorted(arr_d, g["date"].values, side="right") - 1
        vals = np.where(idx >= 0, arr_f[np.clip(idx, 0, None)], pre_map.get(code, np.nan))
        out_factor[g["pos"].values] = vals
    q = raw.copy()
    for c in ["open", "high", "low", "close"]:
        q[c] = raw[c].values * out_factor
    q["preclose"] = q.groupby("code", sort=False)["close"].shift(1)
    q["pctchg"] = (q["close"] / q["preclose"] - 1.0) * 100.0
    q["amount"] = q["volume"] * ((q["high"] + q["low"] + q["close"]) / 3.0)
    q["turn"] = np.nan
    q["isst"] = 0
    q["tradestatus"] = 1
    n_missing = int(np.isnan(out_factor).sum())
    print("qfq: rows without factor:", n_missing, flush=True)
    return q, n_missing


def write_years(df, prefix):
    summary = {}
    for year, g in df.groupby("year", sort=True):
        out = os.path.join(PANEL, "%s_%s.csv.gz" % (prefix, year))
        g[COLS].to_csv(out, index=False, compression="gzip", float_format="%.6f")
        summary[year] = {"rows": int(len(g)), "codes": int(g["code"].nunique()),
                         "date_min": g["date"].min(), "date_max": g["date"].max(),
                         "file": "%s_%s.csv.gz" % (prefix, year), "bytes": os.path.getsize(out)}
        print("wrote", summary[year], flush=True)
    return summary


def main():
    raw = load_raw()
    # preclose/pctchg from raw close sequence; ex-div dates corrected below when factors present
    raw["preclose"] = raw.groupby("code", sort=False)["close"].shift(1)
    raw["pctchg"] = (raw["close"] / raw["preclose"] - 1.0) * 100.0
    # amount proxy before qfq derivation
    raw["amount"] = raw["volume"] * ((raw["high"] + raw["low"] + raw["close"]) / 3.0)
    raw["turn"] = np.nan
    raw["isst"] = 0
    raw["tradestatus"] = 1
    raw["year"] = raw["date"].str[:4]
    print("raw rows:", len(raw), "codes:", raw["code"].nunique(), flush=True)
    fac = load_factors()
    q = build_qfq(raw, fac)
    # fix raw preclose on ex-div dates if factors available, then recompute pctchg
    if fac is not None:
        raw = correct_exdiv_preclose(raw, fac)
        raw["pctchg"] = (raw["close"] / raw["preclose"] - 1.0) * 100.0
    sum_raw = write_years(raw, "bars")
    sum_qfq = write_years(q[0], "bars_qfq") if q else {}
    raw[COLS].to_csv(os.path.join(TMP, "panel_all_raw.csv.gz"), index=False, compression="gzip", float_format="%.6f")
    if q:
        q[0][COLS].to_csv(os.path.join(TMP, "panel_all_qfq.csv.gz"), index=False, compression="gzip", float_format="%.6f")
    summary = {"raw": sum_raw, "qfq": sum_qfq,
               "totals": {"raw_rows": int(len(raw)), "codes": int(raw["code"].nunique()),
                          "qfq_rows": int(len(q[0])) if q else 0},
               "start": START, "end": END}
    with open(os.path.join(TMP, "panel_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("DONE", json.dumps(summary["totals"], ensure_ascii=False), flush=True)


def correct_exdiv_preclose(raw, fac):
    """On ex-dividend dates, official preclose = prev_close * fore_prev / fore_event."""
    facs = fac.copy()
    facs["fore"] = pd.to_numeric(facs["foreAdjustFactor"], errors="coerce")
    facs = facs.dropna(subset=["fore"]).sort_values(["code", "dividOperateDate"])
    # event date -> fore factor
    evmap = {(r.code, r.dividOperateDate): r.fore for r in facs.itertuples()}
    raw = raw.sort_values(["code", "date"]).copy()
    raw["prev_close"] = raw.groupby("code", sort=False)["close"].shift(1)
    raw["prev_date"] = raw.groupby("code", sort=False)["date"].shift(1)
    # count corrections
    key = list(zip(raw["code"].values, raw["date"].values))
    mask = np.array([k in evmap for k in key])
    n_fix = 0
    if mask.any():
        idx = np.where(mask)[0]
        for i in idx:
            code = raw["code"].iat[i]
            d = raw["date"].iat[i]
            prev_d = raw["prev_date"].iat[i]
            if pd.isna(prev_d):
                continue
            try:
                f_next = evmap[(code, d)]
                # prev trading day factor: last event <= prev_d
                prev_events = [(dd, ff) for (cc, dd), ff in evmap.items() if cc == code and dd <= prev_d]
                f_prev = max(prev_events)[1] if prev_events else 1.0
                raw.iloc[i, raw.columns.get_loc("preclose")] = raw["prev_close"].iat[i] * f_prev / f_next
                n_fix += 1
            except Exception:  # noqa: BLE001
                continue
    print("exdiv preclose corrections:", n_fix, flush=True)
    return raw


if __name__ == "__main__":
    main()
