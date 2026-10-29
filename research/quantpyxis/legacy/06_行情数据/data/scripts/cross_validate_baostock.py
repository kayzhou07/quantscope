# -*- coding: utf-8 -*-
"""Supplementary baostock cross-validation with a PROPER sample (>=30 stocks listed in 2015H1).

Reuses published panel (bars_2015.csv.gz) and a single serialized baostock session.
Updates data/qa/panel_validation.json -> cross_validation_baostock and writes CSV.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
sys.path.insert(0, os.path.join(ROOT, "libs"))
import baostock as bs  # noqa: E402

DATA = os.path.join(ROOT, "data")
QA = os.path.join(DATA, "qa")
PANEL = os.path.join(DATA, "panel")
VAL = os.path.join(QA, "panel_validation.json")
WS, WE = "2015-01-01", "2015-06-30"


def bs_code(c):
    return ("sh." if c.startswith(("60", "68")) else "sz.") + c


def login_retry(n=8):
    for k in range(n):
        lg = bs.login()
        if lg.error_code == "0":
            return True
        time.sleep(1 + 1.2 * k)
    return False


def bs_query(code, max_try=3):
    for k in range(max_try):
        rs = bs.query_history_k_data_plus(bs_code(code), "date,close,preclose,volume,amount,pctChg",
                                          start_date=WS, end_date=WE, frequency="d", adjustflag="3")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rs.error_code == "0":
            return rows
        bs.logout()
        login_retry(3)
        time.sleep(0.4)
    return None


def main():
    df = pd.read_csv(os.path.join(PANEL, "bars_2015.csv.gz"), dtype={"code": str, "date": str})
    w = df[(df["date"] >= WS) & (df["date"] <= WE)]
    cnt = w.groupby("code").size()
    eligible = sorted(cnt[cnt >= 100].index.tolist())
    print("eligible codes 2015H1:", len(eligible), flush=True)
    rng = np.random.default_rng(1416)
    sample = list(rng.choice(eligible, size=min(30, len(eligible)), replace=False))
    # add 3 delisted (status=0) codes that have 2015H1 data
    uni_p = os.path.join(DATA, "meta", "universe.csv")
    extra = []
    if os.path.exists(uni_p):
        u = pd.read_csv(uni_p, dtype=str)
        de = set(u[u["status"].astype(str) == "0"]["code"]) & set(eligible)
        extra = sorted(de)[:3]
        for c in extra:
            if c not in sample:
                sample.append(c)
    print("sample", len(sample), "including delisted", extra, flush=True)
    if not login_retry():
        print("LOGIN FAIL", flush=True)
        sys.exit(2)
    rows = []
    t0 = time.time()
    for i, code in enumerate(sample):
        br = bs_query(code)
        if br is None:
            print("fail", code, flush=True)
            continue
        bmap = {r[0]: r for r in br}
        p = w[w["code"] == code]
        for row in p.itertuples():
            b = bmap.get(row.date)
            if b is None:
                continue
            rows.append({"code": code, "date": row.date,
                         "close_panel": row.close, "close_bs": float(b[1]),
                         "preclose_panel": row.preclose, "preclose_bs": float(b[2]),
                         "volume_panel": row.volume, "volume_bs": float(b[3]),
                         "amount_panel": row.amount, "amount_bs": float(b[4]),
                         "pctchg_panel": row.pctchg, "pctchg_bs": float(b[5])})
    bs.logout()
    bdf = pd.DataFrame(rows)
    bdf.to_csv(os.path.join(QA, "cross_validation_vs_baostock.csv.gz"), index=False, compression="gzip")
    pr = (bdf["pctchg_panel"] / bdf["pctchg_bs"]).replace([np.inf, -np.inf], np.nan)
    pr = pr[np.isfinite(pr)]
    stats = {"attempted": True, "skipped": False, "n_sample": len(sample),
             "sample_delisted": extra,
             "rows": int(len(bdf)), "codes": int(bdf["code"].nunique()),
             "close_max_abs_diff": float((bdf["close_panel"] - bdf["close_bs"]).abs().max()),
             "close_p99_abs_diff": float((bdf["close_panel"] - bdf["close_bs"]).abs().quantile(0.99)),
             "close_n_gt_0.02": int(((bdf["close_panel"] - bdf["close_bs"]).abs() > 0.021).sum()),
             "preclose_max_abs_diff": float((bdf["preclose_panel"] - bdf["preclose_bs"]).abs().max()),
             "preclose_n_gt_0.02": int(((bdf["preclose_panel"] - bdf["preclose_bs"]).abs() > 0.021).sum()),
             "volume_ratio_median": float((bdf["volume_panel"] / bdf["volume_bs"]).median()),
             "amount_ratio_median": float((bdf["amount_panel"] / bdf["amount_bs"]).median()),
             "pctchg_ratio_median(panel/bs; ~0.01 => panel in fraction)": float(pr.median()),
             "elapsed_s": round(time.time() - t0, 1)}
    # worst rows
    bdf["close_diff"] = bdf["close_panel"] - bdf["close_bs"]
    stats["worst_sample"] = bdf.reindex(bdf["close_diff"].abs().sort_values(ascending=False).index)[
        ["code", "date", "close_panel", "close_bs"]].head(8).to_dict(orient="records")
    v = json.load(open(VAL, encoding="utf-8"))
    v["cross_validation_baostock"] = stats
    json.dump(v, open(VAL, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("BAOSTOCK CV", json.dumps(stats, ensure_ascii=False, default=float)[:1500], flush=True)


if __name__ == "__main__":
    main()
