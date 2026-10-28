"""
V3改进版缩量连板选股策略

改进点：
1. 主线识别：同行业涨停>=3家 → 主线，仅做主线的票
2. 位置过滤：连板1-3天缩量OK，4板+缩量→拒绝（必须放量换手）
3. 杂毛过滤：无板块效应的独立涨停 → 跳过
4. 缩量质量：缩量需>30%（vol < prev_vol * 0.7）
"""
import io, json, logging, re, sys, time
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
import os as _os

from config import LIMIT_UP_MIN_DAYS, DEFAULT_LIMIT_UP_PCT, INITIAL_CAPITAL
from tushare_client import TushareClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

tushare_client = TushareClient()

# ── V3 参数 ────────────────────────────────────────────────────────
MAIN_THEME_MIN_STOCKS = 3      # 主线最低涨停家数
VOL_SHRINK_RATIO = 0.7          # 缩量阈值 (vol < prev_vol * 0.7)
MAX_LIMIT_DAYS_FOR_SHRINK = 3   # 连板≤3天允许缩量，≥4天必须放量

# ── 涨停幅度 ────────────────────────────────────────────────────────
def get_limit_pct(code: str) -> float:
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10


def is_limit_up(pct_chg: float, code: str) -> bool:
    """判断是否涨停 (允许0.5%误差)"""
    return pct_chg >= (get_limit_pct(code) * 100 * 0.995)


# ── 主线检测 ────────────────────────────────────────────────────────
def detect_main_themes(date_fmt: str) -> tuple[dict, set]:
    """检测某日的主线板块
    返回: (industry->涨停数, 主线行业名称集合)
    """
    # 获取当日所有涨停股
    daily = tushare_client.get_daily_batch(date_fmt)
    if daily.empty:
        return {}, set()

    all_stocks = tushare_client.get_all_stocks()
    if all_stocks.empty:
        return {}, set()

    # 构建 ts_code -> industry 映射
    industry_map = dict(zip(all_stocks["ts_code"], all_stocks["industry"]))

    # 筛选涨停股
    limit_up_stocks = []
    for _, row in daily.iterrows():
        code = row["ts_code"]
        if code.split(".")[0][:2] not in {"00", "60", "30", "68"}:
            continue
        if is_limit_up(row["pct_chg"], code):
            ind = industry_map.get(code, "未知")
            limit_up_stocks.append((code, ind))

    # 按行业统计
    industry_count = {}
    for _, ind in limit_up_stocks:
        industry_count[ind] = industry_count.get(ind, 0) + 1

    # 主线: 涨停数 >= MAIN_THEME_MIN_STOCKS
    main_themes = {ind for ind, cnt in industry_count.items()
                   if cnt >= MAIN_THEME_MIN_STOCKS}

    return industry_count, main_themes


# ── 连板天数检测 ────────────────────────────────────────────────────
def count_consecutive_limit_days(code: str, date_fmt: str, max_lookback: int = 5) -> int:
    """计算某股票在 date_fmt 之前连续涨停的天数（含当日）"""
    recent_days = tushare_client.get_recent_trading_days(date_fmt, max_lookback + 1)
    if len(recent_days) < 2:
        return 1

    daily_data = tushare_client.get_multi_day_daily(recent_days[:max_lookback + 1])
    dates = sorted(daily_data.keys(), reverse=True)

    days = 0
    for d in dates:
        df = daily_data[d]
        row = df[df["ts_code"] == code]
        if row.empty:
            break
        pct = row.iloc[0]["pct_chg"]
        if is_limit_up(pct, code):
            days += 1
        else:
            break
    return days


