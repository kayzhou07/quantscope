# -*- coding: utf-8 -*-
"""Baostock adjust-factor worker: fetch query_adjust_factor for one shard of codes.

Usage: python factor_worker.py <shard_id> <n_shards>
Writes data/tmp/factors/shard{i}.csv.gz (code,dividOperateDate,foreAdjustFactor,backAdjustFactor,adjustFactor)
Robust against baostock session kicks: checks error_code, re-login + retry.
"""
import csv
import datetime as dt
import gzip
import json
import os
import sys
import time

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
sys.path.insert(0, os.path.join(ROOT, "libs"))
import baostock as bs  # noqa: E402

DATA = os.path.join(ROOT, "data")
TMP = os.path.join(DATA, "tmp")
CODES_CSV = os.path.join(TMP, "codes_raw.csv")
FACT = os.path.join(TMP, "factors")
LOGS = os.path.join(TMP, "logs")
START, END = "1990-01-01", "2026-10-19"


def login_retry(max_try=6):
    for k in range(max_try):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5 * (k + 1))
    return False


def fetch_factors(code, max_try=6):
    for k in range(max_try):
        try:
            rs = bs.query_adjust_factor(code=code, start_date=START, end_date=END)
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code == "0":
                return rows, None
            err = rs.error_code + ":" + str(rs.error_msg)
        except Exception as e:  # noqa: BLE001
            err = "%s:%s" % (type(e).__name__, str(e)[:80])
        # re-login and retry
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass
        login_retry(2)
        time.sleep(0.5 * (k + 1))
    return None, err


def write_part(shard, idx, buf):
    os.makedirs(FACT, exist_ok=True)
    path = os.path.join(FACT, "shard%02d_%04d.csv.gz" % (shard, idx))
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor",
                    "adjustFactor", "query_start", "query_end"])
        w.writerows(buf)
    return path


def main():
    shard = int(sys.argv[1])
    n_shards = int(sys.argv[2])
    os.makedirs(LOGS, exist_ok=True)
    codes = []
    with open(CODES_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            codes.append(r["code"])
    codes = codes[shard::n_shards]
    if not login_retry():
        print("LOGIN FAILED", flush=True)
        sys.exit(2)
    t0 = time.time()
    buf = []
    part = 0
    n_noevent = 0
    n_fail = 0
    errs = []
    logf = open(os.path.join(LOGS, "factor_stocks_shard%02d.jsonl" % shard), "w", encoding="utf-8")
    for i, code in enumerate(codes):
        rows, err = fetch_factors(code)
        if rows is None:
            n_fail += 1
            errs.append((code, err))
            logf.write(json.dumps({"code": code, "n": 0, "err": err}, ensure_ascii=False) + "\n")
            continue
        if not rows:
            n_noevent += 1
        for r in rows:
            # r = [code, dividOperateDate, foreAdjustFactor, backAdjustFactor, adjustFactor]
            buf.append([code, r[1], r[2], r[3], r[4], START, END])
        logf.write(json.dumps({"code": code, "n": len(rows), "err": None}, ensure_ascii=False) + "\n")
        if (i + 1) % 25 == 0:
            logf.flush()
            print("[factor shard %d] %d/%d noevent=%d fail=%d elapsed=%.0fs" %
                  (shard, i + 1, len(codes), n_noevent, n_fail, time.time() - t0), flush=True)
        if (i + 1) % 300 == 0:
            path = write_part(shard, part, buf)
            print("[factor shard %d] wrote %s rows=%d" % (shard, os.path.basename(path), len(buf)), flush=True)
            part += 1
            buf = []
    if buf:
        path = write_part(shard, part, buf)
        print("[factor shard %d] wrote final %s rows=%d" % (shard, os.path.basename(path), len(buf)), flush=True)
    logf.flush()
    logf.close()
    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass
    summary = {"shard": shard, "n_codes": len(codes), "n_noevent": n_noevent, "n_fail": n_fail,
               "fails": errs[:20], "elapsed_s": round(time.time() - t0, 1),
               "ts": dt.datetime.now().isoformat(timespec="seconds")}
    with open(os.path.join(LOGS, "factor_summary_shard%02d.json" % shard), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
