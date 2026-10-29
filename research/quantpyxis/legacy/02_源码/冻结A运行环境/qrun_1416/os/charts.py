# -*- coding: utf-8 -*-
"""Chart generation for the Bull/Bear OS report (Pillow only; no matplotlib).

Inputs:  os/results/*.json (os/run_v1.py), os/results/chart_data.json (os/analyze.py),
         valuation/outputs/market_valuation.csv, dcf_summary.csv
Outputs: os/out/figs/fig1_nav.png ... fig7_*.png
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
RES = REPO / "os" / "results"
FIG = REPO / "os" / "out" / "figs"
FIG.mkdir(parents=True, exist_ok=True)
FONT_DIR = Path("C:/Windows/Fonts")
FONT = str(FONT_DIR / "msyh.ttc")
FONT_B = str(FONT_DIR / "msyhbd.ttc")

# palette
COL = {
    "bullbear_v3": (31, 78, 160),
    "bullbear_v3m": (36, 120, 190),
    "v3_bull_only": (222, 125, 32),
    "bullbear_v1": (150, 120, 200),
    "bull_only": (222, 125, 32),
    "neutral_only": (120, 120, 120),
    "legacy_b_q": (140, 60, 160),
    "ew50": (60, 150, 90),
    "bh_csi300": (150, 150, 150),
    "bh_chinext": (200, 90, 90),
    "cash": (200, 200, 200),
}
LABELS = {
    "bullbear_v3": "牛熊双系统V3(季度)",
    "bullbear_v3m": "牛熊双系统V3(月度)",
    "v3_bull_only": "仅牛市OS-V3(95%)",
    "bullbear_v1": "牛熊双系统V1(失败)",
    "bull_only": "仅牛市OS(90%)",
    "neutral_only": "仅中性OS(40%)",
    "legacy_b_q": "旧季度动量+12%止损",
    "ew50": "等权50",
    "bh_csi300": "沪深300",
    "bh_chinext": "创业板指",
    "cash": "全现金",
}
REGIME_COL = {"BULL": (200, 235, 200), "NEUTRAL": (235, 235, 235),
              "BEAR": (245, 202, 202), "CRASH": (235, 160, 160)}
W, H = 1600, 900
ML, MR, MT, MB = 95, 45, 80, 95


def font(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def _nice_ticks(lo, hi, n=6):
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    else:
        step = 10 * mag
    tick = math.floor(lo / step) * step
    out = []
    while tick <= hi + 1e-9:
        out.append(tick)
        tick += step
    if len(out) < 2:
        out = [lo, hi]
    return out


def _canvas(title, subtitle=""):
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((ML, 22), title, font=font(30, True), fill=(20, 20, 20))
    if subtitle:
        d.text((ML, 58), subtitle, font=font(17), fill=(90, 90, 90))
    return img, d


def _axes(img, d, ymin, ymax, xticks, ytick_fmt="{:,.0f}", x_labels=None,
          grid_color=(225, 225, 225)):
    plot = (ML, MT, W - MR, H - MB)
    d.rectangle(plot, outline=(120, 120, 120), width=1)
    yt = _nice_ticks(ymin, ymax)
    for y in yt:
        if y < ymin - 1e-9 or y > ymax + 1e-9:
            continue
        py = plot[3] - (y - ymin) / (ymax - ymin) * (plot[3] - plot[1])
        d.line((ML, py, W - MR, py), fill=grid_color, width=1)
        d.text((12, py - 9), ytick_fmt.format(y), font=font(15), fill=(70, 70, 70))
    for (x, lab) in xticks:
        px = plot[0] + x * (plot[2] - plot[0])
        d.line((px, H - MB, px, H - MB + 6), fill=(120, 120, 120), width=1)
        if x_labels:
            d.text((px - 34, H - MB + 12), lab, font=font(15), fill=(70, 70, 70))
    return plot


def _xmap(dates, plot):
    n = len(dates)
    if n <= 1:
        return lambda i: plot[0]
    return lambda i: plot[0] + i / (n - 1) * (plot[2] - plot[0])


def _year_xticks(dates, plot, every=1):
    ticks = []
    for i, dt in enumerate(dates):
        y = dt[:4]
        if i == 0 or dates[i - 1][:4] != y:
            if int(y) % every == 0:
                ticks.append((i / (len(dates) - 1) if len(dates) > 1 else 0, y))
    return ticks


def _legend(d, items, x=ML, y=H - 42):
    cx = x
    for label, color in items:
        d.rectangle((cx, y + 4, cx + 22, y + 18), fill=color)
        d.text((cx + 28, y), label, font=font(16), fill=(40, 40, 40))
        cx += 34 + d.textlength(label, font=font(16))


# --------------------------------------------------------------------------- #
def chart_nav(payload):
    series = payload["series"]
    dates = payload["dates"]
    img, d = _canvas("图1 净值曲线：牛熊双系统V3 vs 单系统 vs 基准（2014-01-02—2026-10-19）",
                     "初始资金 100,000 元；对数思想：归一化为 1.0 起点；费用/滑点按统一口径")
    plot = (ML, MT, W - MR, H - MB)
    names = [n for n in ["bullbear_v3", "bullbear_v3m", "v3_bull_only", "legacy_b_q",
                         "bh_csi300", "bh_chinext", "cash"] if n in series]
    vals = {}
    for n in names:
        v = pd.Series(series[n]["nav"], index=series[n]["dates"]).astype(float)
        vals[n] = v / v.iloc[0]
    lo = min(float(s.min()) for s in vals.values())
    hi = max(float(s.max()) for s in vals.values())
    lo, hi = min(lo, 1.0), max(hi, 1.0)
    pad = (hi - lo) * 0.06
    xt = _year_xticks(dates, plot)
    plot = _axes(img, d, lo - pad, hi + pad, xt, "{:.1f}")
    xm = _xmap(dates, plot)
    for n in names:
        s = vals[n].to_numpy()
        pts = [(xm(i), plot[3] - (float(v) - (lo - pad)) / ((hi + pad) - (lo - pad)) * (plot[3] - plot[1]))
               for i, v in enumerate(s)]
        d.line(pts, fill=COL[n], width=3 if n == "bullbear_v3" else 2)
    _legend(d, [(LABELS[n], COL[n]) for n in names])
    d.text((ML, H - 12), "注：指数为价格指数（不含分红）；策略为前复权总收益近似口径；两行均为模拟结果。",
           font=font(14), fill=(120, 120, 120))
    img.save(FIG / "fig1_nav.png")
    print("wrote", FIG / "fig1_nav.png")


def chart_drawdown(payload):
    series = payload["series"]
    dates = payload["dates"]
    img, d = _canvas("图2 策略回撤：牛熊双系统V3 vs 月度版 vs 沪深300",
                     "回撤 = NAV / 历史最高NAV - 1；负值越深表示亏损越大")
    names = [n for n in ["bullbear_v3", "bullbear_v3m", "bh_csi300"] if n in series]
    dds = {}
    for n in names:
        v = pd.Series(series[n]["nav"], index=series[n]["dates"]).astype(float)
        dds[n] = v / v.cummax() - 1.0
    lo = min(float(s.min()) for s in dds.values())
    plot = (ML, MT, W - MR, H - MB)
    xt = _year_xticks(dates, plot)
    plot = _axes(img, d, lo, 0.01, xt, "{:.0%}")
    xm = _xmap(dates, plot)
    for n in names:
        s = dds[n].to_numpy()
        pts = [(xm(i), plot[3] - (float(v) - lo) / (0.01 - lo) * (plot[3] - plot[1]))
               for i, v in enumerate(s)]
        d.line(pts, fill=COL[n], width=2 if n != "bullbear_v3" else 3)
    _legend(d, [(LABELS[n], COL[n]) for n in names])
    img.save(FIG / "fig2_drawdown.png")
    print("wrote", FIG / "fig2_drawdown.png")


def chart_exposure_regime(payload):
    """Exposure line + regime background. Regime taken from bullbear_v1 result JSON."""
    p = RES / "bullbear_v3.json"
    if not p.exists():
        return
    nav = pd.DataFrame(json.loads(p.read_text(encoding="utf-8"))["nav"])
    nav["date"] = nav["date"].astype(str)
    nav = nav.set_index("date").sort_index()
    dates = list(nav.index)
    img, d = _canvas("图3 牛熊双系统V3：仓位与市场状态（Regime）",
                     "底色=状态机判定（绿=牛市/灰=中性/红=熊市），红线=股票仓位")
    plot = (ML, MT, W - MR, H - MB)
    # background regime bands
    reg = nav["regime"].fillna("unknown") if "regime" in nav.columns else None
    if reg is not None:
        cur = None
        start = 0
        for i in range(len(dates) + 1):
            r = reg.iloc[i] if i < len(reg) else None
            if cur is None:
                cur, start = r, i
                continue
            if r != cur:
                x0 = plot[0] + start / max(len(dates) - 1, 1) * (plot[2] - plot[0])
                x1 = plot[0] + i / max(len(dates) - 1, 1) * (plot[2] - plot[0])
                color = REGIME_COL.get(str(cur), (245, 245, 245))
                d.rectangle((x0, plot[1], max(x1, x0 + 1), plot[3]), fill=color)
                cur, start = r, i
    xt = _year_xticks(dates, plot)
    plot2 = _axes(img, d, 0.0, 1.0, xt, "{:.0%}")
    exp = nav["exposure"].astype(float).to_numpy()
    xm = _xmap(dates, plot2)
    pts = [(xm(i), plot2[3] - float(v) * (plot2[3] - plot2[1])) for i, v in enumerate(exp)]
    d.line(pts, fill=(180, 30, 30), width=3)
    _legend(d, [("股票仓位", (180, 30, 30)), ("牛市状态", REGIME_COL["BULL"]),
                ("中性", REGIME_COL["NEUTRAL"]), ("熊市", REGIME_COL["BEAR"])])
    img.save(FIG / "fig3_exposure_regime.png")
    print("wrote", FIG / "fig3_exposure_regime.png")


def chart_segments(payload):
    seg_path = RES / "tables" / "segments_long.csv"
    if not seg_path.exists():
        return
    seg = pd.read_csv(seg_path)
    names = [n for n in ["bullbear_v3", "bh_csi300", "bh_chinext", "v3_bull_only"]
             if n in set(seg["variant"])]
    segs = list(dict.fromkeys(seg["segment"]))
    img, d = _canvas("图4 分段收益：牛熊双系统V3 vs 沪深300 / 创业板指 / 仅牛市OS",
                     "每段按策略在该段的复利收益；含费用与滑点")
    plot = (ML, MT, W - MR, H - MB)
    vals = []
    for s in segs:
        for n in names:
            row = seg[(seg["segment"] == s) & (seg["variant"] == n)]
            if len(row):
                vals.append(float(row["return"].iloc[0]))
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    pad = (hi - lo) * 0.12
    xt = [(i / len(segs), s[:4] if i % 2 == 0 else "") for i, s in enumerate(segs)]
    plot = _axes(img, d, (lo - pad) * 100, (hi + pad) * 100, xt, "{:.0f}%")
    x0 = plot[0] + 20
    width = (plot[2] - plot[0] - 40) / len(segs)
    bw = width / (len(names) + 1.2)
    zero_y = plot[3] - (0 - (lo - pad)) / ((hi + pad) - (lo - pad)) * (plot[3] - plot[1])
    for i, s in enumerate(segs):
        for k, n in enumerate(names):
            row = seg[(seg["segment"] == s) & (seg["variant"] == n)]
            if not len(row):
                continue
            v = float(row["return"].iloc[0]) * 100
            x = x0 + i * width + (k + 0.6) * bw
            y = plot[3] - (v - (lo - pad) * 100) / (((hi + pad) - (lo - pad)) * 100) * (plot[3] - plot[1])
            top, bot = min(zero_y, y), max(zero_y, y)
            d.rectangle((x, top, x + bw * 0.85, bot), fill=COL[n])
        d.text((x0 + i * width + width / 2 - 40, H - MB + 12), s[:6], font=font(13), fill=(60, 60, 60))
    _legend(d, [(LABELS[n], COL[n]) for n in names])
    # value labels on top of bars for the main strategy
    for i, s in enumerate(segs):
        row = seg[(seg["segment"] == s) & (seg["variant"] == "bullbear_v1")]
        if len(row):
            v = float(row["return"].iloc[0]) * 100
            x = x0 + i * width + 0.6 * bw
            y = plot[3] - (v - (lo - pad) * 100) / (((hi + pad) - (lo - pad)) * 100) * (plot[3] - plot[1])
            d.text((x + bw * 0.85 / 2 - 22, y - 20 if v >= 0 else y + 4),
                   f"{v:+.0f}%", font=font(13, True), fill=(20, 60, 130))
    img.save(FIG / "fig4_segments.png")
    print("wrote", FIG / "fig4_segments.png")


def chart_yearly(payload):
    p = RES / "tables" / "yearly.csv"
    if not p.exists():
        return
    y = pd.read_csv(p, index_col=0)
    names = [n for n in ["bullbear_v3", "bullbear_v3m", "v3_bull_only", "bh_csi300"] if n in y.columns]
    img, d = _canvas("图5 逐年收益：牛熊双系统V3 vs 对照组",
                     "2026 为 2026-01-02—2026-10-19 的不完整年份")
    plot = (ML, MT, W - MR, H - MB)
    years = list(y.index)
    vals = []
    for yr in years:
        for n in names:
            if n in y.columns and pd.notna(y.loc[yr, n]):
                vals.append(float(y.loc[yr, n]))
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    pad = (hi - lo) * 0.15
    xt = [(i / max(len(years) - 1, 1), str(yr)) for i, yr in enumerate(years)]
    plot = _axes(img, d, (lo - pad) * 100, (hi + pad) * 100, xt, "{:.0f}%")
    width = (plot[2] - plot[0] - 40) / len(years)
    bw = width / (len(names) + 1.2)
    zero_y = plot[3] - (0 - (lo - pad)) / ((hi + pad) - (lo - pad)) * (plot[3] - plot[1])
    for i, yr in enumerate(years):
        for k, n in enumerate(names):
            if n not in y.columns or pd.isna(y.loc[yr, n]):
                continue
            v = float(y.loc[yr, n]) * 100
            x = plot[0] + 20 + i * width + (k + 0.6) * bw
            yy = plot[3] - (v - (lo - pad) * 100) / (((hi + pad) - (lo - pad)) * 100) * (plot[3] - plot[1])
            d.rectangle((x, min(zero_y, yy), x + bw * 0.85, max(zero_y, yy)), fill=COL[n])
    _legend(d, [(LABELS[n], COL[n]) for n in names])
    img.save(FIG / "fig5_yearly.png")
    print("wrote", FIG / "fig5_yearly.png")


def chart_monthly_heatmap(payload):
    p = RES / "tables" / "monthly_matrix_bullbear_v3.csv"
    if not p.exists():
        return
    mm = pd.read_csv(p, index_col=0)
    months = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    cols = [c for c in months if c in mm.columns]
    img, d = _canvas("图6 牛熊双系统V3：月度收益热力图",
                     "单位为 %；绿色为正收益、红色为负收益，颜色深浅按绝对值")
    x0, y0 = 170, MT + 20
    cw, ch = (W - MR - x0) / max(len(cols), 1), (H - MB - y0) / max(len(mm), 1)
    for j, m in enumerate(cols):
        d.text((x0 + j * cw + cw / 2 - 12, y0 - 30), m + "月", font=font(16), fill=(40, 40, 40))
    mx = max(0.01, float(mm[cols].abs().max().max()))
    for i, (yr, row) in enumerate(mm.iterrows()):
        d.text((x0 - 90, y0 + i * ch + ch / 2 - 11), str(yr), font=font(16), fill=(40, 40, 40))
        for j, m in enumerate(cols):
            v = row.get(m)
            if pd.isna(v):
                continue
            v = float(v)
            inten = min(1.0, abs(v) / mx)
            if v >= 0:
                color = (255 - int(150 * inten), 255 - int(60 * inten), 255 - int(150 * inten))
            else:
                color = (255, 255 - int(150 * inten), 255 - int(150 * inten))
            d.rectangle((x0 + j * cw, y0 + i * ch, x0 + (j + 1) * cw - 2, y0 + (i + 1) * ch - 2), fill=color)
            d.text((x0 + j * cw + cw / 2 - 28, y0 + i * ch + ch / 2 - 10),
                   f"{v * 100:+.1f}", font=font(13), fill=(20, 20, 20))
    img.save(FIG / "fig6_monthly_heatmap.png")
    print("wrote", FIG / "fig6_monthly_heatmap.png")


def chart_valuation():
    p = REPO / "valuation" / "outputs" / "market_valuation.csv"
    if not p.exists():
        return
    mv = pd.read_csv(p)
    img, d = _canvas("图7 市场估值周期：沪深300 PE-TTM 与隐含风险溢价（2014-2026，季末）",
                     "数据：westock 历史 quote 的指数级 PE/PB（估值口径）；分位为样本内 5 年滚动分位")
    plot = (ML, MT, W - MR, H - MB)
    dates = list(mv["asof_date"].astype(str))
    pe = mv["csi300_pe_ttm"].astype(float)
    ymin, ymax = 0, max(30.0, float(pe.max()) * 1.15)
    xt = _year_xticks(dates, plot)
    plot = _axes(img, d, ymin, ymax, xt, "{:.0f}")
    xm = _xmap(dates, plot)
    pts = [(xm(i), plot[3] - (v - ymin) / (ymax - ymin) * (plot[3] - plot[1]))
           for i, v in enumerate(pe)]
    d.line(pts, fill=COL["bullbear_v1"], width=3)
    for i, v in enumerate(pe):
        d.ellipse((xm(i) - 3, plot[3] - (v - ymin) / (ymax - ymin) * (plot[3] - plot[1]) - 3,
                   xm(i) + 3, plot[3] - (v - ymin) / (ymax - ymin) * (plot[3] - plot[1]) + 3),
                  fill=COL["bullbear_v1"])
    # ERP on secondary scale (right axis) as bars
    erp = mv["csi300_implied_erp"].astype(float) * 100
    elo, ehi = min(0.0, float(erp.min())), max(1.0, float(erp.max()))
    for i, v in enumerate(erp):
        h = (v - elo) / (ehi - elo) * (plot[3] - plot[1]) * 0.35
        d.rectangle((xm(i) - 4, plot[3] - h, xm(i) + 4, plot[3]), fill=(255, 190, 120))
    d.text((plot[2] - 330, MT + 8), f"蓝线：沪深300 PE-TTM（左轴 0—{ymax:.0f}x）",
           font=font(15), fill=(40, 40, 40))
    d.text((plot[2] - 330, MT + 30), "橙色柱：隐含ERP%（右轴方向，越高越便宜）",
           font=font(15), fill=(180, 110, 30))
    # annotate extremes
    if len(mv):
        imax = int(pe.idxmax())
        imin = int(pe.idxmin())
        for i, txt, dy in ((imax, f"PE峰值 {pe.iloc[imax]:.1f}x", -28), (imin, f"PE谷值 {pe.iloc[imin]:.1f}x", 14)):
            x, y = xm(i), plot[3] - (pe.iloc[i] - ymin) / (ymax - ymin) * (plot[3] - plot[1])
            d.text((x - 60, y + dy), txt, font=font(15, True), fill=(180, 30, 30))
    img.save(FIG / "fig7_valuation.png")
    print("wrote", FIG / "fig7_valuation.png")


def chart_dcf():
    p = REPO / "valuation" / "outputs" / "dcf_summary.csv"
    if not p.exists():
        return
    df = pd.read_csv(p)
    df = df[df["model"].astype(str).str.startswith("fcff")]
    if df.empty:
        return
    img, d = _canvas("图8 DCF 三情景价值 vs 市场价格（FCFF 两阶段）",
                     "每只股票两组：左=2015-12-31 估值日，右=2026-10-19；圆点=市场价格")
    plot = (ML, MT, W - MR, H - MB)
    df["key"] = df["name"].astype(str) + "\n" + df["valuation_date"].astype(str)
    keys = list(dict.fromkeys(df["key"]))
    keys = keys[:12]
    lo = 0.0
    hi = float(df[["bear_fvps", "base_fvps", "bull_fvps", "price"]].max().max()) * 1.1
    xt = [(i / max(len(keys) - 1, 1), "") for i in range(len(keys))]
    plot = _axes(img, d, lo, hi, xt, "{:.0f}")
    cw = (plot[2] - plot[0]) / max(len(keys), 1)
    for i, k in enumerate(keys):
        row = df[df["key"] == k].iloc[0]
        xc = plot[0] + (i + 0.5) * cw
        for j, fld, col in ((0, "bear_fvps", (230, 110, 110)), (1, "base_fvps", (60, 130, 200)),
                            (2, "bull_fvps", (70, 160, 90))):
            v = float(row[fld])
            y = plot[3] - v / hi * (plot[3] - plot[1])
            d.rectangle((xc + (j - 1.5) * 12, y, xc + (j - 1.5) * 12 + 10, plot[3]), fill=col)
        yp = plot[3] - float(row["price"]) / hi * (plot[3] - plot[1])
        d.ellipse((xc - 5, yp - 5, xc + 5, yp + 5), fill=(20, 20, 20))
        label = str(row["name"])
        d.text((xc - 34, H - MB + 12), label[:4], font=font(13), fill=(40, 40, 40))
    _legend(d, [("熊市情景", (230, 110, 110)), ("基准情景", (60, 130, 200)),
                ("牛市情景", (70, 160, 90)), ("市场价格", (20, 20, 20))])
    img.save(FIG / "fig8_dcf.png")
    print("wrote", FIG / "fig8_dcf.png")


def main():
    p = RES / "chart_data.json"
    if not p.exists():
        raise SystemExit("run os/analyze.py first")
    payload = json.loads(p.read_text(encoding="utf-8"))
    chart_nav(payload)
    chart_drawdown(payload)
    chart_exposure_regime(payload)
    chart_segments(payload)
    chart_yearly(payload)
    chart_monthly_heatmap(payload)
    chart_valuation()
    chart_dcf()
    print("all charts written to", FIG)


if __name__ == "__main__":
    main()
