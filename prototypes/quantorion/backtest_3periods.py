"""
三时段双版本回测对比：原版 vs 改进版
时段1: 2026-05-11 ~ 2026-05-29 (15交易日)
时段2: 2026-06-01 ~ 2026-06-19 (14交易日)
时段3: 2026-06-22 ~ 2026-07-10 (15交易日)
规则：上周收益滚入本周本金（复利），前日收盘买入 → 当日收盘卖出
"""
import io, sys, time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import logging
logging.basicConfig(level=logging.WARNING)

from config import INITIAL_CAPITAL, LIMIT_UP_MIN_DAYS, DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient


def get_trading_days_in_range(start_ymd, end_ymd, client):
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


def get_name_map(client):
    all_stocks = client.get_all_stocks()
    return dict(zip(all_stocks["ts_code"], all_stocks["name"])) if not all_stocks.empty else {}


# ═══════════════════════════════════════════════════════
# 原版
# ═══════════════════════════════════════════════════════
def run_original(week_days, client, initial_cash, name_map):
    cash = float(initial_cash)
    daily_records = []
    for target_date in week_days:
        date_fmt = target_date.replace("-", "")
        if not client.is_trading_day(date_fmt): continue
        prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
        if len(recent_days) < LIMIT_UP_MIN_DAYS + 1: continue
        daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
        if len(daily_data) < LIMIT_UP_MIN_DAYS + 1: continue
        dates = sorted(daily_data.keys(), reverse=True)
        df_t = daily_data[dates[0]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_t.columns = ["ts_code", "close", "pct_chg", "vol"]
        df_y = daily_data[dates[1]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_y.columns = ["ts_code", "close_y", "pct_chg_y", "vol_y"]
        merged = df_t.merge(df_y, on="ts_code", how="inner")
        merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in {"00", "60", "30", "68"})
        def is_limit(row, col="pct_chg"):
            return row[col] >= (get_limit_pct(row["ts_code"]) * 100 * 0.995)
        merged["lt"] = merged.apply(lambda r: is_limit(r, "pct_chg"), axis=1)
        merged["ly"] = merged.apply(lambda r: is_limit(r, "pct_chg_y"), axis=1)
        merged["vd"] = merged["vol"] < merged["vol_y"]
        hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]
        eod_data = client.get_daily_batch(date_fmt)
        eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"])) if not eod_data.empty else {}
        if hits.empty:
            daily_records.append({"date": target_date, "nav": cash, "picks": 0, "pnl": 0})
            continue
        n = len(hits); per_stock = cash / n; day_pnl = 0
        for _, row in hits.iterrows():
            code = row["ts_code"]; buy = row["close"]; sell = eod_prices.get(code, buy)
            shares = int(per_stock / buy / 100) * 100
            if shares < 100: continue
            day_pnl += shares * (sell - buy)
        cash += day_pnl
        daily_records.append({"date": target_date, "nav": round(cash, 2), "picks": n, "pnl": round(day_pnl, 2)})
    return daily_records


