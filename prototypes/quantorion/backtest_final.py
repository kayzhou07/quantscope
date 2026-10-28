"""
最终版 vs 原版: 仅加单票30%仓位限制
"""
import io, sys, time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import logging
logging.basicConfig(level=logging.WARNING)

from config import INITIAL_CAPITAL, LIMIT_UP_MIN_DAYS, DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient


def get_trading_days(start_ymd, end_ymd, client):
    start = datetime.strptime(start_ymd, "%Y%m%d")
    end = datetime.strptime(end_ymd, "%Y%m%d")
    df = client.pro.trade_cal(exchange="SSE", start_date=start_ymd, end_date=end_ymd)
    if df is None or df.empty:
        days = []; cur = start
        while cur <= end:
            if cur.weekday() < 5: days.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return days
    open_days = df[df["is_open"] == 1]["cal_date"].sort_values().tolist()
    return [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in open_days]


def group_by_week(trading_days):
    weeks, cur_wk, cur_wn = [], [], None
    for d in trading_days:
        dt = datetime.strptime(d, "%Y-%m-%d")
        wk = (dt.isocalendar()[0], dt.isocalendar()[1])
        if wk != cur_wn:
            if cur_wk: weeks.append(cur_wk)
            cur_wk, cur_wn = [d], wk
        else:
            cur_wk.append(d)
    if cur_wk: weeks.append(cur_wk)
    return weeks


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix): return pct
    return 0.10


def screen_stocks(client, date_fmt):
    """通用选股：缩量连板"""
    prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        return pd.DataFrame(), {}
    daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        return pd.DataFrame(), {}
    dates = sorted(daily_data.keys(), reverse=True)
    df_t = daily_data[dates[0]][["ts_code","close","pct_chg","vol"]].copy()
    df_t.columns = ["ts_code","close","pct_chg","vol"]
    df_y = daily_data[dates[1]][["ts_code","close","pct_chg","vol"]].copy()
    df_y.columns = ["ts_code","close_y","pct_chg_y","vol_y"]
    merged = df_t.merge(df_y, on="ts_code", how="inner")
    merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in {"00","60","30","68"})
    def is_limit(row, col="pct_chg"):
        return row[col] >= (get_limit_pct(row["ts_code"]) * 100 * 0.995)
    merged["lt"] = merged.apply(lambda r: is_limit(r,"pct_chg"), axis=1)
    merged["ly"] = merged.apply(lambda r: is_limit(r,"pct_chg_y"), axis=1)
    merged["vd"] = merged["vol"] < merged["vol_y"]
    hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]
    eod_data = client.get_daily_batch(date_fmt)
    eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"])) if not eod_data.empty else {}
    return hits, eod_prices


def run_original(week_days, client, initial_cash):
    """原版：无任何限制"""
    cash = float(initial_cash)
    records = []
    for td in week_days:
        dfmt = td.replace("-", "")
        if not client.is_trading_day(dfmt): continue
        hits, prices = screen_stocks(client, dfmt)
        if hits.empty:
            records.append({"d": td, "nav": cash, "n": 0, "pnl": 0})
            continue
        n = len(hits); per = cash / n; pnl = 0
        for _, r in hits.iterrows():
            buy = r["close"]; sell = prices.get(r["ts_code"], buy)
            sh = int(per / buy / 100) * 100
            if sh >= 100: pnl += sh * (sell - buy)
        cash += pnl
        records.append({"d": td, "nav": round(cash,2), "n": n, "pnl": round(pnl,2)})
    return records


def run_final(week_days, client, initial_cash):
    """最终版：单票时仅用30%资金"""
    cash = float(initial_cash)
    records = []
    for td in week_days:
        dfmt = td.replace("-", "")
        if not client.is_trading_day(dfmt): continue
        hits, prices = screen_stocks(client, dfmt)
        if hits.empty:
            records.append({"d": td, "nav": cash, "n": 0, "pnl": 0})
            continue
        n = len(hits)
        # 唯一改动
        trade_cash = cash * 0.3 if n == 1 else cash
        per = trade_cash / n; pnl = 0
        for _, r in hits.iterrows():
            buy = r["close"]; sell = prices.get(r["ts_code"], buy)
            sh = int(per / buy / 100) * 100
            if sh >= 100: pnl += sh * (sell - buy)
        cash += pnl
        records.append({"d": td, "nav": round(cash,2), "n": n, "pnl": round(pnl,2),
                        "flag": "30%" if n == 1 else "满仓"})
    return records


