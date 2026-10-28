"""
双版本回测对比：原版 vs 改进版
- 原版：缩量连板（连板≥2天，任意缩量），均分资金
- 改进版：缩量质量过滤（缩量>20%）+ 单票仓位上限(25%) + 市场环境仓位管理
- 规则：上周收益滚入本周本金（复利）
- 回测区间：2026-05-11 至 2026-05-29
"""
import io, json, sys, time
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


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

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
    weeks, current_week, current_week_num = [], [], None
    for d in trading_days:
        dt = datetime.strptime(d, "%Y-%m-%d")
        wk = (dt.isocalendar()[0], dt.isocalendar()[1])
        if wk != current_week_num:
            if current_week: weeks.append(current_week)
            current_week, current_week_num = [d], wk
        else:
            current_week.append(d)
    if current_week: weeks.append(current_week)
    return weeks


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix): return pct
    return 0.10


def get_stock_names(client):
    all_stocks = client.get_all_stocks()
    if not all_stocks.empty:
        return dict(zip(all_stocks["ts_code"], all_stocks["name"]))
    return {}


# ═══════════════════════════════════════════════════════════════
# 原版策略
# ═══════════════════════════════════════════════════════════════

def run_original(week_days, client, initial_cash, name_map):
    """原版：缩量连板（任意缩量），均分资金，无上限"""
    cash = float(initial_cash)
    daily_records = []

    for target_date in week_days:
        date_fmt = target_date.replace("-", "")
        if not client.is_trading_day(date_fmt):
            continue

        prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
        if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
            continue

        daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
        if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
            continue

        dates = sorted(daily_data.keys(), reverse=True)
        df_t = daily_data[dates[0]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_t.columns = ["ts_code", "close", "pct_chg", "vol"]
        df_y = daily_data[dates[1]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_y.columns = ["ts_code", "close_y", "pct_chg_y", "vol_y"]

        merged = df_t.merge(df_y, on="ts_code", how="inner")

        valid_prefixes = {"00", "60", "30", "68"}
        merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in valid_prefixes)

        def is_limit(row, col="pct_chg"):
            limit_pct = get_limit_pct(row["ts_code"])
            return row[col] >= (limit_pct * 100 * 0.995)

        merged["lt"] = merged.apply(lambda r: is_limit(r, "pct_chg"), axis=1)
        merged["ly"] = merged.apply(lambda r: is_limit(r, "pct_chg_y"), axis=1)
        merged["vd"] = merged["vol"] < merged["vol_y"]  # 原版：任意缩量

        hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]

        eod_data = client.get_daily_batch(date_fmt)
        eod_prices = {}
        if not eod_data.empty:
            eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"]))

        if hits.empty:
            daily_records.append({"date": target_date, "nav": cash, "picks": 0, "pnl": 0, "detail": []})
            continue

        n = len(hits)
        per_stock = cash / n  # 原版：均分，无上限
        day_pnl = 0
        picks_detail = []

        for _, row in hits.iterrows():
            code = row["ts_code"]
            short = code.split(".")[0]
            buy_price = row["close"]
            sell_price = eod_prices.get(code, buy_price)
            shares = int(per_stock / buy_price / 100) * 100
            if shares < 100:
                continue
            pnl = shares * (sell_price - buy_price)
            day_pnl += pnl
            picks_detail.append({
                "code": short, "name": name_map.get(code, short),
                "buy": round(buy_price, 2), "sell": round(sell_price, 2),
                "shares": shares, "pnl": round(pnl, 2),
                "vol_pct": round(float(row["vol"] / row["vol_y"]) * 100, 1) if row["vol_y"] > 0 else 0
            })

        cash += day_pnl
        daily_records.append({
            "date": target_date, "nav": round(cash, 2),
            "picks": len(picks_detail), "pnl": round(day_pnl, 2),
            "detail": picks_detail
        })

    return daily_records


# ═══════════════════════════════════════════════════════════════
# 改进版策略
# ═══════════════════════════════════════════════════════════════