# ═══════════════════════════════════════════════════════
# 改进版
# ═══════════════════════════════════════════════════════
def run_improved(week_days, client, initial_cash, name_map):
    """改进: 缩量>20% + 单票<=25% + 市场环境仓位"""
    cash = float(initial_cash)
    daily_records = []
    for target_date in week_days:
        date_fmt = target_date.replace("-", "")
        if not client.is_trading_day(date_fmt): continue

        regime_data = client.get_market_regime(date_fmt)
        position_target = regime_data.get("position_target", 0.90)
        max_stocks = regime_data.get("max_stocks", 3)

        prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
        if len(recent_days) < LIMIT_UP_MIN_DAYS + 1: continue
        daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
        if len(daily_data) < LIMIT_UP_MIN_DAYS + 1: continue
        dates = sorted(daily_data.keys(), reverse=True)
        df_t = daily_data[dates[0]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_t.columns = ["ts_code", "close", "pct_chg", "vol"]
        df_y = daily_data[dates[1]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_y.columns = ["ts_code", "close_y", "pct_chg_y", "vol_y"]
        merged = df_t.merge(df_y, on="ts_code", how="inner")
        merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in {"00", "60", "30", "68"})
        def is_limit(row, col="pct_chg"):
            return row[col] >= (get_limit_pct(row["ts_code"]) * 100 * 0.995)
        merged["lt"] = merged.apply(lambda r: is_limit(r, "pct_chg"), axis=1)
        merged["ly"] = merged.apply(lambda r: is_limit(r, "pct_chg_y"), axis=1)
        merged["vd"] = merged["vol"] < merged["vol_y"] * 0.8  # 缩量>20%
        hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]

        eod_data = client.get_daily_batch(date_fmt)
        eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"])) if not eod_data.empty else {}

        if hits.empty:
            daily_records.append({"date": target_date, "nav": cash, "picks": 0, "pnl": 0})
            continue

        effective_cash = cash * position_target
        if len(hits) > max_stocks:
            hits = hits.head(max_stocks)
        n = len(hits)
        per_stock = min(effective_cash / n, cash * 0.25)  # 单票上限25%
        day_pnl = 0
        for _, row in hits.iterrows():
            code = row["ts_code"]; buy = row["close"]; sell = eod_prices.get(code, buy)
            shares = int(per_stock / buy / 100) * 100
            if shares < 100: continue
            day_pnl += shares * (sell - buy)
        cash += day_pnl
        daily_records.append({"date": target_date, "nav": round(cash, 2), "picks": n, "pnl": round(day_pnl, 2)})
    return daily_records


# ═══════════════════════════════════════════════════════
# 单时段回测
# ═══════════════════════════════════════════════════════
def backtest_period(start_ymd, end_ymd, strategy_fn, client, name_map):
    trading_days = get_trading_days_in_range(start_ymd, end_ymd, client)
    weeks = group_by_week(trading_days)
    capital = float(INITIAL_CAPITAL)
    week_summaries = []
    all_pnls = []
    for wi, wd in enumerate(weeks):
        if len(wd) < 2: continue
        start_cap = capital
        records = strategy_fn(wd, client, capital, name_map)
        if records:
            capital = records[-1]["nav"]
            for r in records:
                if r.get("picks", 0) > 0:
                    all_pnls.append(r["pnl"])
        ret = (capital / start_cap - 1) * 100
        week_summaries.append({"week": wi+1, "start": wd[0], "end": wd[-1], "ret": ret, "days": len(wd)})
    total_ret = (capital / INITIAL_CAPITAL - 1) * 100
    return {
        "weeks": week_summaries,
        "final_capital": capital,
        "total_return": total_ret,
        "avg_week": np.mean([w["ret"] for w in week_summaries]),
        "win_weeks": sum(1 for w in week_summaries if w["ret"] > 0),
        "total_weeks": len(week_summaries),
        "max_week": max(w["ret"] for w in week_summaries),
        "min_week": min(w["ret"] for w in week_summaries),
        "avg_daily_pnl": np.mean(all_pnls) if all_pnls else 0,
        "total_trades": len(all_pnls),
    }


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════
PERIODS = [
    ("20260511", "20260529", "时段1: 5/11 - 5/29"),
    ("20260601", "20260619", "时段2: 6/1  - 6/19"),
    ("20260622", "20260710", "时段3: 6/22 - 7/10"),
]

client = TushareClient()
name_map = get_name_map(client)

print(f"回测: 三个时段 × 两个版本")
print(f"起始资金: {INITIAL_CAPITAL:,} CNY | 复利(周收益滚入下周)")
print(f"原版: 缩量连板(任意缩量) + 均分全仓")
print(f"改进: 缩量>20% + 单票<=25% + 市场环境仓位")
print()

all_orig = []
all_impr = []

