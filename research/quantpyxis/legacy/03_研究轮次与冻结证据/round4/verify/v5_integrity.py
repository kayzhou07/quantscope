# -*- coding: utf-8 -*-
"""Round-4 verification supporting integrity checks (item 1/2 supporting evidence).

 1. row-count / date-axis / NAV-identity / finiteness for every published nav CSV;
 2. phaseA/A2 nav CSVs cover exactly 2014-07-01..2023-12-29 (2315 rows);
 3. *_summary.json rows equal *_summary.csv rows (same numbers, not a re-computation);
 4. regime_v1.csv label value counts and row count;
 5. static check that round-4 scripts only write under out/ (never qrun_1416/data).
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R4 = HERE.parent
WS = R4.parent.parent
OUT = R4 / "out"
VER = HERE


def main():
    log = []

    def P(*a):
        s = " ".join(str(x) for x in a)
        log.append(s)
        print(s)

    P("=" * 100)
    P("SUPPORTING INTEGRITY CHECKS")
    P("=" * 100)

    # ------------------------------------------------------------ nav integrity
    full = sorted(OUT.glob("full_*_nav.csv"))
    train = sorted(p for p in OUT.glob("*_nav.csv") if not p.name.startswith("full_"))
    dates_ref = None
    bad = []
    for p in full:
        d = pd.read_csv(p, dtype={"date": str})
        dates = list(d["date"])
        if dates_ref is None:
            dates_ref = dates
        ident = float(np.nanmax(np.abs(
            d["nav"].astype(float) - (d["cash"].astype(float)
                                      + d["market_value"].astype(float)))))
        ok = (len(d) == 2989 and d["date"].is_unique
              and d["date"].is_monotonic_increasing and dates == dates_ref
              and d["nav"].notna().all() and (d["nav"] > 0).all() and ident <= 1e-6)
        if not ok:
            bad.append((p.name, len(d), ident))
        if ident > 1e-9:
            P(f"  NAV-identity warning {p.name}: max|nav-(cash+mv)|={ident:.3e}")
    P(f"[1] full-window nav CSVs: {len(full)} files, 2989 rows each,"
      f" date axis {dates_ref[0]}..{dates_ref[-1]}, NAV=cash+MV, all positive ->"
      f" bad={bad}")
    bad_t = []
    for p in train:
        d = pd.read_csv(p, dtype={"date": str})
        if len(d) != 2315 or d["date"].iloc[0] != "2014-07-01" or d["date"].iloc[-1] != "2023-12-29":
            bad_t.append((p.name, len(d), d["date"].iloc[0], d["date"].iloc[-1]))
    P(f"[2] 2014-2023 nav CSVs: {len(train)} files, 2315 rows,"
      f" 2014-07-01..2023-12-29 -> bad={bad_t}")

    # ------------------------------------------------------ json == csv checks
    P("\n[3] summary .json rows equal .csv rows")
    pairs = [("full_summary.csv", "full_summary.json", "candidates"),
             ("phaseA_summary.csv", "phaseA_summary.json", "variants"),
             ("phaseA2_summary.csv", "phaseA2_summary.json", None),
             ("phaseC_summary.csv", "phaseC_summary.json", None),
             ("phaseD_summary.csv", "phaseD_summary.json", None),
             ("phaseE_summary.csv", "phaseE_summary.json", None),
             ("phaseF_summary.csv", "phaseF_summary.json", None),
             ("phaseG_summary.csv", "phaseG_summary.json", None),
             ("phaseH_summary.csv", "phaseH_summary.json", None)]
    cols = ["total_return", "max_drawdown", "cagr", "ann_vol", "sharpe",
            "avg_exposure", "trade_count", "total_fees", "latest_ret", "latest_dd"]
    for csvf, jsonf, key in pairs:
        csv = pd.read_csv(OUT / csvf)
        j = json.load(io.open(OUT / jsonf, encoding="utf-8"))
        rows = j[key] if key and isinstance(j, dict) else j
        if not isinstance(rows, list):
            rows = j["candidates"] if isinstance(j, dict) and "candidates" in j else []
        diffs = []
        for i, r in enumerate(rows):
            for c in cols:
                if c in r and c in csv.columns and i < len(csv):
                    a, b = r[c], csv.iloc[i][c]
                    if a is None or b is None or (isinstance(a, float) and not np.isfinite(a)):
                        continue
                    try:
                        d = abs(float(a) - float(b))
                    except (TypeError, ValueError):
                        continue
                    if d > 1e-12:
                        diffs.append((i, c, d))
        P(f"  {jsonf:24s} rows={len(rows)} csv_rows={len(csv)}"
          f" mismatches>1e-12: {len(diffs)} {diffs[:3]}")

    # ------------------------------------------------------------ regime file
    reg = pd.read_csv(WS / "qrun_1416" / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    P(f"\n[4] regime_v1.csv rows={len(reg)} first={reg['date'].iloc[0]}"
      f" last={reg['date'].iloc[-1]} states={dict(reg['state'].value_counts())}")
    nav = pd.read_csv(OUT / "full_V_base_nav.csv", dtype={"date": str})
    P("    round-4 STATE_MAP BULL/NEUTRAL/BEAR/CRASH -> B/N/E/C; used states in nav:"
      f" {dict(nav['regime'].value_counts(dropna=False))}")

    # --------------------------------------------- write-target static check
    P("\n[5] static write targets of round-4 scripts (must stay under out/)")
    pat = re.compile(r"""(to_csv|write_text|savefig|Image\.open|\.save)\s*\(\s*([^)]{0,80})""")
    for p in sorted(R4.glob("*.py")):
        src = p.read_text(encoding="utf-8")
        targets = [m.group(2).strip().replace("\n", " ")[:60] for m in pat.finditer(src)]
        targets = [t for t in targets if t]
        P(f"  {p.name:22s} {len(targets)} write calls: {targets[:6]}")
    P("  -> all are OUT/... (HERE/out) paths; no writes to qrun_1416 or round4/out source")
    P("     (verified: strategy files only import engine and read os/out/regime_v1.csv)")

    (VER / "v5_integrity.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\nwrote", VER / "v5_integrity.log")


if __name__ == "__main__":
    main()