def run_improved(week_days, client, initial_cash, name_map):
    """改进版：缩量>20% + 单票上限25% + 市场环境仓位管理"""
    cash = float(initial_cash)
    daily_records = []

    for target_date in week_days:
        date_fmt = target_date.replace("-", "")
        if not client.is_trading_day(date_fmt):
            continue

        # ── 市场环境判断 ──
        regime_data = client.get_market_regime(date_fmt)
        position_target = regime_data.get("position_target", 0.90)
        max_stocks = regime_data.get("max_stocks", 3)

        prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
        if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
            continue

        daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
        if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
            continue

        dates = sorted(daily_data.keys(), reverse=True)
        df_t = daily_data[dates[0]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_t.columns = ["ts_code", "close", "pct_chg", "vol"]
        df_y = daily_data[dates[1]][["ts_code", "close", "pct_chg", "vol"]].copy()
        df_y.columns = ["ts_code", "close_y", "pct_chg_y", "vol_y"]

        merged = df_t.merge(df_y, on="ts_code", how="inner")

        valid_prefixes = {"00", "60", "30", "68"}
        merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in valid_prefixes)

        def is_limit(row, col="pct_chg"):
            limit_pct = get_limit_pct(row["ts_code"])
            return row[col] >= (limit_pct * 100 * 0.995)

        merged["lt"] = merged.apply(lambda r: is_limit(r, "pct_chg"), axis=1)
        merged["ly"] = merged.apply(lambda r: is_limit(r, "pct_chg_y"), axis=1)
        # 改进1: 缩量质量过滤 — 成交量必须下降超过20%
        merged["vd"] = merged["vol"] < merged["vol_y"] * 0.8

        hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]

        eod_data = client.get_daily_batch(date_fmt)
        eod_prices = {}
        if not eod_data.empty:
            eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"]))

        if hits.empty:
            daily_records.append({
                "date": target_date, "nav": cash, "picks": 0, "pnl": 0,
                "detail": [], "regime": regime_data.get("regime", "normal")
            })
            continue

        # 改进2: 市场环境限制 — 控制总仓位和最大持仓数
        effective_cash = cash * position_target
        if len(hits) > max_stocks:
            hits = hits.head(max_stocks)

        n = len(hits)
        # 改进3: 单票仓位上限25%
        max_per_stock = cash * 0.25
        raw_per_stock = effective_cash / n
        per_stock = min(raw_per_stock, max_per_stock)

        day_pnl = 0
        picks_detail = []

        for _, row in hits.iterrows():
            code = row["ts_code"]
            short = code.split(".")[0]
            buy_price = row["close"]
            sell_price = eod_prices.get(code, buy_price)
            shares = int(per_stock / buy_price / 100) * 100
            if shares < 100:
                continue
            pnl = shares * (sell_price - buy_price)
            day_pnl += pnl
            picks_detail.append({
                "code": short, "name": name_map.get(code, short),
                "buy": round(buy_price, 2), "sell": round(sell_price, 2),
                "shares": shares, "pnl": round(pnl, 2),
                "vol_pct": round(float(row["vol"] / row["vol_y"]) * 100, 1) if row["vol_y"] > 0 else 0
            })

        cash += day_pnl
        daily_records.append({
            "date": target_date, "nav": round(cash, 2),
            "picks": len(picks_detail), "pnl": round(day_pnl, 2),
            "detail": picks_detail,
            "regime": regime_data.get("regime", "normal")
        })

    return daily_records


# ═══════════════════════════════════════════════════════════════
# 回测主流程（复利版：每周收益滚入下周本金）
# ═══════════════════════════════════════════════════════════════

def backtest_compound(start_ymd, end_ymd, strategy_fn, label, client, name_map):
    """按周复利回测：上周结束资金 = 下周起始资金"""
    trading_days = get_trading_days_in_range(start_ymd, end_ymd, client)
    weeks = group_by_week(trading_days)

    capital = float(INITIAL_CAPITAL)
    all_records = []

    for wi, week_days in enumerate(weeks):
        if len(week_days) < 2:
            continue
        records = strategy_fn(week_days, client, capital, name_map)
        if records:
            capital = records[-1]["nav"]  # 复利：滚入下周
        all_records.append({
            "week_idx": wi + 1,
            "week_start": week_days[0],
            "week_end": week_days[-1],
            "days": len(week_days),
            "start_nav": records[0]["nav"] if records else capital,
            "end_nav": records[-1]["nav"] if records else capital,
            "records": records,
        })

    return all_records, capital