for s, e, label in PERIODS:
    print(f"{'='*65}")
    print(f"  {label}")
    print(f"{'='*65}")

    # 原版
    r_orig = backtest_period(s, e, run_original, client, name_map)
    all_orig.append(r_orig)
    print(f"  原版: {INITIAL_CAPITAL:,} -> {r_orig['final_capital']:,.0f} | "
          f"总收益 {r_orig['total_return']:+.2f}% | "
          f"周胜率 {r_orig['win_weeks']}/{r_orig['total_weeks']} | "
          f"周均 {r_orig['avg_week']:+.2f}%")
    for wk in r_orig["weeks"]:
        print(f"    W{wk['week']} [{wk['start']}~{wk['end']}]: {wk['ret']:+.2f}%")

    # 改进版
    r_impr = backtest_period(s, e, run_improved, client, name_map)
    all_impr.append(r_impr)
    print(f"  改进: {INITIAL_CAPITAL:,} -> {r_impr['final_capital']:,.0f} | "
          f"总收益 {r_impr['total_return']:+.2f}% | "
          f"周胜率 {r_impr['win_weeks']}/{r_impr['total_weeks']} | "
          f"周均 {r_impr['avg_week']:+.2f}%")
    for wk in r_impr["weeks"]:
        print(f"    W{wk['week']} [{wk['start']}~{wk['end']}]: {wk['ret']:+.2f}%")

    diff = r_impr["total_return"] - r_orig["total_return"]
    better = "改进" if diff > 0 else "原版"
    print(f"  >>> {label}: {better}领先 {abs(diff):.2f}%")

# ══ 总对比 ══
print(f"\n{'═'*65}")
print(f"  汇总对比")
print(f"{'═'*65}")
print(f"{'时段':<20s} {'原版收益':>10s} {'改进收益':>10s} {'优胜':>6s}")
print(f"{'─'*46}")
total_orig_ret = 0
total_impr_ret = 0
for i, (s, e, label) in enumerate(PERIODS):
    o_ret = all_orig[i]["total_return"]
    i_ret = all_impr[i]["total_return"]
    total_orig_ret += o_ret
    total_impr_ret += i_ret
    winner = "改进" if i_ret > o_ret else "原版"
    short = label.split(":")[0]
    print(f"{short:<20s} {o_ret:>9.2f}% {i_ret:>9.2f}% {winner:>6s}")

print(f"{'─'*46}")
print(f"{'合计':<20s} {total_orig_ret:>9.2f}% {total_impr_ret:>9.2f}%")
print(f"{'平均':<20s} {total_orig_ret/3:>9.2f}% {total_impr_ret/3:>9.2f}%")

# 稳定性对比
orig_weekly = [w["ret"] for r in all_orig for w in r["weeks"]]
impr_weekly = [w["ret"] for r in all_impr for w in r["weeks"]]
print(f"\n  稳定性对比:")
print(f"  {'':20s} {'原版':>10s} {'改进':>10s}")
print(f"  {'周收益标准差':20s} {np.std(orig_weekly):>9.2f}% {np.std(impr_weekly):>9.2f}%")
print(f"  {'最大单周亏损':20s} {min(orig_weekly):>9.2f}% {min(impr_weekly):>9.2f}%")

orig_hit = sum(1 for r in all_orig for w in r["weeks"] if w["ret"] > 0)
impr_hit = sum(1 for r in all_impr for w in r["weeks"] if w["ret"] > 0)
total_wks = sum(r["total_weeks"] for r in all_orig)
print(f"  {'总周胜率':20s} {orig_hit:>9}/{total_wks} {impr_hit:>9}/{total_wks}")

print()
print(f"  >>> 三时段总和：", end=" ")
if total_impr_ret > total_orig_ret:
    print(f"改进版胜出 +{total_impr_ret - total_orig_ret:.2f}%")
else:
    print(f"原版胜出 +{total_orig_ret - total_impr_ret:.2f}%")
