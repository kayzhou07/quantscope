# -*- coding: utf-8 -*-
"""Sample validation: (a) derived qfq vs baostock adjustflag=2; (b) panel raw vs baostock raw
(close/volume units); (c) ex-div preclose correction check.

Uses a SINGLE baostock session, sequential, SHORT date windows to respect one-session rule.
Writes data/qa/qfq_validation.csv, data/qa/cross_validation.csv + JSON stats.
Run AFTER factor fetch finishes.
"""
import csv
import gzip
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
sys.path.insert(0, os.path.join(ROOT, "libs"))
import baostock as bs  # noqa: E402

DATA = os.path.join(ROOT, "data")
TMP = os.path.join(DATA, "tmp")
QA = os.path.join(DATA, "qa")
N_SAMPLE = 30


def login_retry(max_try=8):
    for k in range(max_try):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
            print("login ec", lg.error_code, lg.error_msg, flush=True)
        except Exception as e:  # noqa: BLE001
            print("login exc", type(e).__name__, flush=True)
        time.sleep(1.0 + 1.5 * k)
    return False


def bs_query(code, fields, start, end, fq="3", max_try=4):
    for k in range(max_try):
        try:
            rs = bs.query_history_k_data_plus(code, fields, start_date=start, end_date=end,
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
        time.sleep(0.5)
    return None


def load_panel():
    path = os.path.join(TMP, "panel_all_raw.csv.gz")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, dtype={"code": str, "date": str})


def load_qfq():
    path = os.path.join(TMP, "panel_all_qfq.csv.gz")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, dtype={"code": str, "date": str})


def main():
    os.makedirs(QA, exist_ok=True)
    rawp = load_panel()
    qp = load_qfq()
    if rawp is None:
        print("no panel_all_raw.csv.gz; abort", flush=True)
        sys.exit(1)
    codes = sorted(rawp["code"].unique().tolist())
    random.seed(1416)
    sample = random.sample(codes, min(N_SAMPLE, len(codes)))
    # choose a code with recent delisting too if present
    delfile = os.path.join(TMP, "codes_raw.csv")
    delisted = []
    with open(delfile, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["status"] == "0":
                delisted.append(r["code"])
    extra = [c for c in delisted if c in set(codes)][:3]
    for c in extra:
        if c not in sample:
            sample.append(c)
    if not login_retry():
        print("LOGIN FAILED", flush=True)
        sys.exit(2)
    # window chosen to limit baostock cost (one-session rule): 2015 full year
    windows = [("2015-01-01", "2015-12-31")]
    qfq_sample = set(sample[:12]) | set(extra)
    cross_rows = []
    qfq_rows = []
    t0 = time.time()
    for i, code in enumerate(sample):
        sub = rawp[rawp["code"] == code]
        if sub.empty:
            continue
        for (ws, we) in windows:
            w = sub[(sub["date"] >= ws) & (sub["date"] <= we)]
            if w.empty:
                continue
            bsraw = bs_query(code, "date,open,high,low,close,preclose,volume,amount,turn,pctChg",
                             ws, we, fq="3")
            if bsraw is None:
                print("BS FAIL raw", code, ws, flush=True)
                continue
            bmap = {r[0]: r for r in bsraw}
            # cross validation on close, volume, preclose, pctchg
            pairs = []
            for _, row in w.iterrows():
                b = bmap.get(row["date"])
                if b is None:
                    continue
                pairs.append((row, b))
            for row, b in pairs:
                cross_rows.append({
                    "code": code, "date": row["date"], "window": ws[:4],
                    "panel_close": row["close"], "baostock_close": float(b[4]),
                    "close_diff": row["close"] - float(b[4]),
                    "panel_volume": row["volume"], "baostock_volume": float(b[6]),
                    "volume_ratio": row["volume"] / float(b[6]) if float(b[6]) else np.nan,
                    "panel_preclose": row["preclose"], "baostock_preclose": float(b[5]),
                    "preclose_diff": row["preclose"] - float(b[5]),
                    "panel_pctchg": row["pctchg"], "baostock_pctchg": float(b[9]),
                })
            # qfq check on a subset
            if qp is not None and code in qfq_sample:
                bs_qfq = bs_query(code, "date,open,high,low,close", ws, we, fq="2")
                if bs_qfq is not None:
                    qmap = {r[0]: r for r in bs_qfq}
                    qsub = qp[(qp["code"] == code) & (qp["date"] >= ws) & (qp["date"] <= we)]
                    for _, row in qsub.iterrows():
                        b = qmap.get(row["date"])
                        if b is None:
                            continue
                        qfq_rows.append({
                            "code": code, "date": row["date"],
                            "derived_close": row["close"], "baostock_qfq_close": float(b[4]),
                            "abs_err": abs(row["close"] - float(b[4])),
                            "rel_err": abs(row["close"] - float(b[4])) / float(b[4]) if float(b[4]) else np.nan,
                        })
        if (i + 1) % 5 == 0:
            print("validated", i + 1, "/", len(sample), "elapsed", round(time.time() - t0), flush=True)
    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass
    # summarize
    cdf = pd.DataFrame(cross_rows)
    qdf = pd.DataFrame(qfq_rows)
    os.makedirs(QA, exist_ok=True)
    stats = {"n_sample_codes": len(sample), "delisted_in_sample": extra}
    if not cdf.empty:
        cdf.to_csv(os.path.join(QA, "cross_validation_sample.csv.gz"), index=False, compression="gzip")
        stats["cross_validation"] = {
            "rows": int(len(cdf)), "codes": int(cdf["code"].nunique()),
            "max_abs_close_diff": float(cdf["close_diff"].abs().max()),
            "p99_abs_close_diff": float(cdf["close_diff"].abs().quantile(0.99)),
            "n_close_diff_gt_0.005": int((cdf["close_diff"].abs() > 0.005).sum()),
            "median_volume_ratio": float(cdf["volume_ratio"].median()),
            "p01_volume_ratio": float(cdf["volume_ratio"].quantile(0.01)),
            "p99_volume_ratio": float(cdf["volume_ratio"].quantile(0.99)),
            "max_abs_pctchg_diff": float((cdf["panel_pctchg"] - cdf["baostock_pctchg"]).abs().max()),
        }
    if not qdf.empty:
        qdf.to_csv(os.path.join(QA, "qfq_validation_sample.csv.gz"), index=False, compression="gzip")
        stats["qfq_validation"] = {
            "rows": int(len(qdf)), "codes": int(qdf["code"].nunique()),
            "max_abs_err": float(qdf["abs_err"].max()),
            "p99_abs_err": float(qdf["abs_err"].quantile(0.99)),
            "n_rel_err_gt_1e-4": int((qdf["rel_err"] > 1e-4).sum()),
            "n_rel_err_gt_1e-3": int((qdf["rel_err"] > 1e-3).sum()),
            "worst": qdf.sort_values("rel_err", ascending=False).head(10).to_dict(orient="records"),
        }
    with open(os.path.join(QA, "validation_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1, default=float)
    print("VALIDATION", json.dumps(stats, ensure_ascii=False, default=float)[:3000], flush=True)


if __name__ == "__main__":
    main()