def print_results(results, final_capital, label):
    """格式化输出回测结果"""
    start_cap = INITIAL_CAPITAL
    print(f"\n{'═' * 70}")
    print(f"  {label}")
    print(f"{'═' * 70}")

    for week in results:
        ret = (week["end_nav"] / week["start_nav"] - 1) * 100
        tag = "盈利" if ret > 0 else "亏损" if ret < 0 else "持平"
        print(f"\n第{week['week_idx']}周 [{week['week_start']} → {week['week_end']}]")
        print(f"  起始: {week['start_nav']:,.0f} → 结束: {week['end_nav']:,.0f} | {tag} {ret:+.2f}%")
        for dr in week["records"]:
            regime_str = f" [{dr.get('regime', 'normal')}]" if 'regime' in dr else ""
            if dr.get("picks", 0) > 0:
                print(f"  {dr['date']}{regime_str}: 选中{dr['picks']}只, 盈亏 {dr.get('pnl', 0):+,.0f}, 净值 {dr['nav']:,.0f}")
                for p in dr.get("detail", []):
                    vol_info = f" (量比{p['vol_pct']}%)" if 'vol_pct' in p else ""
                    print(f"    {p['code']} {p['name']}: {p['buy']}→{p['sell']} ×{p['shares']}股 PnL={p['pnl']:+,.0f}{vol_info}")
            else:
                print(f"  {dr['date']}{regime_str}: 无选股, 净值 {dr['nav']:,.0f}")

    total_ret = (final_capital / start_cap - 1) * 100
    win_weeks = sum(1 for w in results if w["end_nav"] > w["start_nav"])
    weekly_rets = [(w["end_nav"] / w["start_nav"] - 1) * 100 for w in results]

    print(f"\n{'─' * 70}")
    print(f"  汇总: {start_cap:,.0f} → {final_capital:,.0f} CNY")
    print(f"  绝对收益: {final_capital - start_cap:+,.0f} CNY")
    print(f"  总收益率: {total_ret:+.2f}%")
    print(f"  盈利周: {win_weeks}/{len(results)} ({win_weeks/len(results)*100:.0f}%)")
    print(f"  平均周收益: {np.mean(weekly_rets):+.2f}%")
    if win_weeks > 0:
        print(f"  平均盈利周: {np.mean([r for r in weekly_rets if r > 0]):+.2f}%")
    if win_weeks < len(results):
        print(f"  平均亏损周: {np.mean([r for r in weekly_rets if r < 0]):+.2f}%")
    print(f"  最佳周: {max(weekly_rets):+.2f}%")
    print(f"  最差周: {min(weekly_rets):+.2f}%")

    return {
        "label": label,
        "final_capital": final_capital,
        "total_return_pct": total_ret,
        "weekly_rets": weekly_rets,
        "win_weeks": win_weeks,
        "total_weeks": len(results),
    }


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    start_ymd = "20260511"
    end_ymd = "20260529"

    print(f"回测区间: 2026-05-11 → 2026-05-29")
    print(f"起始资金: {INITIAL_CAPITAL:,} CNY")
    print(f"规则: 前日收盘买入 → 当日收盘卖出")
    print(f"资金: 上周收益滚入下周本金（复利）")
    print()

    client = TushareClient()
    name_map = get_stock_names(client)

    # ── 原版回测 ──
    t0 = time.time()
    results_orig, final_orig = backtest_compound(
        start_ymd, end_ymd, run_original, "原版策略 (任意缩量 + 均分资金)", client, name_map
    )
    summary_orig = print_results(results_orig, final_orig, "原版策略 (任意缩量 + 均分资金)")
    t_orig = time.time() - t0

    # ── 改进版回测 ──
    t0 = time.time()
    results_v2, final_v2 = backtest_compound(
        start_ymd, end_ymd, run_improved, "改进版策略 (缩量>20% + 单票≤25% + 市场环境)", client, name_map
    )
    summary_v2 = print_results(results_v2, final_v2, "改进版策略 (缩量>20% + 单票≤25% + 市场环境)")
    t_v2 = time.time() - t0

    # ── 对比 ──
    print(f"\n{'═' * 70}")
    print(f"  对比总览")
    print(f"{'═' * 70}")
    print(f"  {'':20s} {'原版':>15s} {'改进版':>15s}")
    print(f"  {'─' * 50}")
    print(f"  {'最终净值':20s} {summary_orig['final_capital']:>15,.0f} {summary_v2['final_capital']:>15,.0f}")
    print(f"  {'总收益率':20s} {summary_orig['total_return_pct']:>14+.2f}% {summary_v2['total_return_pct']:>14+.2f}%")
    print(f"  {'盈利周数':20s} {summary_orig['win_weeks']:>14}/{summary_orig['total_weeks']} {summary_v2['win_weeks']:>14}/{summary_v2['total_weeks']}")
    print(f"  {'平均周收益':20s} {np.mean(summary_orig['weekly_rets']):>14+.2f}% {np.mean(summary_v2['weekly_rets']):>14+.2f}%")
    print(f"  {'最佳周':20s} {max(summary_orig['weekly_rets']):>14+.2f}% {max(summary_v2['weekly_rets']):>14+.2f}%")
    print(f"  {'最差周':20s} {min(summary_orig['weekly_rets']):>14+.2f}% {min(summary_v2['weekly_rets']):>14+.2f}%")

    diff = summary_v2['total_return_pct'] - summary_orig['total_return_pct']
    print(f"\n  改进版 vs 原版: {diff:+.2f}%")
    if diff > 0:
        print(f"  改进版多赚: {summary_v2['final_capital'] - summary_orig['final_capital']:+,.0f} CNY")
    else:
        print(f"  改进版少赚: {summary_v2['final_capital'] - summary_orig['final_capital']:+,.0f} CNY")

    print(f"\n  回测耗时: 原版 {t_orig:.1f}s | 改进版 {t_v2:.1f}s")


if __name__ == "__main__":
    main()
