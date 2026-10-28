"""
V3+监管过滤 vs V3 vs 原版 回测对比
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import logging
logging.basicConfig(level=logging.WARNING)

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from config import INITIAL_CAPITAL, LIMIT_UP_MIN_DAYS, DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient

START = '20260105'
END = '20260721'
client = TushareClient()
trade_cal = client.pro.trade_cal(exchange='SSE', start_date=START, end_date=END)
trading_days = sorted(trade_cal[trade_cal['is_open'] == 1]['cal_date'].tolist())


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10


def is_limit_up(pct_chg, code):
    return pct_chg >= (get_limit_pct(code) * 100 * 0.995)


# 预加载
all_stocks = client.get_all_stocks()
industry_map = dict(zip(all_stocks['ts_code'], all_stocks['industry']))
name_map = dict(zip(all_stocks['ts_code'], all_stocks['name']))

# 缓存 daily_basic (换手率 + PE)
basic_cache = {}


def get_daily_basic_cached(date_fmt):
    if date_fmt in basic_cache:
        return basic_cache[date_fmt]
    try:
        df = client.pro.daily_basic(trade_date=date_fmt)
        if df is not None and not df.empty:
            basic_cache[date_fmt] = dict(zip(
                df['ts_code'],
                zip(df['turnover_rate'], df['pe'], df['pe_ttm'])
            ))
        else:
            basic_cache[date_fmt] = {}
    except Exception:
        basic_cache[date_fmt] = {}
    return basic_cache[date_fmt]


def detect_main_themes(date_fmt):
    daily = client.get_daily_batch(date_fmt)
    if daily.empty:
        return {}, set()
    ic = {}
    for _, row in daily.iterrows():
        code = row['ts_code']
        if code.split('.')[0][:2] not in {'00', '60', '30', '68'}:
            continue
        if is_limit_up(row['pct_chg'], code):
            ind = industry_map.get(code, '未知')
            ic[ind] = ic.get(ind, 0) + 1
    main = {ind for ind, cnt in ic.items() if cnt >= 3}
    return ic, main


def calc_regulatory_risk(code, prev_date_fmt, daily_data_multi, limit_days):
    """
    计算监管风险评分 (满分100)
    """
    score = 0
    dates = sorted(daily_data_multi.keys(), reverse=True)

    # ── 1. 近10日涨幅 (30分) ──
    if len(dates) >= 1:
        today_close = daily_data_multi[dates[0]]
        today_close = today_close[today_close['ts_code'] == code]
        if not today_close.empty:
            current = today_close.iloc[0]['close']

            # 找10天前的收盘价
            lookback_dates = dates[:11]  # 最多取11天
            if len(lookback_dates) >= 2:
                old_date = lookback_dates[-1]
                old_data = daily_data_multi[old_date]
                old_row = old_data[old_data['ts_code'] == code]
                if not old_row.empty:
                    old_close = old_row.iloc[0]['close']
                    ret_10d = (current / old_close - 1) * 100
                    if ret_10d >= 100:
                        score += 30
                    elif ret_10d >= 70:
                        score += 20
                    elif ret_10d >= 50:
                        score += 10

    # ── 2. 缩量/换手率 (30分) ──
    basic_info = get_daily_basic_cached(prev_date_fmt)
    if code in basic_info:
        turnover, pe, pe_ttm = basic_info[code]
        if turnover is not None and not np.isnan(turnover):
            if turnover <= 2:
                score += 30
            elif turnover <= 5:
                score += 20
            elif turnover <= 8:
                score += 10
    else:
        # 无换手率数据，通过成交量估算: 涨停日vol/5日均量
        if len(dates) >= 5:
            vols = []
            for d in dates[:5]:
                df = daily_data_multi[d]
                row = df[df['ts_code'] == code]
                if not row.empty:
                    vols.append(row.iloc[0]['vol'])
            if len(vols) >= 5:
                today_vol = vols[0]
                avg_vol = np.mean(vols[1:]) if len(vols) > 1 else today_vol
                vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1
                if vol_ratio <= 0.3:
                    score += 30
                elif vol_ratio <= 0.5:
                    score += 20

    # ── 3. 连板天数 (25分) ──
    if limit_days >= 7:
        score += 25
    elif limit_days >= 5:
        score += 20
    elif limit_days >= 3:
        score += 10

    # ── 4. 基本面PE (15分) ──
    if code in basic_info:
        turnover, pe, pe_ttm = basic_info[code]
        if pe is not None and not np.isnan(pe):
            if pe < 0 or pe >= 200:
                score += 15
            elif pe >= 100:
                score += 10

    return score


# ═══════════════════════════════════════════
# 原版
# ═══════════════════════════════════════════
def screen_original(client, date_fmt):
    prev_date = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        return None, {}
    daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        return None, {}
    dates = sorted(daily_data.keys(), reverse=True)
    df_t = daily_data[dates[0]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_t.columns = ['ts_code', 'close', 'pct_chg', 'vol']
    df_y = daily_data[dates[1]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_y.columns = ['ts_code', 'close_y', 'pct_chg_y', 'vol_y']
    merged = df_t.merge(df_y, on='ts_code', how='inner')
    merged['is_a'] = merged['ts_code'].apply(lambda c: c.split('.')[0][:2] in {'00', '60', '30', '68'})
    merged['lt'] = merged.apply(lambda r: is_limit_up(r['pct_chg'], r['ts_code']), axis=1)
    merged['ly'] = merged.apply(lambda r: is_limit_up(r['pct_chg_y'], r['ts_code']), axis=1)
    merged['vd'] = merged['vol'] < merged['vol_y']
    hits = merged[merged['lt'] & merged['ly'] & merged['vd'] & merged['is_a']]
    eod_data = client.get_daily_batch(date_fmt)
    eod_prices = dict(zip(eod_data['ts_code'], eod_data['close'])) if not eod_data.empty else {}
    return hits, eod_prices


# ═══════════════════════════════════════════
# V3
# ═══════════════════════════════════════════
def screen_v3(client, date_fmt):
    prev_date = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    industry_count, main_themes = detect_main_themes(prev_date)
    recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 5)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        return None, {}
    daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 5])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        return None, {}
    dates = sorted(daily_data.keys(), reverse=True)
    df_t = daily_data[dates[0]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_t.columns = ['ts_code', 'close', 'pct_chg', 'vol']
    df_y = daily_data[dates[1]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_y.columns = ['ts_code', 'close_y', 'pct_chg_y', 'vol_y']
    merged = df_t.merge(df_y, on='ts_code', how='inner')

    for i, d in enumerate(dates[2:5], start=2):
        extra = daily_data[d][['ts_code', 'pct_chg', 'vol']].copy()
        extra.columns = ['ts_code', f'pct_chg_{i}', f'vol_{i}']
        merged = merged.merge(extra, on='ts_code', how='left')

    merged['is_a'] = merged['ts_code'].apply(lambda c: c.split('.')[0][:2] in {'00', '60', '30', '68'})
    merged['lt'] = merged.apply(lambda r: is_limit_up(r['pct_chg'], r['ts_code']), axis=1)
    merged['ly'] = merged.apply(lambda r: is_limit_up(r['pct_chg_y'], r['ts_code']), axis=1)
    merged['vd'] = merged['vol'] < merged['vol_y'] * 0.7
    hits = merged[merged['lt'] & merged['ly'] & merged['vd'] & merged['is_a']].copy()

    if hits.empty:
        return hits, {}

    def calc_days(row):
        days = 2
        for i in range(2, 5):
            col = f'pct_chg_{i}'
            if col in row and not pd.isna(row[col]) and is_limit_up(row[col], row['ts_code']):
                days += 1
            else:
                break
        return days

    hits['limit_days'] = hits.apply(calc_days, axis=1)
    hits['industry'] = hits['ts_code'].map(industry_map).fillna('未知')

    passed = []
    for _, row in hits.iterrows():
        ind = row['industry']
        ld = int(row['limit_days'])
        if ind not in main_themes:
            continue
        if ld >= 4:
            continue
        passed.append(row)

    hits = pd.DataFrame(passed) if passed else pd.DataFrame()
    eod_data = client.get_daily_batch(date_fmt)
    eod_prices = dict(zip(eod_data['ts_code'], eod_data['close'])) if not eod_data.empty else {}
    return hits, eod_prices


# ═══════════════════════════════════════════
# V3 + 监管过滤
# ═══════════════════════════════════════════
def screen_v3_reg(client, date_fmt):
    prev_date = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    industry_count, main_themes = detect_main_themes(prev_date)

    # 需要更多历史数据用于10日涨幅计算和连板天数
    recent_days = client.get_recent_trading_days(prev_date, 12)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        return None, {}
    daily_data = client.get_multi_day_daily(recent_days[:12])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        return None, {}
    dates = sorted(daily_data.keys(), reverse=True)

    df_t = daily_data[dates[0]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_t.columns = ['ts_code', 'close', 'pct_chg', 'vol']
    df_y = daily_data[dates[1]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_y.columns = ['ts_code', 'close_y', 'pct_chg_y', 'vol_y']
    merged = df_t.merge(df_y, on='ts_code', how='inner')

    for i, d in enumerate(dates[2:8], start=2):
        extra = daily_data[d][['ts_code', 'pct_chg', 'vol']].copy()
        extra.columns = ['ts_code', f'pct_chg_{i}', f'vol_{i}']
        merged = merged.merge(extra, on='ts_code', how='left')

    merged['is_a'] = merged['ts_code'].apply(lambda c: c.split('.')[0][:2] in {'00', '60', '30', '68'})
    merged['lt'] = merged.apply(lambda r: is_limit_up(r['pct_chg'], r['ts_code']), axis=1)
    merged['ly'] = merged.apply(lambda r: is_limit_up(r['pct_chg_y'], r['ts_code']), axis=1)
    merged['vd'] = merged['vol'] < merged['vol_y'] * 0.7
    hits = merged[merged['lt'] & merged['ly'] & merged['vd'] & merged['is_a']].copy()

    if hits.empty:
        return hits, {}

    def calc_days(row):
        days = 2
        for i in range(2, 8):
            col = f'pct_chg_{i}'
            if col in row and not pd.isna(row[col]) and is_limit_up(row[col], row['ts_code']):
                days += 1
            else:
                break
        return days

    hits['limit_days'] = hits.apply(calc_days, axis=1)
    hits['industry'] = hits['ts_code'].map(industry_map).fillna('未知')

    # V3过滤 + 监管评分
    passed = []
    reg_filtered = []
    for _, row in hits.iterrows():
        code = row['ts_code']
        ind = row['industry']
        ld = int(row['limit_days'])

        # V3过滤: 主线
        if ind not in main_themes:
            continue
        # V3过滤: 4板+不能缩量
        if ld >= 4:
            continue

        # 监管风险评分
        reg_score = calc_regulatory_risk(code, prev_date, daily_data, ld)

        # 极高风险(≥80): 直接过滤
        if reg_score >= 80:
            short = code.split('.')[0]
            name = name_map.get(code, short)
            reg_filtered.append(f'{name}({short}) 监管{reg_score}分 ld={ld}')
            continue

        # 高风险(60-79): 标记但保留(todo: 可降仓)
        passed.append(row)

    hits = pd.DataFrame(passed) if passed else pd.DataFrame()
    eod_data = client.get_daily_batch(date_fmt)
    eod_prices = dict(zip(eod_data['ts_code'], eod_data['close'])) if not eod_data.empty else {}
    return hits, eod_prices


def run_full_backtest(screen_fn):
    cash = float(INITIAL_CAPITAL)
    records = []
    for date_fmt in trading_days:
        td = f'{date_fmt[:4]}-{date_fmt[4:6]}-{date_fmt[6:8]}'
        hits, eod_prices = screen_fn(client, date_fmt)
        if hits is None or hits.empty:
            records.append({'date': td, 'nav': cash, 'picks': 0, 'pnl': 0})
            continue
        n = len(hits)
        trade_cash = cash * 0.3 if n == 1 else cash
        per = trade_cash / n
        day_pnl = 0
        for _, row in hits.iterrows():
            buy = row['close']
            sell = eod_prices.get(row['ts_code'], buy)
            shares = int(per / buy / 100) * 100
            if shares >= 100:
                day_pnl += shares * (sell - buy)
        cash += day_pnl
        records.append({'date': td, 'nav': round(cash, 2), 'picks': n, 'pnl': round(day_pnl, 2)})
    return records


print('正在回测 (V3+监管需要获取daily_basic，较慢)...')
print()

rec_orig = run_full_backtest(screen_original)
rec_v3 = run_full_backtest(screen_v3)
rec_v3r = run_full_backtest(screen_v3_reg)

# ── 总览 ──
print(f'全量: {trading_days[0]} ~ {trading_days[-1]} ({len(trading_days)}天)')
print()
print(f'{"版本":<24s} {"最终资金":>14s} {"总收益":>10s} {"交易天":>6s} {"信号":>6s}')
print('-' * 68)
for name, rec in [('原版', rec_orig), ('V3', rec_v3), ('V3+监管', rec_v3r)]:
    final = rec[-1]['nav']
    ret = (final / INITIAL_CAPITAL - 1) * 100
    td = sum(1 for r in rec if r['picks'] > 0)
    sig = sum(r['picks'] for r in rec)
    wins = sum(1 for r in rec if r['pnl'] > 0)
    losses = sum(1 for r in rec if r['pnl'] < 0)
    wr = f'{wins}/{wins + losses}' if (wins + losses) > 0 else '-'
    print(f'{name:<24s} {final:>14,.0f} {ret:>+9.2f}% {td:>6} {sig:>6}  胜{wr}')

# ── 月度 ──
print()
print('【月度收益】')
for name, rec in [('原版', rec_orig), ('V3', rec_v3), ('V3+监管', rec_v3r)]:
    monthly = {}
    for r in rec:
        m = r['date'][:7]
        if m not in monthly:
            monthly[m] = {'start': None, 'end': None}
        if monthly[m]['start'] is None:
            monthly[m]['start'] = r['nav']
        monthly[m]['end'] = r['nav']
    parts = []
    for m in sorted(monthly.keys()):
        mr = (monthly[m]['end'] / monthly[m]['start'] - 1) * 100
        parts.append(f'{m}:{mr:+.1f}%')
    print(f'  {name}: {" | ".join(parts)}')

# ── 最大回撤 ──
print()
for name, rec in [('原版', rec_orig), ('V3', rec_v3), ('V3+监管', rec_v3r)]:
    peak = INITIAL_CAPITAL
    max_dd = 0
    for r in rec:
        if r['nav'] > peak:
            peak = r['nav']
        dd = (r['nav'] / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    print(f'  {name} 最大回撤: {max_dd:.1f}%')

# ── 10日分段 ──
SEG_LEN = 10
segments = [trading_days[i:i + SEG_LEN] for i in range(0, len(trading_days), SEG_LEN)]

print()
print(f'【{SEG_LEN}日分段独立回测（每段50万起）】')
print(f'{"段":<4s} {"区间":<22s} {"原版":>8s} {"V3":>8s} {"V3+监管":>8s}  {"胜负":>6s}')
print('-' * 72)

for si, seg in enumerate(segments):
    results = []
    for fn in [screen_original, screen_v3, screen_v3_reg]:
        c = float(INITIAL_CAPITAL)
        for date_fmt in seg:
            hits, prices = fn(client, date_fmt)
            if hits is not None and not hits.empty:
                n = len(hits)
                tc = c * 0.3 if n == 1 else c
                per = tc / n
                for _, row in hits.iterrows():
                    buy = row['close']
                    sell = prices.get(row['ts_code'], buy)
                    sh = int(per / buy / 100) * 100
                    if sh >= 100:
                        c += sh * (sell - buy)
        results.append((c / INITIAL_CAPITAL - 1) * 100)

    r1, r2, r3 = results
    best = max(r1, r2, r3)
    w = '原版' if r1 == best else ('V3' if r2 == best else 'V3+监')
    label = f'{seg[0][4:]}~{seg[-1][4:]}'
    print(f'{si + 1:<4} {label:<22s} {r1:>+7.2f}% {r2:>+7.2f}% {r3:>+7.2f}%  {w:>6s}')

# 统计
print()
for name, fn in [('原版', screen_original), ('V3', screen_v3), ('V3+监管', screen_v3_reg)]:
    seg_rets = []
    for seg in segments:
        c = float(INITIAL_CAPITAL)
        for date_fmt in seg:
            hits, prices = fn(client, date_fmt)
            if hits is not None and not hits.empty:
                n = len(hits)
                tc = c * 0.3 if n == 1 else c
                per = tc / n
                for _, row in hits.iterrows():
                    buy = row['close']
                    sell = prices.get(row['ts_code'], buy)
                    sh = int(per / buy / 100) * 100
                    if sh >= 100:
                        c += sh * (sell - buy)
        seg_rets.append((c / INITIAL_CAPITAL - 1) * 100)

    print(f'  {name}: 均{np.mean(seg_rets):+.2f}% | 正段{sum(1 for r in seg_rets if r>0)}/{len(seg_rets)} | 最差{min(seg_rets):+.2f}% | 最佳{max(seg_rets):+.2f}%')

# 7月重点
print()
print('【7月重点】')
for name, rec in [('原版', rec_orig), ('V3', rec_v3), ('V3+监管', rec_v3r)]:
    jr = [r for r in rec if r['date'].startswith('2026-07')]
    if len(jr) >= 2:
        ret_j = (jr[-1]['nav'] / jr[0]['nav'] - 1) * 100
    else:
        ret_j = 0
    td = sum(1 for r in jr if r['picks'] > 0)
    sig = sum(r['picks'] for r in jr)
    wins = sum(1 for r in jr if r['pnl'] > 0)
    losses = sum(1 for r in jr if r['pnl'] < 0)
    print(f'  {name}: 7月{ret_j:+.2f}% | 交易{td}天 | {sig}只信号 | 胜{wins}/{wins+losses}')

print()
print('Done!')
