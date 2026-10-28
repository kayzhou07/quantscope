"""
三时段三版本回测：原版 vs 改进版 vs 动态版
动态版：市场环境联动缩量阈值
  正常/牛市: 任意缩量
  震荡:      缩量 > 10%
  弱势/熊市: 缩量 > 20%
+ 始终保留：单票上限25% + 市场环境仓位管理
规则：复利（周收益滚入下周），前日收盘买入 → 当日收盘卖出
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


class BacktestRunner:
    def __init__(self, client):
        self.client = client
        all_stocks = client.get_all_stocks()
        self.name_map = dict(zip(all_stocks["ts_code"], all_stocks["name"])) if not all_stocks.empty else {}

    def run_original(self, week_days, initial_cash):
        """原版：任意缩量 + 均分全仓"""
        cash = float(initial_cash)
        daily_records = []
        for target_date in week_days:
            date_fmt = target_date.replace("-", "")
            if not self.client.is_trading_day(date_fmt): continue
            hits, eod_prices = self._screen_stocks(date_fmt, vol_threshold=1.0)  # 任意缩量
            if hits.empty:
                daily_records.append({"date": target_date, "nav": cash, "picks": 0, "pnl": 0})
                continue
            n = len(hits); per_stock = cash / n; day_pnl = 0
            for _, row in hits.iterrows():
                buy = row["close"]; sell = eod_prices.get(row["ts_code"], buy)
                shares = int(per_stock / buy / 100) * 100
                if shares < 100: continue
                day_pnl += shares * (sell - buy)
            cash += day_pnl
            daily_records.append({"date": target_date, "nav": round(cash,2), "picks": n, "pnl": round(day_pnl,2)})
        return daily_records

    def run_improved(self, week_days, initial_cash):
        """改进版：缩量>20% + 单票<=25% + 市场环境仓位"""
        cash = float(initial_cash)
        daily_records = []
        for target_date in week_days:
            date_fmt = target_date.replace("-", "")
            if not self.client.is_trading_day(date_fmt): continue

            regime_data = self.client.get_market_regime(date_fmt)
            position_target = regime_data.get("position_target", 0.90)
            max_stocks = regime_data.get("max_stocks", 3)

            hits, eod_prices = self._screen_stocks(date_fmt, vol_threshold=0.8)  # 缩量>20%
            if hits.empty:
                daily_records.append({"date": target_date, "nav": cash, "picks": 0, "pnl": 0})
                continue

            effective_cash = cash * position_target
            if len(hits) > max_stocks:
                hits = hits.head(max_stocks)
            n = len(hits)
            per_stock = min(effective_cash / n, cash * 0.25)
            day_pnl = 0
            for _, row in hits.iterrows():
                buy = row["close"]; sell = eod_prices.get(row["ts_code"], buy)
                shares = int(per_stock / buy / 100) * 100
                if shares < 100: continue
                day_pnl += shares * (sell - buy)
            cash += day_pnl
            daily_records.append({"date": target_date, "nav": round(cash,2), "picks": n, "pnl": round(day_pnl,2)})
        return daily_records

    def run_dynamic(self, week_days, initial_cash):
        """动态版：缩量阈值联动市场环境 + 单票<=25% + 市场环境仓位"""
        cash = float(initial_cash)
        daily_records = []
        for target_date in week_days:
            date_fmt = target_date.replace("-", "")
            if not self.client.is_trading_day(date_fmt): continue

            regime_data = self.client.get_market_regime(date_fmt)
            regime = regime_data.get("regime", "normal")
            position_target = regime_data.get("position_target", 0.90)
            max_stocks = regime_data.get("max_stocks", 3)

            # 动态缩量阈值: 正常=任意, 震荡=10%, 弱势=20%
            vol_map = {"normal": 1.0, "oscillation": 0.9, "weak": 0.8}
            vol_threshold = vol_map.get(regime, 1.0)

            hits, eod_prices = self._screen_stocks(date_fmt, vol_threshold=vol_threshold)
            if hits.empty:
                daily_records.append({"date": target_date, "nav": cash, "picks": 0, "pnl": 0, "regime": regime})
                continue

            effective_cash = cash * position_target
            if len(hits) > max_stocks:
                hits = hits.head(max_stocks)
            n = len(hits)
            per_stock = min(effective_cash / n, cash * 0.25)
            day_pnl = 0
            for _, row in hits.iterrows():
                buy = row["close"]; sell = eod_prices.get(row["ts_code"], buy)
                shares = int(per_stock / buy / 100) * 100
                if shares < 100: continue
                day_pnl += shares * (sell - buy)
            cash += day_pnl
            daily_records.append({"date": target_date, "nav": round(cash,2), "picks": n, "pnl": round(day_pnl,2), "regime": regime})
        return daily_records

    def _screen_stocks(self, date_fmt, vol_threshold=1.0):
        """通用选股：缩量连板，vol_threshold=1.0=任意缩量, 0.9=缩量>10%, 0.8=缩量>20%"""
        prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        recent_days = self.client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
        if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
            return pd.DataFrame(), {}
        daily_data = self.client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
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
        merged["vd"] = merged["vol"] < merged["vol_y"] * vol_threshold
        hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]
        eod_data = self.client.get_daily_batch(date_fmt)
        eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"])) if not eod_data.empty else {}
        return hits, eod_prices


def backtest_period(start_ymd, end_ymd, strategy_fn, client, runner):
    trading_days = get_trading_days_in_range(start_ymd, end_ymd, client)
    weeks = group_by_week(trading_days)
    capital = float(INITIAL_CAPITAL)
    week_summaries = []
    for wi, wd in enumerate(weeks):
        if len(wd) < 2: continue
        start_cap = capital
        records = strategy_fn(wd, capital)
        if records:
            capital = records[-1]["nav"]
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
        "std_week": np.std([w["ret"] for w in week_summaries]),
    }


PERIODS = [
    ("20260511","20260529","时段1: 5/11-5/29"),
    ("20260601","20260619","时段2: 6/1-6/19"),
    ("20260622","20260710","时段3: 6/22-7/10"),
]

client = TushareClient()
runner = BacktestRunner(client)

print("=" * 70)
print("  三版本回测对比: 原版 vs 改进(固定20%) vs 动态(联动市场)")
print("=" * 70)
print(f"  起始资金: {INITIAL_CAPITAL:,} CNY | 复利(周收益滚入下周)")
print(f"  原版  : 任意缩量 + 均分全仓")
print(f"  改进  : 缩量>20%  + 单票<=25% + 市场环境仓位")
print(f"  动态  : 牛市任意/震荡>10%/熊市>20% + 单票<=25% + 市场环境仓位")
print()

all_results = {"原版": [], "改进": [], "动态": []}

for s, e, label in PERIODS:
    print(f"{'─'*65}")
    print(f"  {label}")
    print(f"{'─'*65}")

    # 原版
    r1 = backtest_period(s, e, runner.run_original, client, runner)
    all_results["原版"].append(r1)

    # 改进版
    r2 = backtest_period(s, e, runner.run_improved, client, runner)
    all_results["改进"].append(r2)

    # 动态版
    r3 = backtest_period(s, e, runner.run_dynamic, client, runner)
    all_results["动态"].append(r3)

    for vname, r in [("原版", r1), ("改进", r2), ("动态", r3)]:
        weeks_str = " → ".join([f"{w['ret']:+.1f}%" for w in r["weeks"]])
        print(f"  {vname}: {INITIAL_CAPITAL:,} -> {r['final_capital']:,.0f} | "
              f"{r['total_return']:+.2f}% | 周: {weeks_str}")

    # 时段内最好
    best = max([("原版", r1), ("改进", r2), ("动态", r3)], key=lambda x: x[1]["total_return"])
    print(f"  >>> {label}: {best[0]}领先")

# ── 总对比表 ──
print(f"\n{'═'*70}")
print(f"  汇总对比")
print(f"{'═'*70}")
header = f"{'时段':<18s} {'原版':>9s} {'改进':>9s} {'动态':>9s}  {'优胜':>6s}"
print(header)
print(f"{'─'*58}")

for i, (s, e, label) in enumerate(PERIODS):
    o = all_results["原版"][i]["total_return"]
    im = all_results["改进"][i]["total_return"]
    dy = all_results["动态"][i]["total_return"]
    best_name = max([("原版", o), ("改进", im), ("动态", dy)], key=lambda x: x[1])[0]
    short = label.split(":")[0]
    print(f"{short:<18s} {o:>8.2f}% {im:>8.2f}% {dy:>8.2f}%  {best_name:>6s}")

# 合计行
print(f"{'─'*58}")
sum_o = sum(r["total_return"] for r in all_results["原版"])
sum_im = sum(r["total_return"] for r in all_results["改进"])
sum_dy = sum(r["total_return"] for r in all_results["动态"])
print(f"{'合计':<18s} {sum_o:>8.2f}% {sum_im:>8.2f}% {sum_dy:>8.2f}%")
print(f"{'平均':<18s} {sum_o/3:>8.2f}% {sum_im/3:>8.2f}% {sum_dy/3:>8.2f}%")

# 稳定性
print(f"\n{'─'*65}")
print(f"  稳定性与风险对比")
print(f"{'─'*65}")
print(f"  {'指标':<20s} {'原版':>12s} {'改进':>12s} {'动态':>12s}")
print(f"  {'─'*56}")

all_orig_wk = [w["ret"] for r in all_results["原版"] for w in r["weeks"]]
all_impr_wk = [w["ret"] for r in all_results["改进"] for w in r["weeks"]]
all_dyna_wk = [w["ret"] for r in all_results["动态"] for w in r["weeks"]]

print(f"  {'周收益标准差':<20s} {np.std(all_orig_wk):>11.2f}% {np.std(all_impr_wk):>11.2f}% {np.std(all_dyna_wk):>11.2f}%")
print(f"  {'最大单周亏损':<20s} {min(all_orig_wk):>11.2f}% {min(all_impr_wk):>11.2f}% {min(all_dyna_wk):>11.2f}%")

win_orig = sum(1 for r in all_orig_wk if r > 0)
win_impr = sum(1 for r in all_impr_wk if r > 0)
win_dyna = sum(1 for r in all_dyna_wk if r > 0)
total_wks = len(all_orig_wk)
print(f"  {'总周胜率':<20s} {win_orig:>9}/{total_wks} {win_impr:>9}/{total_wks} {win_dyna:>9}/{total_wks}")

# 综合得分: 总收益排名 + 稳定性排名
def score(results, all_wk, name):
    total_ret = sum(r["total_return"] for r in results)
    std = np.std(all_wk)
    # 归一化排名 (收益越高越好, 波动越低越好)
    return total_ret / (std + 0.01)

scores = {
    "原版": score(all_results["原版"], all_orig_wk, "原版"),
    "改进": score(all_results["改进"], all_impr_wk, "改进"),
    "动态": score(all_results["动态"], all_dyna_wk, "动态"),
}
print(f"\n  {'综合得分(收益/波动)':<20s} {scores['原版']:>11.1f} {scores['改进']:>11.1f} {scores['动态']:>11.1f}")

best_overall = max(scores, key=scores.get)
print(f"\n  >>> 综合最优: {best_overall}")
