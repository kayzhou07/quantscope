# -*- coding: utf-8 -*-
"""Diagnose anomalies found by panel_audit: extra panel date vs calendar, delisting tails."""
import os, json
import pandas as pd
import numpy as np

WS = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace"
Q = os.path.join(WS, "qrun_1416")
M = os.path.join(Q, "data", "meta")
P = os.path.join(Q, "data", "panel")
OUT = os.path.join(WS, r"winrate_backtest_20261020\round3\audit")
WSTART, WEND = "2014-07-01", "2023-12-29"

cal = pd.read_csv(os.path.join(M, "calendar.csv"), dtype=str)
uni = pd.read_csv(os.path.join(M, "universe.csv"), dtype=str)
cal["d"] = pd.to_datetime(cal["date"])
cal["wd"] = cal["d"].dt.dayofweek
open_days = set(cal.loc[cal["is_open"] == "1", "date"])

# qfq dates in window
dates = set()
rows_by_date = {}
for y in range(2013, 2024):
    df = pd.read_csv(os.path.join(P, f"bars_qfq_{y}.csv.gz"), dtype={"code": str},
                     usecols=["code", "date", "close"])
    df = df[(df["date"] >= WSTART) & (df["date"] <= WEND)]
    for d, n in df.groupby("date").size().items():
        dates.add(d)
        rows_by_date[d] = rows_by_date.get(d, 0) + int(n)
    del df

win_cal = set(d for d in open_days if WSTART <= d <= WEND)
print("panel dates in window:", len(dates), " calendar open in window:", len(win_cal))
extra = sorted(dates - win_cal)
missing = sorted(win_cal - dates)
print("PANEL DATES NOT IN CALENDAR OPEN:", extra)
for d in extra:
    wd = pd.Timestamp(d).dayofweek
    print("  ", d, "weekday", wd, "bars:", rows_by_date.get(d, 0),
          "cal row:", cal.loc[cal["date"] == d, "is_open"].tolist())
print("CALENDAR OPEN DATES WITH NO PANEL BARS:", missing)
for d in missing[:20]:
    print("  ", d, "weekday", pd.Timestamp(d).dayofweek)

# full panel date coverage vs calendar (all years) for context
all_panel_dates = set()
for y in range(2012, 2027):
    df = pd.read_csv(os.path.join(P, f"bars_qfq_{y}.csv.gz"), dtype={"code": str}, usecols=["date"])
    all_panel_dates |= set(df["date"].unique())
    del df
cal_open_all = set(cal.loc[cal["is_open"] == "1", "date"])
print("FULL: panel distinct dates", len(all_panel_dates), "cal open", len(cal_open_all))
print("FULL panel-not-cal:", sorted(all_panel_dates - cal_open_all)[:30])
print("FULL cal-not-panel count:", len(cal_open_all - all_panel_dates),
      "examples:", sorted(cal_open_all - all_panel_dates)[:15])

# delisting tails
uni["out"] = pd.to_datetime(uni["out_date"], errors="coerce")
out_codes = uni[uni["out"].notna()].copy()
out_in = out_codes[(out_codes["out"] >= WSTART) & (out_codes["out"] <= WEND)]
maxbar = {}
minbar = {}
for y in range(2013, 2024):
    df = pd.read_csv(os.path.join(P, f"bars_qfq_{y}.csv.gz"), dtype={"code": str},
                     usecols=["code", "date"])
    df = df[(df["date"] >= WSTART) & (df["date"] <= WEND)]
    g = df.groupby("code")["date"]
    for c, v in g.max().items():
        if c not in maxbar or v > maxbar[c]:
            maxbar[c] = v
    for c, v in g.min().items():
        if c not in minbar or v < minbar[c]:
            minbar[c] = v
    del df

rows = []
for _, r in out_in.iterrows():
    c = r["code"]
    ob = r["out"].strftime("%Y-%m-%d")
    lb = maxbar.get(c)
    if lb is None:
        rows.append((c, r["board"], ob, None, None, "no_bars_in_window"))
        continue
    gap_cal = len([d for d in win_cal if lb < d <= ob])
    rows.append((c, r["board"], ob, lb, gap_cal,
                 "equal" if lb == ob else ("before" if lb < ob else "after")))
summary = pd.DataFrame(rows, columns=["code", "board", "out_date", "last_bar", "calendar_days_missing",
                                      "relation"])
print("\nDELISTED-IN-WINDOW", len(out_in))
print(summary["relation"].value_counts().to_dict())
print(summary.groupby("relation")["calendar_days_missing"].describe())
print("\nafter-out examples:")
print(summary[summary["relation"] == "after"].to_string())
print("\nworst gaps (before):")
print(summary[summary["relation"] == "before"].sort_values("calendar_days_missing", ascending=False).head(15).to_string())
summary.to_csv(os.path.join(OUT, "diag_delisted_tails.csv"), index=False, encoding="utf-8")

# all delisted codes (any era): does panel stop at out_date?
rows2 = []
for _, r in out_codes.iterrows():
    c = r["code"]
    ob = r["out"].strftime("%Y-%m-%d")
    lb = maxbar.get(c)
    rows2.append((c, ob, lb, "in_window" if c in maxbar else "no_bars_in_window"))
df2 = pd.DataFrame(rows2, columns=["code", "out_date", "last_bar_window", "coverage"])

# how many out_dates fall inside the panel span but code has no bars at all in window
print("\nout-of-window delisted:", len(df2[df2["coverage"] == "no_bars_in_window"]))

# suspension rate cross-check: days with a bar vs not, for a sample of codes that are
# listed throughout: e.g. 600000
df = pd.read_csv(os.path.join(P, "bars_qfq_2014.csv.gz"), dtype={"code": str},
                 usecols=["code", "date"])
d600 = set(df.loc[df["code"] == "600000", "date"])
print("600000 bars in 2014:", len(d600),
      "missing open days 2014:", len([d for d in win_cal if d[:4] == "2014" and d not in d600]))
