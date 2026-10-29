# -*- coding: utf-8 -*-
"""Classify qfq/raw ratio jumps that do NOT fall on a baostock event date.

Hypothesis (verified on samples): these are ex-rights events whose official ex-date fell while the
stock was suspended; the observable price gap and the qfq factor jump both occur on the first
trading day after suspension. Such jumps are ECONOMICALLY CORRECT for return computation.
Classification per unmatched jump:
  A. raw overnight close-to-close return < -(limit+2%) (or > +limit+2% with limit under unknown ST):
     an ex-rights price gap is directly observable on that date -> corporate action confirmed
  B. baostock has an event within +-5 calendar days -> date-alignment difference
  C. neither -> genuine unexplained defect candidate
Writes data/qa/qfq_jump_classification.json and updates data/qa/panel_validation.json.
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
DATA = os.path.join(ROOT, "data")
QA = os.path.join(DATA, "qa")
PANEL = os.path.join(DATA, "panel")
TMP = os.path.join(DATA, "tmp")
START, END = "2012-01-01", "2026-10-19"


def canon(s):
    import re
    return re.sub(r"^(sh|sz)\.?", "", str(s))


def main():
    fac = pd.read_csv(os.path.join(TMP, "factors", "factors_all.csv.gz"),
                      dtype={"code": str, "dividOperateDate": str})
    fac["code"] = fac["code"].map(canon)
    fac = fac.dropna(subset=["foreAdjustFactor"])
    ev = {c: sorted(g["dividOperateDate"]) for c, g in fac.groupby("code", sort=False)}
    ev_arr = {c: np.array([pd.Timestamp(d).toordinal() for d in ds]) for c, ds in ev.items()}
    ev_set = {c: set(ds) for c, ds in ev.items()}
    prev_tail = None  # per-code last (date, close_raw, close_qfq) from previous year
    rows = []
    for year in range(2012, 2027):
        rp = os.path.join(PANEL, "bars_%d.csv.gz" % year)
        qp = os.path.join(PANEL, "bars_qfq_%d.csv.gz" % year)
        if not os.path.exists(rp) or not os.path.exists(qp):
            continue
        r = pd.read_csv(rp, dtype={"code": str, "date": str}, usecols=["code", "date", "close"])
        q = pd.read_csv(qp, dtype={"code": str, "date": str}, usecols=["code", "date", "close"])
        m = r.rename(columns={"close": "rc"}).merge(q.rename(columns={"close": "qc"}),
                                                   on=["code", "date"], how="inner")
        if prev_tail is not None:
            m = pd.concat([prev_tail, m], ignore_index=True)
        m = m.sort_values(["code", "date"], ignore_index=True)
        m["ratio"] = m["qc"] / m["rc"]
        m["rret"] = m.groupby("code", sort=False)["rc"].pct_change()
        m["rchg"] = (m.groupby("code", sort=False)["ratio"].pct_change()).abs()
        # jumps in years >= next year's start handled naturally by concat tail
        this_year = m[m["date"].str[:4].astype(int) == year]
        jumps = this_year[this_year["rchg"] > 1e-3]
        for row in jumps.itertuples():
            ds = ev_set.get(row.code)
            on_event = bool(ds and row.date in ds)
            if on_event:
                continue
            # raw gap classification
            is_cy = row.code.startswith(("30", "301"))
            lim = 0.21 if (is_cy and row.date >= "2020-08-24") else 0.11
            rret = row.rret if pd.notna(row.rret) else np.nan
            gap = False
            if pd.notna(rret):
                if rret < -(lim + 0.02) or rret > (lim + 0.02):
                    gap = True
            # nearest event distance
            arr = ev_arr.get(row.code)
            near5 = False
            near120 = False
            if arr is not None and len(arr):
                o = pd.Timestamp(row.date).toordinal()
                k = int(np.searchsorted(arr, o))
                for idx in (k - 1, k):
                    if 0 <= idx < len(arr):
                        d = abs(int(arr[idx]) - o)
                        if d <= 5:
                            near5 = True
                        if d <= 120:
                            near120 = True
            rows.append({"code": row.code, "date": row.date, "ratio_chg": float(row.rchg),
                         "raw_ret": float(rret) if pd.notna(rret) else None,
                         "raw_gap_confirmed": bool(gap),
                         "nearest_event_within5d": near5, "nearest_event_within120d": near120,
                         "limit": lim})
        # tail for next year
        last = m.groupby("code", sort=False).tail(1)[["code", "date", "rc", "qc"]]
        prev_tail = last
        print(year, "jumps-unmatched", len(rows), flush=True)
    df = pd.DataFrame(rows)
    out = {"n_unmatched_jumps": int(len(df)),
           "raw_gap_confirmed": int(df["raw_gap_confirmed"].sum()) if len(df) else 0,
           "near_event_5d": int(df["nearest_event_within5d"].sum()) if len(df) else 0,
           "near_event_120d": int(df["nearest_event_within120d"].sum()) if len(df) else 0,
           "explained_by_raw_gap_or_near_event": int(((df["raw_gap_confirmed"]) | (df["nearest_event_within5d"])).sum()) if len(df) else 0,
           "unexplained": int((~(df["raw_gap_confirmed"] | df["nearest_event_within5d"])).sum()) if len(df) else 0}
    if len(df):
        unexplained = df[~(df["raw_gap_confirmed"] | df["nearest_event_within5d"])]
        out["unexplained_samples"] = unexplained.head(20).to_dict(orient="records")
        out["unexplained_near120d"] = int(unexplained["nearest_event_within120d"].sum())
        out["unexplained_n_gt0.01"] = int((unexplained["ratio_chg"] > 0.01).sum())
        df.to_csv(os.path.join(QA, "qfq_unmatched_jumps.csv"), index=False)
    json.dump(out, open(os.path.join(QA, "qfq_jump_classification.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=float)
    # merge into panel_validation.json
    vp = os.path.join(QA, "panel_validation.json")
    if os.path.exists(vp):
        v = json.load(open(vp, encoding="utf-8"))
        qs = v.get("qfq_structural", {})
        qs["unmatched_jump_classification"] = out
        v["qfq_structural"] = qs
        json.dump(v, open(vp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("CLASSIFICATION", json.dumps(out, ensure_ascii=False, default=float)[:2500], flush=True)


if __name__ == "__main__":
    main()