# ── V3 核心选股 ────────────────────────────────────────────────────
def get_limit_up_advice_v3(current_cash: float, target_date: str,
                           vol_shrink_ratio: float = VOL_SHRINK_RATIO,
                           main_theme_min: int = MAIN_THEME_MIN_STOCKS,
                           max_shrink_days: int = MAX_LIMIT_DAYS_FOR_SHRINK,
                           verbose: bool = True) -> list[dict]:
    """
    V3改进选股:
    1. 仅做主线的缩量连板
    2. 1-3板缩量OK, 4板+必须放量
    3. 无板块效应 → 跳过
    """
    date_fmt = target_date.replace("-", "")

    if not tushare_client.is_trading_day(date_fmt):
        return []

    prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")

    # ── 主线检测 ──
    industry_count, main_themes = detect_main_themes(prev_date)
    if verbose and main_themes:
        logger.info(f"主线板块: {', '.join(sorted(main_themes))}")

    # ── 基础筛选: 连续涨停+缩量 ──
    recent_days = tushare_client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 5)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        return []

    daily_data = tushare_client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 5])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        return []

    dates = sorted(daily_data.keys(), reverse=True)
    df_t = daily_data[dates[0]][["ts_code", "close", "pct_chg", "vol"]].copy()
    df_t.columns = ["ts_code", "close", "pct_chg", "vol"]
    df_y = daily_data[dates[1]][["ts_code", "close", "pct_chg", "vol"]].copy()
    df_y.columns = ["ts_code", "close_y", "pct_chg_y", "vol_y"]

    merged = df_t.merge(df_y, on="ts_code", how="inner")

    # 加入更多历史数据用于判断连板天数
    for i, d in enumerate(dates[2:5], start=2):
        extra = daily_data[d][["ts_code", "pct_chg", "vol"]].copy()
        extra.columns = ["ts_code", f"pct_chg_{i}", f"vol_{i}"]
        merged = merged.merge(extra, on="ts_code", how="left")

    valid_prefixes = {"00", "60", "30", "68"}
    merged["is_a"] = merged["ts_code"].apply(lambda c: c.split(".")[0][:2] in valid_prefixes)
    merged["lt"] = merged.apply(lambda r: is_limit_up(r["pct_chg"], r["ts_code"]), axis=1)
    merged["ly"] = merged.apply(lambda r: is_limit_up(r["pct_chg_y"], r["ts_code"]), axis=1)

    # V3缩量标准: vol < prev_vol * ratio (原版是 ratio=1.0)
    merged["vd"] = merged["vol"] < merged["vol_y"] * vol_shrink_ratio

    # 基础条件: 连续涨停 + 缩量 + A股
    hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]].copy()

    if hits.empty:
        if verbose:
            logger.info("V3基础筛选: 0只")
        return []

    # ── 获取行业信息 ──
    all_stocks = tushare_client.get_all_stocks()
    name_map = {}
    industry_map = {}
    if not all_stocks.empty:
        name_map = dict(zip(all_stocks["ts_code"], all_stocks["name"]))
        industry_map = dict(zip(all_stocks["ts_code"], all_stocks["industry"]))

    # ── 计算连板天数 ──
    def calc_limit_days(row):
        days = 2  # 至少今日+昨日
        for i in range(2, 5):
            col = f"pct_chg_{i}"
            if col in row and not pd.isna(row[col]):
                if is_limit_up(row[col], row["ts_code"]):
                    days += 1
                else:
                    break
        return days

    hits["limit_days"] = hits.apply(calc_limit_days, axis=1)
    hits["industry"] = hits["ts_code"].map(industry_map).fillna("未知")

    # ── V3 三级过滤 ──
    passed = []
    filtered_log = {"no_theme": [], "high_board_shrink": []}

    for _, row in hits.iterrows():
        code = row["ts_code"]
        short = code.split(".")[0]
        name = name_map.get(code, short)
        ind = row["industry"]
        limit_days = int(row["limit_days"])

        # 过滤1: 主线检测 - 无板块效应 → 跳过
        if ind not in main_themes:
            filtered_log["no_theme"].append(f"{name}({short}) 行业={ind} 涨停数={industry_count.get(ind, 0)}<{main_theme_min}")
            continue

        # 过滤2: 高位缩量 - 4板+缩量 → 跳过
        if limit_days >= max_shrink_days + 1:
            filtered_log["high_board_shrink"].append(
                f"{name}({short}) {limit_days}连板缩量(红线: >={max_shrink_days+1}板)")
            continue

        passed.append(row)

    if verbose:
        logger.info(f"V3筛选: 基础{len(hits)}只 → 通过{len(passed)}只 "
                     f"(主线过滤{len(filtered_log['no_theme'])}只, "
                     f"高位缩量过滤{len(filtered_log['high_board_shrink'])}只)")

    if not passed:
        return []

    # ── 生成结果 ──
    results = []
    trade_cash = current_cash * 0.3 if len(passed) == 1 else current_cash
    per_stock_cash = trade_cash / len(passed)

    for row in passed:
        code = row["ts_code"]
        short = code.split(".")[0]
        buy_price = row["close"]
        volume = int(per_stock_cash / buy_price / 100) * 100
        if volume < 100:
            continue
        results.append({
            "symbol": short,
            "symbol_name": name_map.get(code, short),
            "volume": volume,
        })

    return results


# ── CLI ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        cash_match = re.search(r"资金为(\d+(?:\.\d+)?)元", query)
        current_cash = float(cash_match.group(1)) if cash_match else float(INITIAL_CAPITAL)
        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", query)
        if date_match:
            y, m, d = date_match.groups()
            target_date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        else:
            print("请提供目标日期")
            sys.exit(1)
        result = get_limit_up_advice_v3(current_cash, target_date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result:
            print("今日无符合条件的标的")
    else:
        print("用法: python main_v3_improved.py 资金为XX元，请给出XXXX年XX月XX日的投资建议")
