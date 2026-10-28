# -*- coding: utf-8 -*-
"""Probe: file encodings/shapes/schema of qrun_1416 panel + meta (read-only)."""
import os
import pandas as pd

WS = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace"
D = os.path.join(WS, "qrun_1416", "data")
M = os.path.join(D, "meta")
P = os.path.join(D, "panel")

cal = pd.read_csv(os.path.join(M, "calendar.csv"), dtype=str)
print("calendar", cal.shape, cal.columns.tolist(), cal.tail(3).to_dict("records"))
print("calendar is_open counts", cal["is_open"].value_counts().to_dict())

uni = pd.read_csv(os.path.join(M, "universe.csv"), dtype=str)
print("universe", uni.shape, uni.columns.tolist())
print(uni.head(3).to_string())
print("board counts", uni["board"].value_counts().to_dict())
print("exchange counts", uni["exchange"].value_counts().to_dict())
print("status counts", uni["status"].value_counts().to_dict())
print("ipo_date nonnull", int(uni["ipo_date"].notna().sum()),
      "out_date nonnull", int(uni["out_date"].notna().sum()))
print("code len counts", uni["code"].str.len().value_counts().to_dict())

codes = pd.read_csv(os.path.join(M, "codes.csv"), dtype=str)
print("codes", codes.shape, "dup ids", int(codes["id"].duplicated().sum()),
      "id head", codes["id"].head(3).tolist())

for f in ["bars_2014.csv.gz", "bars_qfq_2014.csv.gz"]:
    fp = os.path.join(P, f)
    df = pd.read_csv(fp, dtype={"code": str}, nrows=5)
    print(f, "cols", df.columns.tolist())
    print(df.head(3).to_string())

qfq = pd.read_csv(os.path.join(P, "bars_qfq_2014.csv.gz"), dtype={"code": str},
                  usecols=["code", "date", "close"])
print("qfq2014 shape", qfq.shape, "date range", qfq["date"].min(), qfq["date"].max())
print("qfq2014 code sample", qfq["code"].head(5).tolist(), "n codes", qfq["code"].nunique())
print("dup (code,date)", int(qfq.duplicated(["code", "date"]).sum()))

raw = pd.read_csv(os.path.join(P, "bars_2014.csv.gz"), dtype={"code": str},
                  usecols=["code", "date", "close", "isst", "tradestatus", "turn", "amount"])
print("raw2014 shape", raw.shape)
for c in ["isst", "tradestatus"]:
    print(c, "value_counts", raw[c].value_counts(dropna=False).head(5).to_dict())
print("turn nonnull", int(raw["turn"].notna().sum()), "of", len(raw))
print("amount nonnull", int(raw["amount"].notna().sum()))