def backtest(start_ymd, end_ymd, fn, client):
    days = get_trading_days(start_ymd, end_ymd, client)
    weeks = group_by_week(days)
    capital = float(INITIAL_CAPITAL)
    summaries = []; all_flags = []
    for wi, wd in enumerate(weeks):
        if len(wd) < 2: continue
        start_cap = capital
        recs = fn(wd, client, capital)
        if recs:
            capital = recs[-1]["nav"]
            for r in recs:
                if r.get("n", 0) > 0:
                    all_flags.append((r["d"], r["n"], r.get("flag", "")))
        ret = (capital / start_cap - 1) * 100
        summaries.append({"w": wi+1, "start": wd[0], "end": wd[-1], "ret": ret})
    total_ret = (capital / INITIAL_CAPITAL - 1) * 100
    return {
        "weeks": summaries,
        "final": capital,
        "total": total_ret,
        "flags": all_flags,
    }


PERIODS = [
    ("20260511","20260529","时段1: 5/11-5/29"),
    ("20260601","20260619","时段2: 6/1-6/19"),
    ("20260622","20260710","时段3: 6/22-7/10"),
]

client = TushareClient()
print("=" * 60)
print("  最终版 vs 原版: 仅加 单票30%仓位 一条规则")
print("=" * 60)
print(f"  原版: 任意缩量 + 均分全仓")
print(f"  最终: 任意缩量 + 均分全仓 + 选到1只时只用30%资金")
print(f"  起始: {INITIAL_CAPITAL:,} CNY | 复利(周收益滚入)")
print()

all_o = []; all_f = []

for s, e, label in PERIODS:
    print("-" * 50)
    print(f"  {label}")
    ro = backtest(s, e, run_original, client)
    rf = backtest(s, e, run_final, client)
    all_o.append(ro); all_f.append(rf)

    for vn, r in [("原版", ro), ("最终", rf)]:
        ws = " -> ".join([f"{w['ret']:+.1f}%" for w in r["weeks"]])
        print(f"  {vn}: {INITIAL_CAPITAL:,} -> {r['final']:,.0f} | {r['total']:+.2f}% | 周: {ws}")

    # 单票触发记录
    singles = [(d, n, f) for d, n, f in rf["flags"] if n == 1]
    if singles:
        print(f"  触发单票保护的天数: {len(singles)}")
        for d, n, f in singles:
            print(f"    {d}: 仅{n}只票 -> 只用30%资金")
    else:
        print(f"  未触发单票保护")

    diff = rf["total"] - ro["total"]
    better = "最终" if diff > 0 else "原版"
    print(f"  >>> {better}领先 {abs(diff):.2f}%")

# 汇总
print(f"\n{'='*60}")
print(f"  三时段汇总")
print(f"{'='*60}")
print(f"  {'时段':<18s} {'原版':>9s} {'最终':>9s}  {'胜负':>6s}")
print(f"  {'-'*46}")
for i, (s,e,label) in enumerate(PERIODS):
    o = all_o[i]["total"]; f = all_f[i]["total"]
    w = "最终" if f > o else "原版"
    print(f"  {label.split(':')[0]:<18s} {o:>8.2f}% {f:>8.2f}%  {w:>6s}")

sum_o = sum(r["total"] for r in all_o)
sum_f = sum(r["total"] for r in all_f)
print(f"  {'-'*46}")
print(f"  {'合计':<18s} {sum_o:>8.2f}% {sum_f:>8.2f}%")
print(f"  {'平均':<18s} {sum_o/3:>8.2f}% {sum_f/3:>8.2f}%")

# 风险指标
ow = [w["ret"] for r in all_o for w in r["weeks"]]
fw = [w["ret"] for r in all_f for w in r["weeks"]]
print(f"\n  {'风险指标':<20s} {'原版':>10s} {'最终':>10s}")
print(f"  {'周收益标准差':<20s} {np.std(ow):>9.2f}% {np.std(fw):>9.2f}%")
print(f"  {'最大单周亏损':<20s} {min(ow):>9.2f}% {min(fw):>9.2f}%")
print(f"  {'总周胜率':<20s} {sum(1 for r in ow if r>0):>8}/{len(ow)} {sum(1 for r in fw if r>0):>8}/{len(fw)}")

# 触发次数
total_singles = sum(len([x for x in r["flags"] if x[1]==1]) for r in all_f)
print(f"\n  三时段触发单票保护: {total_singles} 次")
print(f"  每次触发时: 全仓变30%, 风险敞口降至原来的30%")

diff_total = sum_f - sum_o
if abs(diff_total) < 0.5:
    print(f"\n  >>> 结论: 两版收益几乎持平 ({diff_total:+.2f}%), 但最终版大幅降低了单票黑天鹅风险")
elif diff_total > 0:
    print(f"\n  >>> 结论: 最终版收益更高 +{diff_total:.2f}%, 且风险更低 — 双重胜出")
else:
    print(f"\n  >>> 结论: 最终版少赚 {abs(diff_total):.2f}%, 换来大幅降低的尾部风险")
