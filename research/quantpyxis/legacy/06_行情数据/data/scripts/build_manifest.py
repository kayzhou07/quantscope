# -*- coding: utf-8 -*-
"""Build data/meta/manifest.json: sha256 + rows + date ranges + timestamps for all published files.

Includes Lead-published meta files (calendar/universe/codes/index_daily) and panel bars.
Run after panels + qa are final (data/scripts/panel_validation.py -> build_qa.py -> build_manifest.py).
"""
import glob
import hashlib
import json
import os

import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
DATA = os.path.join(ROOT, "data")
MANIFEST = os.path.join(DATA, "meta", "manifest.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def meta_info(path):
    df = pd.read_csv(path, dtype=str)
    info = {"rows": int(len(df)), "columns": list(df.columns)}
    if "date" in df.columns:
        info["date_min"] = str(df["date"].min())
        info["date_max"] = str(df["date"].max())
    if "code" in df.columns:
        info["codes"] = int(df["code"].nunique())
    return info


def bar_info(path, qfq=False):
    df = pd.read_csv(path, dtype={"code": str, "date": str})
    return {"rows": int(len(df)), "codes": int(df["code"].nunique()),
            "date_min": str(df["date"].min()), "date_max": str(df["date"].max()),
            "columns": list(df.columns)}


def main():
    files = []
    now = pd.Timestamp.now().isoformat(timespec="seconds")
    # ---- meta files (Lead-published)
    for name in ["calendar.csv", "universe.csv", "codes.csv"]:
        p = os.path.join(DATA, "meta", name)
        if not os.path.exists(p):
            continue
        try:
            info = meta_info(p)
        except Exception as e:  # noqa: BLE001
            info = {"error": str(e)}
        files.append({"path": "meta/" + name, "bytes": os.path.getsize(p),
                      "sha256": sha256(p), "publisher": "lead", **info,
                      "status": "ok" if info.get("rows", 0) > 0 else "stub"})
    # ---- fetch log (data-runner)
    fl = os.path.join(DATA, "meta", "fetch_log.jsonl")
    if os.path.exists(fl):
        with open(fl, "r", encoding="utf-8") as f:
            n = sum(1 for _ in f)
        files.append({"path": "meta/fetch_log.jsonl", "bytes": os.path.getsize(fl),
                      "rows": n, "sha256": sha256(fl), "publisher": "data-runner", "status": "ok"})
    # ---- index
    ip = os.path.join(DATA, "panel", "index_daily.csv.gz")
    if os.path.exists(ip):
        try:
            df = pd.read_csv(ip, dtype={"index_code": str, "date": str})
            info = {"rows": int(len(df)),
                    "index_codes": sorted(df["index_code"].unique().tolist()) if len(df) else [],
                    "date_min": str(df["date"].min()) if len(df) else None,
                    "date_max": str(df["date"].max()) if len(df) else None}
            st = "ok" if len(df) > 0 else "stub"
        except Exception as e:  # noqa: BLE001
            info = {"error": str(e)}
            st = "error"
        files.append({"path": "panel/index_daily.csv.gz", "bytes": os.path.getsize(ip),
                      "sha256": sha256(ip), "publisher": "lead", **info, "status": st})
    # ---- raw bars
    for p in sorted(glob.glob(os.path.join(DATA, "panel", "bars_20*.csv.gz"))):
        base = os.path.basename(p)
        entry = {"path": "panel/" + base, "bytes": os.path.getsize(p),
                 "sha256": sha256(p), "publisher": "lead",
                 "status": "ok" if os.path.getsize(p) > 300 else "stub"}
        try:
            entry.update(bar_info(p))
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)
            entry["status"] = "error"
        files.append(entry)
    # ---- qfq bars (5 price columns)
    for p in sorted(glob.glob(os.path.join(DATA, "panel", "bars_qfq_20*.csv.gz"))):
        base = os.path.basename(p)
        entry = {"path": "panel/" + base, "bytes": os.path.getsize(p),
                 "sha256": sha256(p), "publisher": "lead",
                 "status": "ok" if os.path.getsize(p) > 300 else "stub"}
        try:
            entry.update(bar_info(p))
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)
            entry["status"] = "error"
        files.append(entry)
    # ---- qa
    for p in sorted(glob.glob(os.path.join(DATA, "qa", "*"))):
        if os.path.isfile(p):
            files.append({"path": "qa/" + os.path.basename(p), "bytes": os.path.getsize(p),
                          "sha256": sha256(p), "publisher": "data-runner", "status": "ok"})
    # ---- scripts
    for p in sorted(glob.glob(os.path.join(DATA, "scripts", "*"))):
        if os.path.isfile(p):
            files.append({"path": "scripts/" + os.path.basename(p), "bytes": os.path.getsize(p),
                          "sha256": sha256(p), "publisher": "data-runner", "status": "ok"})
    manifest = {
        "dataset": "qrun_1416 A-share daily panel (main board + ChiNext)",
        "version": "v1_final",
        "generated_at": now,
        "date_range": ["2012-01-01", "2026-10-19"],
        "sources": {
            "raw_prices": "Sina Finance KLC full-history kline (klc_kl.js, official JS decode) - unadjusted OHLCV + real amount + reference preclose",
            "qfq_prices": "Sina qfq.js adjustment factors (Lead-built), forward-adjusted",
            "calendar_universe_index": "Lead-published from baostock meta (calendar/universe/codes/index_daily)",
            "adjust_factor_validation": "baostock 0.9.4 query_adjust_factor (single serialized session); used only for dividend-event QA",
        },
        "schema": {
            "bars_{year}.csv.gz": ["code", "date", "open", "high", "low", "close", "preclose",
                                   "volume", "amount", "turn", "pctchg", "isst", "tradestatus"],
            "bars_qfq_{year}.csv.gz": ["code", "date", "open", "close", "high", "low"],
            "index_daily.csv.gz": ["index_code", "date", "open", "high", "low", "close",
                                   "preclose", "volume", "amount"],
        },
        "units_and_notes": {
            "code": "6-digit without exchange prefix (600/601/603/605=SH; 000/001/002/003/300/301=SZ); exchange in meta/universe.csv",
            "price": "CNY; bars_* = raw unadjusted real prices; bars_qfq_* = forward-adjusted (5 price columns only)",
            "volume": "shares (股)",
            "amount": "real turnover value in CNY (amount_source=real_sina)",
            "amount_source": "real_sina",
            "turn": "unavailable (empty/NaN)",
            "isst": "unavailable -> constant 0 (NOT real ST flag; isst_source=unavailable_0)",
            "tradestatus": "1 for all present rows; suspension = row missing",
            "preclose": "preclose_source=mixed(reference/prev_close): Sina reference price on ex-div dates, previous available close carried otherwise; first row per code empty",
            "pctchg": "DECIMAL fraction (0.0152 = +1.52%), not percent",
            "qfq_note": "qfq file has price columns only; use raw file for volume/amount",
        },
        "counts": {
            "files": len(files),
            "total_bytes": sum(f.get("bytes", 0) for f in files),
        },
        "files": files,
    }
    vpath = os.path.join(DATA, "qa", "panel_validation.json")
    if os.path.exists(vpath):
        try:
            with open(vpath, "r", encoding="utf-8") as f:
                vv = json.load(f)
            manifest["validation_summary"] = {
                "panel_validation_json": "qa/panel_validation.json",
                "years_present": vv.get("years_present"),
                "years_missing": vv.get("years_missing"),
                "structural": vv.get("structural"),
                "completeness_ok": vv.get("completeness_ok"),
                "delisted_n": vv.get("delisted_n"),
                "delisted_with_data": vv.get("delisted_with_data"),
                "cross_validation_tencent": vv.get("cross_validation_tencent"),
                "cross_validation_baostock": vv.get("cross_validation_baostock"),
                "qfq_structural": vv.get("qfq_structural"),
            }
        except Exception as e:  # noqa: BLE001
            manifest["validation_summary"] = {"error": str(e)}
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("wrote", MANIFEST, "files:", len(files), "bytes:", manifest["counts"]["total_bytes"], flush=True)
    for e in files:
        print(" ", e["path"], e.get("status"), e.get("rows"), e.get("date_min"), e.get("date_max"), flush=True)


if __name__ == "__main__":
    main()
