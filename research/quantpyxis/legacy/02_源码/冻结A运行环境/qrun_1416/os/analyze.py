# -*- coding: utf-8 -*-
"""Analyse the OS v1 backtest results: overall/segment/yearly/regime tables + chart data.

Reads os/results/*.json (written by os/run_v1.py), writes:
    os/results/tables/overall.csv
    os/results/tables/segments_long.csv
    os/results/tables/segments_wide.csv
    os/results/tables/yearly.csv
    os/results/tables/regime_stats.csv
    os/results/tables/nav_curves.csv
    os/results/tables/monthly_matrix_{variant}.csv
    os/results/REPORT_TABLES.md
    os/results/chart_data.json  (drawdown series, exposure, regime labels)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RES = REPO / "os" / "results"
TAB = RES / "tables"
TAB.mkdir(parents=True, exist_ok=True)

# Frozen segment definitions (os/RULES_V1.md §6) ---------------------------
SEGMENTS = [
    ("2014-01-02", "2015-06-12", "2014-2015牛市"),
    ("2015-06-15", "2016-01-28", "2015.6-2016.1股灾熊市"),
    ("2016-02-01", "2018-01-24", "2016-2017修复/慢牛"),
    ("2018-01-25", "2019-01-03", "2018熊市"),
    ("2019-01-04", "2021-02-18", "2019.1-2021.2牛市"),
    ("2021-02-19", "2022-12-30", "2021-2022震荡/结构市"),
    ("2023-01-03", "2024-02-05", "2023-2024.2熊市"),
    ("2024-02-06", "2024-09-23", "2024.2-2024.9弱修复"),
    ("2024-09-24", "2026-10-19", "2024.9-2026牛市"),
]

VARIANTS = ["bullbear_v3", "bullbear_v3m", "v3_bull_only", "bullbear_v2", "bullbear_v1", "legacy_b_q",
            "ew50", "bh_csi300", "bh_chinext", "cash"]
LABELS = {
    "bullbear_v3": "牛熊双系统V3(季度)",
    "bullbear_v3m": "牛熊双系统V3(月度)",
    "v3_bull_only": "仅牛市OS-V3(95%恒定)",
    "bullbear_v2": "牛熊双系统V2",
    "v2_bull_only": "仅牛市OS-V2(95%)",
    "v2_neutral_only": "仅中性OS-V2(30%)",
    "bullbear_v1": "牛熊双系统V1",
    "bull_only": "仅牛市OS(90%恒定)",
    "neutral_only": "仅中性OS(40%恒定)",
    "legacy_b_q": "旧版季度动量+12%止损",
    "ew50": "等权50(月度)",
    "bh_csi300": "沪深300买入持有",
    "bh_chinext": "创业板指买入持有",
    "cash": "全现金",
}


def load_results() -> dict:
    out = {}
    for name in VARIANTS:
        p = RES / f"{name}.json"
        if not p.exists():
            continue
        payload = json.loads(p.read_text(encoding="utf-8"))
        nav = pd.DataFrame(payload.get("nav") or [])
        if nav.empty:
            continue
        if "date" not in nav.columns:
            raise ValueError(f"{p.name}: nav has no date column")
        nav["date"] = nav["date"].astype(str)
        nav = nav.set_index("date").sort_index()
        trades = pd.DataFrame(payload.get("trades") or [])
        if not trades.empty and "date" in trades.columns:
            trades["date"] = trades["date"].astype(str)
        out[name] = {"nav": nav, "trades": trades, "summary": payload.get("summary", {}),
                     "meta": payload.get("meta", {})}
    return out


def _base_nav(nav: pd.Series, start: str) -> float:
    prev = nav[nav.index < start]
    if len(prev):
        return float(prev.iloc[-1])
    return float(nav.iloc[0])


def _segment_metrics(nav: pd.Series, start: str, end: str) -> dict:
    mask = (nav.index >= start) & (nav.index <= end)
    seg = nav.loc[mask]
    if len(seg) == 0:
        return {"ret": np.nan, "maxdd": np.nan, "days": 0}
    base = _base_nav(nav, start)
    vals = np.concatenate([[base], seg.to_numpy(dtype=float)])
    peak = np.maximum.accumulate(vals)
    dd = vals / peak - 1.0
    return {"ret": float(seg.iloc[-1] / base - 1.0), "maxdd": float(dd.min()),
            "days": int(len(seg))}


def overall_table(results: dict) -> pd.DataFrame:
    rows = []
    s0 = next(iter(results.values()))
    for name, r in results.items():
        s = r["summary"]
        row = {
            "variant": name,
            "label": LABELS.get(name, name),
            "start": s.get("start"), "end": s.get("end"),
            "final_nav": s.get("final_nav"),
            "total_return": s.get("total_return"),
            "cagr": s.get("cagr"),
            "ann_vol": s.get("ann_vol"),
            "sharpe": s.get("sharpe"),
            "sortino": s.get("sortino"),
            "max_drawdown": s.get("max_drawdown"),
            "maxdd_peak": s.get("maxdd_peak_date"),
            "maxdd_trough": s.get("maxdd_trough_date"),
            "avg_exposure": s.get("avg_exposure"),
            "all_cash_days": s.get("all_cash_days"),
            "trade_count": s.get("trade_count"),
            "trade_days": s.get("trade_days"),
            "total_fees": s.get("total_fees"),
            "turnover_annual": s.get("turnover_annual"),
            "fifo_realized_pnl": s.get("fifo_realized_pnl"),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def segment_table(results: dict) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        nav = r["nav"]["nav"].astype(float)
        exp = r["nav"].get("exposure")
        trades = r["trades"]
        for (s, e, lab) in SEGMENTS:
            m = _segment_metrics(nav, s, e)
            seg = nav.loc[(nav.index >= s) & (nav.index <= e)]
            exposure = float(np.nanmean(exp.loc[seg.index])) if exp is not None else np.nan
            cash_days = int((exp.loc[seg.index] <= 1e-9).sum()) if exp is not None else 0
            n_tr = 0
            fees = 0.0
            if len(trades) and "date" in trades.columns:
                tt = trades[(trades["date"] >= s) & (trades["date"] <= e)]
                n_tr = int(len(tt))
                if "fee_total" in tt.columns:
                    fees = float(pd.to_numeric(tt["fee_total"], errors="coerce").sum())
            rows.append({"variant": name, "segment": lab, "start": s, "end": e,
                         "return": m["ret"], "maxdd": m["maxdd"], "days": m["days"],
                         "avg_exposure": exposure, "cash_days": cash_days,
                         "trades": n_tr, "fees": fees})
    return pd.DataFrame(rows)


def yearly_table(results: dict) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        nav = r["nav"]["nav"].astype(float)
        df = pd.DataFrame({"nav": nav})
        df["year"] = df.index.str[:4]
        for year, g in df.groupby("year"):
            base_i = nav.index[nav.index < f"{year}-01-01"]
            base = float(nav.loc[base_i[-1]]) if len(base_i) else float(g["nav"].iloc[0])
            rows.append({"variant": name, "year": year,
                         "return": float(g["nav"].iloc[-1] / base - 1.0)})
    out = pd.DataFrame(rows).pivot(index="year", columns="variant", values="return")
    return out


def regime_stats(results: dict) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        nav_df = r["nav"]
        if "regime" not in nav_df.columns:
            continue
        nav = nav_df["nav"].astype(float)
        reg = nav_df["regime"].fillna("unknown")
        exp = nav_df.get("exposure")
        ret = nav.pct_change().fillna(0.0)
        # contiguous regime episodes -> episode returns (proper compounding)
        ep = (reg != reg.shift(1)).cumsum()
        for label in sorted(reg.unique()):
            labels = reg[reg == label].index
            if len(labels) == 0:
                continue
            idx = labels
            r_ = ret.loc[idx]
            ep_returns = []
            for eid, g in reg.groupby(ep):
                if g.iloc[0] != label:
                    continue
                ids = g.index
                sub = ret.loc[ids]
                if len(sub):
                    ep_returns.append(float((1.0 + sub).prod() - 1.0))
            rows.append({
                "variant": name, "regime": label, "days": int(len(r_)),
                "episodes": int(len(ep_returns)),
                "mean_daily_ret": float(r_.mean()), "ann_ret_proxy": float(r_.mean() * 252),
                "ann_vol": float(r_.std() * np.sqrt(252.0)),
                "mean_episode_return": float(np.mean(ep_returns)) if ep_returns else np.nan,
                "avg_exposure": float(np.nanmean(exp.loc[idx])) if exp is not None else np.nan,
            })
    return pd.DataFrame(rows)


def nav_curves(results: dict):
    frames = []
    for name, r in results.items():
        nav = r["nav"]["nav"].astype(float).rename(name)
        frames.append(nav)
    bench_cols = [c for c in next(iter(results.values()))["nav"].columns
                  if c.startswith("bench_")]
    base = next(iter(results.values()))["nav"]
    for c in bench_cols:
        frames.append(base[c].astype(float).rename(c))
    out = pd.concat(frames, axis=1).sort_index()
    return out


def drawdown_and_exposure(results: dict):
    chart = {}
    for name, r in results.items():
        nav = r["nav"]["nav"].astype(float)
        peak = nav.cummax()
        chart[name] = {"dates": list(nav.index),
                       "drawdown": list((nav / peak - 1.0).round(6)),
                       "nav": list(nav.round(4))}
        if "exposure" in r["nav"].columns:
            chart[name]["exposure"] = list(r["nav"]["exposure"].astype(float).round(4))
    return chart


def monthly_matrix(results: dict):
    out = {}
    for name, r in results.items():
        nav = r["nav"]["nav"].astype(float)
        df = pd.DataFrame({"nav": nav})
        df["year"] = df.index.str[:4]
        df["month"] = df.index.str[5:7]
        rows = {}
        for (y, m), g in df.groupby(["year", "month"]):
            prev = nav[nav.index < g.index[0]]
            base = float(prev.iloc[-1]) if len(prev) else float(g["nav"].iloc[0])
            rows.setdefault(y, {})[m] = float(g["nav"].iloc[-1] / base - 1.0)
        out[name] = pd.DataFrame(rows).T.sort_index()
    return out


def fmt_pct(x, digits=2):
    try:
        if x is None or not np.isfinite(x):
            return "—"
        return f"{x * 100:+.{digits}f}%"
    except Exception:  # noqa: BLE001
        return "—"


def write_markdown(results, overall, segments, yearly, regime_df):
    lines = []
    lines.append("# 牛熊双操作系统 — 结果表（自动生成）\n")
    lines.append(f"样本区间：{overall['start'].iloc[0]} .. {overall['end'].iloc[0]}，"
                 f"初始资金 100,000 元，费用/滑点按 SPEC 统一口径。\n")
    lines.append("## 1. 全期总表\n")
    cols = ["label", "total_return", "cagr", "ann_vol", "sharpe", "max_drawdown",
            "avg_exposure", "all_cash_days", "trade_count", "total_fees"]
    lines.append("| 变体 | 总收益 | CAGR | 年化波动 | Sharpe | 最大回撤 | 平均仓位 | 全现金日 | 成交笔数 | 费用 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in overall.iterrows():
        lines.append("| {} | {} | {} | {} | {:.2f} | {} | {} | {} | {} | {:,.0f} |".format(
            row["label"], fmt_pct(row["total_return"]), fmt_pct(row["cagr"]),
            fmt_pct(row["ann_vol"]), row["sharpe"] if np.isfinite(row["sharpe"]) else 0.0,
            fmt_pct(row["max_drawdown"]), fmt_pct(row["avg_exposure"], 1),
            int(row["all_cash_days"]), int(row["trade_count"]) if np.isfinite(row["trade_count"]) else 0,
            row["total_fees"] if np.isfinite(row["total_fees"]) else 0.0))
    lines.append("\n## 2. 分段表现（收益 / 段内最大回撤）\n")
    piv = segments.pivot(index="segment", columns="variant", values="return")
    piv = piv.reindex([s[2] for s in SEGMENTS])
    names = [n for n in VARIANTS if n in piv.columns]
    lines.append("| 阶段 | " + " | ".join(LABELS[n] for n in names) + " |")
    lines.append("|---|" + "---:|" * len(names))
    for seg, row in piv.iterrows():
        lines.append("| " + seg + " | " + " | ".join(fmt_pct(row.get(n)) for n in names) + " |")
    lines.append("\n**段内最大回撤**\n")
    pivdd = segments.pivot(index="segment", columns="variant", values="maxdd")
    pivdd = pivdd.reindex([s[2] for s in SEGMENTS])
    lines.append("| 阶段 | " + " | ".join(LABELS[n] for n in names) + " |")
    lines.append("|---|" + "---:|" * len(names))
    for seg, row in pivdd.iterrows():
        lines.append("| " + seg + " | " + " | ".join(fmt_pct(row.get(n)) for n in names) + " |")
    lines.append("\n## 3. 逐年收益\n")
    if yearly is not None and len(yearly):
        cols = [n for n in VARIANTS if n in yearly.columns]
        lines.append("| 年份 | " + " | ".join(LABELS[n] for n in cols) + " |")
        lines.append("|---|" + "---:|" * len(cols))
        for year, row in yearly.iterrows():
            lines.append(f"| {year} | " + " | ".join(fmt_pct(row.get(c)) for c in cols) + " |")
    lines.append("\n## 4. 分 regime 统计（双系统 V3）\n")
    if len(regime_df):
        sub = regime_df[regime_df["variant"] == "bullbear_v3"]
        lines.append("| regime | 天数 | 区间数 | 日均收益 | 年化收益(代理) | 年化波动 | 平均区间收益 | 平均仓位 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for _, row in sub.iterrows():
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row["regime"], int(row["days"]), int(row.get("episodes", 0)),
                fmt_pct(row["mean_daily_ret"], 3), fmt_pct(row["ann_ret_proxy"]),
                fmt_pct(row["ann_vol"]), fmt_pct(row.get("mean_episode_return")),
                fmt_pct(row["avg_exposure"], 1)))
    (RES / "REPORT_TABLES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", RES / "REPORT_TABLES.md", flush=True)


def main():
    results = load_results()
    if not results:
        raise SystemExit("no result JSONs found; run os/run_v1.py first")
    overall = overall_table(results)
    overall.to_csv(TAB / "overall.csv", index=False)
    segments = segment_table(results)
    segments.to_csv(TAB / "segments_long.csv", index=False)
    yearly = yearly_table(results)
    yearly.to_csv(TAB / "yearly.csv")
    regime_df = regime_stats(results)
    regime_df.to_csv(TAB / "regime_stats.csv", index=False)
    curves = nav_curves(results)
    curves.round(6).to_csv(TAB / "nav_curves.csv")
    for name, mm in monthly_matrix(results).items():
        mm.round(6).to_csv(TAB / f"monthly_matrix_{name}.csv")
    charts = drawdown_and_exposure(results)
    payload = {"segments": SEGMENTS, "variants": list(results), "labels": LABELS,
               "series": charts,
               "nav_curves": {c: list(curves[c].round(4)) for c in curves.columns},
               "dates": list(curves.index)}
    (RES / "chart_data.json").write_text(json.dumps(payload, ensure_ascii=False),
                                         encoding="utf-8")
    write_markdown(results, overall, segments, yearly, regime_df)
    print(overall.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
