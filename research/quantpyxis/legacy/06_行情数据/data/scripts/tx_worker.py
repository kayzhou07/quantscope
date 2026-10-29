# -*- coding: utf-8 -*-
"""Tencent kline worker v3: unadjusted daily bars 2012-01-01..2026-10-19, host rotation, count=2000.

Why v3: web.ifzq.gtimg.cn returns HTTP 501 after ~600 requests (rate block).
Working alternates verified: https://ifzq.gtimg.cn and https://proxy.finance.qq.com/ifzqgtimg .
count=2000 is accepted (max; 5000 errors), so 14 years (~3555 bars) needs only 2 pages/stock.

Resumable: skips codes already present in data/tmp/tx_parts/raw/shard*.csv.gz.
Usage: python tx_worker.py <shard_id> <n_shards> [n_threads]
Output: data/tmp/tx_parts/raw/shard{shard}_{seq:04d}.csv.gz (code,date,open,high,low,close,volume)
"""
import concurrent.futures as cf
import csv
import datetime as dt
import glob
import gzip
import json
import os
import random
import sys
import threading
import time
import urllib.request

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
DATA = os.path.join(ROOT, "data")
TMP = os.path.join(DATA, "tmp")
CODES_CSV = os.path.join(TMP, "codes_raw.csv")
PARTS = os.path.join(TMP, "tx_parts", "raw")
LOGS = os.path.join(TMP, "logs")
START = "2012-01-01"
END = "2026-10-19"
HOSTS = ["https://ifzq.gtimg.cn/appstock/app/fqkline/get",
         "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"]
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
     "Referer": "https://gu.qq.com/"}
_tl = threading.local()


def tx_sym(code):
    return code.replace(".", "")


def host_idx():
    if not hasattr(_tl, "hi"):
        _tl.hi = random.randrange(len(HOSTS))
    return _tl.hi


def fetch_window(sym, end_date, count=2000, fq="nofq"):
    hi = host_idx()
    url = ("%s?param=%s,day,%s,%s,%d,%s" % (HOSTS[hi], sym, START, end_date, count, fq))
    req = urllib.request.Request(url, headers=H)
    try:
        raw = urllib.request.urlopen(req, timeout=20).read()
    except Exception:
        # rotate host and retry once inline
        _tl.hi = (hi + 1) % len(HOSTS)
        url = ("%s?param=%s,day,%s,%s,%d,%s" % (HOSTS[_tl.hi], sym, START, end_date, count, fq))
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=20).read()
    j = json.loads(raw.decode("utf-8", "ignore"))
    a = (j.get("data") or {}).get(sym)
    if not isinstance(a, dict):
        return None
    for key in ("day", "qfqday", "hfqday"):
        v = a.get(key)
        if isinstance(v, list):
            return v
    return []


def fetch_stock(code, max_retry=4):
    sym = tx_sym(code)
    end = END
    out = []
    seen = set()
    t0 = time.time()
    for _page in range(12):
        rows = None
        err = None
        for attempt in range(max_retry):
            try:
                rows = fetch_window(sym, end)
                err = None
                break
            except Exception as e:  # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, str(e)[:90])
                _tl.hi = (host_idx() + 1) % len(HOSTS)
                time.sleep(0.5 * (attempt + 1))
        if rows is None:
            return out, "http_fail:" + str(err), round(time.time() - t0, 2)
        if not rows:
            break
        new = 0
        for r in rows:
            d = r[0]
            if d in seen:
                continue
            seen.add(d)
            out.append(r)
            new += 1
        if new == 0:
            break
        earliest = min(r[0] for r in rows)
        if earliest <= START:
            break
        end = (dt.date.fromisoformat(earliest) - dt.timedelta(days=1)).isoformat()
    return out, None, round(time.time() - t0, 2)


def read_codes(n_shards, shard):
    codes = []
    with open(CODES_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            codes.append(r["code"])
    return codes[shard::n_shards]


def done_codes():
    s = set()
    for p in glob.glob(os.path.join(PARTS, "shard*.csv.gz")):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                rd = csv.reader(f)
                next(rd, None)
                for row in rd:
                    if row:
                        s.add(row[0])
        except Exception:  # noqa: BLE001
            continue
    return s


def next_seq(shard):
    seqs = []
    for p in glob.glob(os.path.join(PARTS, "shard%02d_*.csv.gz" % shard)):
        try:
            seqs.append(int(os.path.basename(p).split("_")[1].split(".")[0]))
        except Exception:  # noqa: BLE001
            pass
    return max(seqs) + 1 if seqs else 0


def write_chunk(part_idx, shard, buf):
    os.makedirs(PARTS, exist_ok=True)
    path = os.path.join(PARTS, "shard%02d_%04d.csv.gz" % (shard, part_idx))
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "date", "open", "high", "low", "close", "volume"])
        w.writerows(buf)
    return path


def main():
    shard = int(sys.argv[1])
    n_shards = int(sys.argv[2])
    n_threads = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    os.makedirs(LOGS, exist_ok=True)
    codes_all = read_codes(n_shards, shard)
    done = done_codes()
    codes = [c for c in codes_all if c not in done]
    logf = open(os.path.join(LOGS, "tx_stocks_shard%02d_v3.jsonl" % shard), "a", encoding="utf-8")
    lock = threading.Lock()
    n_done = [0]
    n_rows = [0]
    n_fail = [0]
    t_start = time.time()
    buf = []
    part_idx = next_seq(shard)
    FLUSH_ROWS = 90000

    def work(code):
        rows, err, t = fetch_stock(code)
        res = []
        for r in rows:
            try:
                res.append([code, r[0], r[1], r[3], r[4], r[2], r[5]])
            except IndexError:
                continue
        return code, res, err, t

    print("[shard %d] codes=%d done_already=%d todo=%d part_idx=%d hosts=%s" %
          (shard, len(codes_all), len(codes_all) - len(codes), len(codes), part_idx,
           [h.split("/")[2] for h in HOSTS]), flush=True)
    with cf.ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = {ex.submit(work, c): c for c in codes}
        for fut in cf.as_completed(futs):
            code, res, err, t = fut.result()
            with lock:
                n_done[0] += 1
                n_rows[0] += len(res)
                if err or not res:
                    n_fail[0] += 1
                logf.write(json.dumps({"code": code, "rows": len(res), "t": t,
                                       "err": err or ("" if res else "no_data")},
                                      ensure_ascii=False) + "\n")
                buf.extend(res)
                if n_done[0] % 40 == 0:
                    logf.flush()
                    print("[shard %d] done %d todo rows=%d fail=%d elapsed=%.0fs" %
                          (shard, n_done[0], len(codes), n_rows[0], n_fail[0], time.time() - t_start),
                          flush=True)
                if len(buf) >= FLUSH_ROWS:
                    path = write_chunk(part_idx, shard, buf)
                    print("[shard %d] wrote %s rows=%d" % (shard, os.path.basename(path), len(buf)), flush=True)
                    part_idx += 1
                    buf = []
    if buf:
        path = write_chunk(part_idx, shard, buf)
        print("[shard %d] wrote final %s rows=%d" % (shard, os.path.basename(path), len(buf)), flush=True)
    logf.close()
    summary = {"shard": shard, "n_codes_total": len(codes_all), "n_skipped": len(codes_all) - len(codes),
               "n_done": n_done[0], "n_rows": n_rows[0], "n_fail": n_fail[0],
               "elapsed_s": round(time.time() - t_start, 1),
               "ts": dt.datetime.now().isoformat(timespec="seconds")}
    with open(os.path.join(LOGS, "tx_summary_shard%02d_v3.json" % shard), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
