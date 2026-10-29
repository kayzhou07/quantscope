# -*- coding: utf-8 -*-
"""Single-session SERIAL baostock adjust-factor fetch (one process, one login).

Per Lead directive: baostock allows only ONE session per IP -> never parallelize.
query_adjust_factor is tiny (~0.05-0.15s/stock); 4941 stocks ~= 8-12 min serial.
Writes data/tmp/factors/factors_all.csv.gz incrementally (every 200 stocks) + done list for resume.
Usage: python fetch_factors_serial.py [n_codes_limit]
"""
import csv
import glob
import gzip
import json
import os
import sys
import time

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
sys.path.insert(0, os.path.join(ROOT, "libs"))
import socket  # noqa: E402
socket.setdefaulttimeout(25)  # baostock has no per-request timeout: avoid infinite hang on dead session
import baostock as bs  # noqa: E402

DATA = os.path.join(ROOT, "data")
TMP = os.path.join(DATA, "tmp")
CODES_CSV = os.path.join(TMP, "codes_raw.csv")
FACT_DIR = os.path.join(TMP, "factors")
OUT = os.path.join(FACT_DIR, "factors_all.csv.gz")
DONE = os.path.join(FACT_DIR, "done_codes.txt")
LOGS = os.path.join(TMP, "logs")
START, END = "1990-01-01", "2026-10-19"


def login_retry(max_try=8):
    for k in range(max_try):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
            print("login ec", lg.error_code, lg.error_msg, flush=True)
        except Exception as e:  # noqa: BLE001
            print("login exc", type(e).__name__, str(e)[:80], flush=True)
        time.sleep(1.0 + 1.5 * k)
    return False


def fetch_factors(code, max_try=5):
    last_err = None
    for k in range(max_try):
        try:
            rs = bs.query_adjust_factor(code=code, start_date=START, end_date=END)
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code == "0":
                return rows, None
            last_err = rs.error_code + ":" + str(rs.error_msg)
            if "未登录" in str(rs.error_msg) or rs.error_code == "10001001":
                try:
                    bs.logout()
                except Exception:  # noqa: BLE001
                    pass
                login_retry(4)
        except Exception as e:  # noqa: BLE001
            last_err = "%s:%s" % (type(e).__name__, str(e)[:80])
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass
            login_retry(4)
        time.sleep(0.3 + 0.4 * k)
    return None, last_err


def load_done():
    if os.path.exists(DONE):
        with open(DONE, "r", encoding="utf-8") as f:
            s = set(x.strip() for x in f if x.strip())
        # verify part files exist; done list assumes data written
        return s
    return set()


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    os.makedirs(FACT_DIR, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    codes = []
    with open(CODES_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            codes.append(r["code"])
    if limit:
        codes = codes[:limit]
    done = load_done()
    todo = [c for c in codes if c not in done]
    print("codes", len(codes), "done", len(done), "todo", len(todo), flush=True)
    if not todo:
        print("nothing to do", flush=True)
        return
    if not login_retry():
        print("LOGIN FAILED", flush=True)
        sys.exit(2)
    t0 = time.time()
    # resume: append to existing gz if present, else create
    has_out = os.path.exists(OUT) and len(done) > 0
    buf = []
    n_noevent = 0
    n_fail = 0
    fails = []
    logf = open(os.path.join(LOGS, "factor_serial.jsonl"), "a", encoding="utf-8")
    donef = open(DONE, "a", encoding="utf-8")

    def flush_buf():
        nonlocal buf
        if not buf:
            return
        mode = "at" if has_out_existing[0] else "wt"
        with gzip.open(OUT, mode, encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if mode == "wt":
                w.writerow(["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor",
                            "adjustFactor", "query_start", "query_end"])
            w.writerows(buf)
        has_out_existing[0] = True
        buf = []

    has_out_existing = [has_out]
    for i, code in enumerate(todo):
        rows, err = fetch_factors(code)
        if rows is None:
            n_fail += 1
            fails.append((code, err))
            logf.write(json.dumps({"code": code, "n": 0, "err": err}, ensure_ascii=False) + "\n")
            continue
        if not rows:
            n_noevent += 1
        for r in rows:
            buf.append([code, r[1], r[2], r[3], r[4], START, END])
        donef.write(code + "\n")
        logf.write(json.dumps({"code": code, "n": len(rows)}, ensure_ascii=False) + "\n")
        if (i + 1) % 100 == 0:
            flush_buf()
            logf.flush()
            donef.flush()
            el = time.time() - t0
            print("[factors] %d/%d noevent=%d fail=%d elapsed=%.0fs eta=%.0fs" %
                  (i + 1, len(todo), n_noevent, n_fail, el, el / (i + 1) * (len(todo) - i - 1)), flush=True)
    flush_buf()
    logf.flush()
    donef.flush()
    logf.close()
    donef.close()
    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass
    summary = {"n_codes": len(codes), "n_noevent": n_noevent, "n_fail": n_fail,
               "fails": fails[:30], "elapsed_s": round(time.time() - t0, 1),
               "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
    with open(os.path.join(LOGS, "factor_serial_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
