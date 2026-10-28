# -*- coding: utf-8 -*-
"""Consolidated round-4 tables + figure for the final candidate set (2014-07..2026-10)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
CAP = 100_000.0
FINAL = [
    ("V_base", "S0/R2 rules (round-3 baseline)"),
    ("b100_n30_top15", "frozen state, B100/N30, top15"),
    ("n90_top20_novolcap", "B100/N90, top20, no vol cap"),
    ("n90_top15_novolcap", "B100/N90, top15, no vol cap"),
    ("REF_none_100", "always 100% (no state)"),
    ("bh_csi300", "CSI300 buy&hold"),
    ("bh_chinext", "ChiNext buy&hold"),
]
SEGS = [
    ("bull_2014_2015", "2014-07-01", "2015-06-12"),
    ("crash_2015_2016", "2015-06-15", "2016-01-28"),
    ("bear_2018", "2018-01-02", "2018-12-31"),
    ("bull_2019_2021", "2019-01-02", "2021-02-10"),
    ("bear_2021_2024", "2021-02-18", "2024-09-13"),
    ("bull_2024_now", "2024-09-18", "2026-10-19"),
]
LATEST = ("2024-09-18", "2026-10-19")
COLORS = {"V_base": (31, 119, 180), "b100_n30_top15": (44, 160, 44),
          "n90_top20_novolcap": (148, 103, 189), "n90_top15_novolcap": (214, 39, 40),
          "REF_none_100": (140, 86, 75), "bh_csi300": (127, 127, 127),
          "bh_chinext": (255, 165, 0)}


def load_nav(tag):
    p = OUT / f"full_{tag}_nav.csv"
    if not p.exists():
        return None
    nav = pd.read_csv(p, index_col=0)
    nav.index = nav.index.astype(str)
    return nav


def metrics(n: pd.Series) -> dict:
    dr = n.pct_change().fillna(0.0)
    dd = n / n.cummax() - 1
    return dict(total_return=float(n.iloc[-1] / CAP - 1),
                max_drawdown=float(dd.min()), dd_trough=str(dd.idxmin()),
                cagr=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1),
                ann_vol=float(dr.std() * np.sqrt(252)),
                sharpe=float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else None,
                days=int(len(n)))


def seg(n, s, e):
    sub = n.loc[(n.index >= s) & (n.index <= e)]
    if len(sub) < 2:
        return {}
    return dict(ret=float(sub.iloc[-1] / sub.iloc[0] - 1),
                dd=float((sub / sub.cummax() - 1).min()), days=int(len(sub)))


def yearly(n):
    d = pd.DataFrame({"nav": n})
    d["prev"] = d["nav"].shift(1)
    d.iloc[0, d.columns.get_loc("prev")] = CAP
    d["year"] = [x[:4] for x in d.index]
    return {y: float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1) for y, g in d.groupby("year")}


def font(sz):
    try:
        return ImageFont.load_default(size=sz)
    except TypeError:
        return ImageFont.load_default()


def main():
    rows, seg_rows, year_rows = [], [], []
    navs = {}
    for tag, label in FINAL:
        nav = load_nav(tag)
        if nav is None:
            print("MISSING", tag)
            continue
        n = nav["nav"].astype(float)
        navs[tag] = n
        m = metrics(n)
        m.update(tag=tag, label=label)
        if "exposure" in nav:
            m["avg_exposure"] = float(nav["exposure"].astype(float).mean())
            m["all_cash_days"] = int((nav["market_value"].astype(float) <= .01).sum())
        lm = seg(n, *LATEST)
        m["latest_ret"] = lm.get("ret")
        m["latest_dd"] = lm.get("dd")
        rows.append(m)
        for name, s, e in SEGS:
            st = seg(n, s, e)
            if st:
                seg_rows.append(dict(tag=tag, segment=name, **st))
        for y, r in yearly(n).items():
            year_rows.append(dict(tag=tag, year=y, ret=r))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "ROUND4_FINAL_TABLE.csv", index=False)
    pd.DataFrame(seg_rows).to_csv(OUT / "ROUND4_SEGMENTS.csv", index=False)
    pd.DataFrame(year_rows).to_csv(OUT / "ROUND4_YEARLY.csv", index=False)
    print(df[["tag", "total_return", "max_drawdown", "sharpe", "latest_ret", "latest_dd"]]
          .to_string(index=False))

    # ---------------- figure ----------------
    W, H = 1700, 1180
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    tf, lf, tk = font(30), font(17), font(14)
    L, T, R, B = 90, 85, W - 360, 560

    def px(i, n):
        return L + (R - L) * i / (n - 1)

    def py(v, vmin, vmax):
        return B - (B - T) * (v - vmin) / (vmax - vmin)

    dr.text((L, 28), "Round 4: full window 2014-07-01..2026-10-19, NAV vs benchmarks (100k CNY)",
            fill=(20, 20, 20), font=tf)
    # use one common index (all navs share dates)
    base = navs["V_base"]
    idx = list(base.index)
    nlen = len(idx)
    norm = {tag: navs[tag] / CAP for tag in navs}
    allv = np.concatenate([norm[t].to_numpy() for t in navs])
    vmin, vmax = float(np.nanmin(allv)), float(np.nanmax(allv))
    pad = (vmax - vmin) * .06
    vmin, vmax = vmin - pad, vmax + pad
    for k in range(6):
        v = vmin + (vmax - vmin) * k / 5
        y = py(v, vmin, vmax)
        dr.line([(L, y), (R, y)], fill=(228, 228, 228))
        dr.text((L - 62, y - 8), f"{v:.1f}x", fill=(80, 80, 80), font=tk)
    seen = set()
    for i, d in enumerate(idx):
        if d[:4] not in seen and (i == 0 or idx[i - 1][:4] != d[:4]):
            seen.add(d[:4])
            x = px(i, nlen)
            dr.line([(x, T), (x, B)], fill=(240, 240, 240))
            dr.text((x - 14, B + 8), d[:4], fill=(80, 80, 80), font=tk)
    # OOS line 2024-09-18
    if "2024-09-18" in idx:
        i = idx.index("2024-09-18")
        x = px(i, nlen)
        dr.line([(x, T), (x, B)], fill=(200, 60, 60))
        dr.text((x + 6, T + 4), "2024-09-18  (OOS start)", fill=(200, 60, 60), font=lf)
    order = ["bh_csi300", "bh_chinext", "REF_none_100", "V_base",
             "b100_n30_top15", "n90_top20_novolcap", "n90_top15_novolcap"]
    for tag in order:
        if tag not in norm:
            continue
        v = norm[tag].to_numpy()
        pts = [(px(i, nlen), py(v[i], vmin, vmax)) for i in range(nlen) if np.isfinite(v[i])]
        w = 3 if tag not in ("bh_csi300", "bh_chinext") else 2
        dr.line(pts, fill=COLORS[tag], width=w)
    lx, ly = R + 14, T
    for tag in order:
        if tag not in norm:
            continue
        dr.line([(lx, ly + 8), (lx + 30, ly + 8)], fill=COLORS[tag], width=4)
        lbl = dict(FINAL).get(tag, tag)
        dr.text((lx + 38, ly), lbl, fill=(30, 30, 30), font=lf)
        ly += 28
    ly += 10
    for tag in order:
        if tag not in norm:
            continue
        m = metrics(navs[tag])
        dr.text((lx, ly), f"{tag}: {m['total_return'] * 100:+.0f}%  DD {m['max_drawdown'] * 100:.0f}%",
                fill=COLORS[tag], font=lf)
        ly += 26

    # drawdown panel
    T2, B2 = 650, 1080
    dr.text((L, 600), "Underwater (drawdown from running peak) — strategies only", fill=(20, 20, 20), font=tf)
    dd_tags = ["V_base", "b100_n30_top15", "n90_top20_novolcap", "n90_top15_novolcap"]
    ddmin = min(float((navs[t] / navs[t].cummax() - 1).min()) for t in dd_tags if t in navs)
    ddmin = min(ddmin * 1.08, -.02)

    def dy(v):
        return T2 + (B2 - T2) * (v / ddmin)

    for k in range(5):
        v = ddmin * k / 4
        y = dy(v)
        dr.line([(L, y), (R, y)], fill=(228, 228, 228))
        dr.text((L - 62, y - 8), f"{v * 100:+.0f}%", fill=(80, 80, 80), font=tk)
    for tag in dd_tags:
        if tag not in navs:
            continue
        dd = (navs[tag] / navs[tag].cummax() - 1).to_numpy()
        pts = [(px(i, nlen), dy(dd[i])) for i in range(nlen) if np.isfinite(dd[i])]
        dr.line(pts, fill=COLORS[tag], width=2)
    img.save(OUT / "round4_results.png")
    print("wrote", OUT / "round4_results.png")


if __name__ == "__main__":
    main()
