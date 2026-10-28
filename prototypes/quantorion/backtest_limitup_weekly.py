"""缩量连板策略按周回测：前日收盘买入，当日收盘卖出，周重置"""
import io, json, logging, sys, time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("limitup_weekly")

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
    weeks, current_week, current_week_num = [], [], None
    for d in trading_days:
        dt = datetime.strptime(d, "%Y-%m-%d")
        wk = (dt.isocalendar()[0], dt.isocalendar()[1])
        if wk != current_week_num:
            if current_week: weeks.append(current_week)
            current_week, current_week_num = [d], wk
        else: current_week.append(d)
    if current_week: weeks.append(current_week)
    return weeks

def get_limit_pct(code: str) -> float:
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix): return pct
    return 0.10

def run_limitup_week(week_days, client):
    """运行一周缩量连板策略：每天选股，当日收盘卖出"""
    cash = float(INITIAL_CAPITAL)
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
        df_t = daily_data[dates[0]][["ts_code","close","pct_chg","vol"]].copy()
        df_t.columns = ["ts_code","close","pct_chg","vol"]
        df_y = daily_data[dates[1]][["ts_code","close","pct_chg","vol"]].copy()
        df_y.columns = ["ts_code","close_y","pct_chg_y","vol_y"]

        merged = df_t.merge(df_y, on="ts_code", how="inner")

        # A股过滤
        valid_prefixes = {"00","60","30","68"}
        merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in valid_prefixes)

        # 涨停判断
        def is_limit(row, col="pct_chg"):
            limit_pct = get_limit_pct(row["ts_code"])
            return row[col] >= (limit_pct * 100 * 0.995)

        merged["lt"] = merged.apply(lambda r: is_limit(r,"pct_chg"), axis=1)
        merged["ly"] = merged.apply(lambda r: is_limit(r,"pct_chg_y"), axis=1)
        merged["vd"] = merged["vol"] < merged["vol_y"]

        hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]

        # EOD: 获取当日收盘价（卖出价）
        eod_data = client.get_daily_batch(date_fmt)
        eod_prices = {}
        if not eod_data.empty:
            eod_prices = dict(zip(eod_data["ts_code"], eod_data["close"]))

        if hits.empty:
            daily_records.append({"date": target_date, "nav": cash, "picks": 0})
            continue

        # 均分资金，前日收盘买入，当日收盘卖出
        n = len(hits)
        per_stock = cash / n
        day_pnl = 0

        for _, row in hits.iterrows():
            code = row["ts_code"]
            buy_price = row["close"]  # 前日收盘价
            sell_price = eod_prices.get(code, buy_price)  # 当日收盘价
            shares = int(per_stock / buy_price / 100) * 100
            if shares < 100: continue
            pnl = shares * (sell_price - buy_price)
            day_pnl += pnl

        cash += day_pnl
        daily_records.append({"date": target_date, "nav": round(cash, 2), "picks": len(hits)})

    start_nav = INITIAL_CAPITAL
    end_nav = daily_records[-1]["nav"] if daily_records else INITIAL_CAPITAL
    return {
        "week_start": week_days[0], "week_end": week_days[-1],
        "days": len(week_days), "start_nav": start_nav,
        "end_nav": end_nav,
        "return_pct": round((end_nav/start_nav - 1)*100, 2),
        "daily_records": daily_records,
    }

def main(start_ymd="20260101", end_ymd="20260603"):
    client = TushareClient()
    trading_days = get_trading_days_in_range(start_ymd, end_ymd, client)
    weeks = group_by_week(trading_days)
    print(f"缩量连板按周回测: {len(weeks)}周 | 初始{INITIAL_CAPITAL:,}CNY | 前日收盘买→当日收盘卖\n")

    results = []
    total_start = time.time()
    for wi, week_days in enumerate(weeks):
        if len(week_days) < 2: continue
        ws = time.time()
        r = run_limitup_week(week_days, client)
        results.append(r)
        e = time.time()-ws
        ret_str = f"{r['return_pct']:+.2f}%"
        print(f"  第{wi+1:02d}周 [{r['week_start']}→{r['week_end']}] ({r['days']}天) "
              f"{INITIAL_CAPITAL:,}→{r['end_nav']:,.0f} {ret_str} [{e:.1f}s]")

    returns = [r["return_pct"] for r in results]
    win = sum(1 for r in returns if r>0)
    lose = sum(1 for r in returns if r<0)
    cumulative = 1.0
    for r in returns: cumulative *= (1+r/100)
    avg_ret = np.mean(returns)

    print(f"\n{'='*65}")
    print(f"  缩量连板回测汇总")
    print(f"{'='*65}")
    print(f"盈利周: {win}/{len(results)} ({win/len(results)*100:.1f}%)")
    print(f"平均周收益: {avg_ret:+.2f}%")
    if win>0: print(f"平均盈利: {np.mean([r for r in returns if r>0]):+.2f}%")
    if lose>0: print(f"平均亏损: {np.mean([r for r in returns if r<0]):+.2f}%")
    print(f"最佳: {results[int(np.argmax(returns))]['week_start']} ({max(returns):+.2f}%)")
    print(f"最差: {results[int(np.argmin(returns))]['week_start']} ({min(returns):+.2f}%)")
    print(f"复利累计: {(cumulative-1)*100:+.2f}%")
    print(f"耗时: {time.time()-total_start:.1f}s")

if __name__ == "__main__":
    main()
