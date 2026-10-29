# -*- coding: utf-8 -*-
"""Render the round-3 result figure (NAV vs benchmarks, drawdown) with Pillow."""
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "out"
CAP = 100_000.0
COLORS = {"S0": (31, 119, 180), "S1": (214, 39, 40),
          "bench_sh000300": (120, 120, 120), "bench_sz399006": (255, 165, 0)}


def load(tag):
    return pd.read_csv(OUT / f"{tag}_nav.csv", index_col=0)


def font(sz):
    try:
        return ImageFont.load_default(size=sz)
    except TypeError:
        return ImageFont.load_default()


def main():
    s0, s1 = load("S0"), load("S1")
    idx = pd.Index(s0.index)
    series = {
        "S0": s0["nav"] / CAP - 1.0,
        "S1": s1["nav"] / CAP - 1.0,
        "bench_sh000300": s0["bench_sh000300"] / CAP - 1.0,
        "bench_sz399006": s0["bench_sz399006"] / CAP - 1.0,
    }
    dd = {}
    for tag in ("S0", "S1"):
        nav = load(tag)["nav"]
        dd[tag] = nav / nav.cummax() - 1.0

    W, H = 1600, 1150
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    title_f, lab_f, tick_f = font(30), font(19), font(15)

    # ---------------- panel A: cumulative return ----------------
    L, T, R, B = 90, 90, W - 360, 520
    dr.text((L, 30), "Round-3 local backtest: cumulative total return (100k CNY, 2014-07-01..2023-12-29)",
            fill=(20, 20, 20), font=title_f)
    all_vals = np.concatenate([v.to_numpy(dtype=float) for v in series.values()])
    vmin, vmax = np.nanmin(all_vals), np.nanmax(all_vals)
    pad = (vmax - vmin) * 0.06
    vmin, vmax = vmin - pad, vmax + pad
    n = len(idx)

    def px(i):
        return L + (R - L) * i / (n - 1)

    def py(v):
        return B - (B - T) * (v - vmin) / (vmax - vmin)

    # grid + y labels
    for k in range(6):
        v = vmin + (vmax - vmin) * k / 5
        y = py(v)
        dr.line([(L, y), (R, y)], fill=(225, 225, 225))
        dr.text((L - 58, y - 8), f"{v * 100:+.0f}%", fill=(80, 80, 80), font=tick_f)
    # x ticks: year starts
    for i, d in enumerate(idx):
        if d.endswith("-01-") and d[5:7] == "01":
            pass
    seen = set()
    for i, d in enumerate(idx):
        y_ = d[:4]
        if y_ not in seen and (d[5:7] >= "01"):
            if i == 0 or idx[i - 1][:4] != y_:
                seen.add(y_)
                x = px(i)
                dr.line([(x, T), (x, B)], fill=(240, 240, 240))
                dr.text((x - 14, B + 8), y_, fill=(80, 80, 80), font=tick_f)
    dr.line([(L, B), (R, B)], fill=(60, 60, 60))
    dr.line([(L, T), (L, B)], fill=(60, 60, 60))
    names = {"S0": "S0 (R2 monthly baseline)",
             "S1": "S1 (affordability backfill)",
             "bench_sh000300": "CSI300 (price index)",
             "bench_sz399006": "ChiNext (price index)"}
    for k, s in series.items():
        v = s.to_numpy(dtype=float)
        pts = [(px(i), py(v[i])) for i in range(n) if np.isfinite(v[i])]
        dr.line(pts, fill=COLORS[k], width=3 if k in ("S0", "S1") else 2)
    # legend
    lx, ly = R + 16, T + 10
    for k in ("S0", "S1", "bench_sh000300", "bench_sz399006"):
        dr.line([(lx, ly + 8), (lx + 34, ly + 8)], fill=COLORS[k], width=4)
        dr.text((lx + 42, ly), names[k], fill=(30, 30, 30), font=lab_f)
        ly += 34
    ly += 8
    for tag in ("S0", "S1"):
        nav = load(tag)["nav"]
        ret = nav.iloc[-1] / CAP - 1
        mdd = (nav / nav.cummax() - 1).min()
        dr.text((lx, ly), f"{tag}: {ret * 100:+.1f}%  maxDD {mdd * 100:.1f}%",
                fill=COLORS[tag], font=lab_f)
        ly += 30

    # ---------------- panel B: drawdown (0 at top, deepest at bottom) ----------------
    T2, B2 = 640, 1060
    dr.text((L, 580), "Underwater (drawdown from running peak)", fill=(20, 20, 20), font=title_f)
    dmin = min(float(v.min()) for v in dd.values())
    dmin = min(dmin * 1.08, -0.02)

    def dy(v):
        return T2 + (B2 - T2) * (v / dmin)

    for k in range(5):
        v = dmin * k / 4
        y = dy(v)
        dr.line([(L, y), (R, y)], fill=(225, 225, 225))
        dr.text((L - 58, y - 8), f"{v * 100:+.0f}%", fill=(80, 80, 80), font=tick_f)
    seen2 = set()
    for i, d in enumerate(idx):
        if d[:4] not in seen2 and (i == 0 or idx[i - 1][:4] != d[:4]):
            seen2.add(d[:4])
            x = px(i)
            dr.line([(x, T2), (x, B2)], fill=(240, 240, 240))
            dr.text((x - 14, B2 + 8), d[:4], fill=(80, 80, 80), font=tick_f)
    for tag in ("S0", "S1"):
        v = dd[tag].to_numpy(dtype=float)
        pts = [(px(i), dy(v[i])) for i in range(n) if np.isfinite(v[i])]
        dr.line(pts, fill=COLORS[tag], width=3)
        i = int(np.nanargmin(v))
        dr.text((px(i) + 4, dy(v[i]) - 26),
                f"{tag} {v[i] * 100:.1f}%", fill=COLORS[tag], font=lab_f)
    dr.line([(L, B2), (R, B2)], fill=(60, 60, 60))
    dr.line([(L, T2), (L, B2)], fill=(60, 60, 60))
    dr.text((L, H - 40),
            "Local engine qrun_1416 + independent 2012-2026 panel. Research only; not the SuperMind platform run.",
            fill=(100, 100, 100), font=tick_f)
    img.save(OUT / "round3_results.png")
    print("wrote", OUT / "round3_results.png")

    # benchmark max drawdown in window
    for col in ("bench_sh000300", "bench_sz399006"):
        s = s0[col] / CAP
        print(col, "maxDD", round(float((s / s.cummax() - 1).min()), 4),
              "ret", round(float(s.iloc[-1] / s.iloc[0] - 1), 4))


if __name__ == "__main__":
    main()
