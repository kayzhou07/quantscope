"""
V3 vs 原版 回测对比
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


# 预加载行业
all_stocks = client.get_all_stocks()
industry_map = dict(zip(all_stocks['ts_code'], all_stocks['industry']))


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


print('正在回测 V3 vs 原版 ...')
print()

rec_orig = run_full_backtest(screen_original)
rec_v3 = run_full_backtest(screen_v3)

# ── 总览 ──
print(f'全量: {trading_days[0]} ~ {trading_days[-1]} ({len(trading_days)}天)')
print()
print(f'{"版本":<20s} {"最终资金":>14s} {"总收益":>10s} {"交易天":>6s} {"信号":>6s} {"胜率":>8s}')
print('-' * 72)
for name, rec in [('原版', rec_orig), ('V3改进', rec_v3)]:
    final = rec[-1]['nav']
    ret = (final / INITIAL_CAPITAL - 1) * 100
    td = sum(1 for r in rec if r['picks'] > 0)
    sig = sum(r['picks'] for r in rec)
    wins = sum(1 for r in rec if r['pnl'] > 0)
    losses = sum(1 for r in rec if r['pnl'] < 0)
    wr = f'{wins}/{wins + losses}' if (wins + losses) > 0 else '-'
    print(f'{name:<20s} {final:>14,.0f} {ret:>+9.2f}% {td:>6} {sig:>6} {wr:>8s}')

# ── 月度 ──
print()
print('【月度收益】')
for name, rec in [('原版', rec_orig), ('V3改进', rec_v3)]:
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
for name, rec in [('原版', rec_orig), ('V3改进', rec_v3)]:
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
print(f'【{SEG_LEN}日分段独立回测（每段从50万开始）】')
print(f'{"段":<4s} {"区间":<22s} {"原版":>8s} {"V3改进":>8s}  {"胜负":>6s}  {"原信号":>5s} {"V3信号":>5s}')
print('-' * 80)

for si, seg in enumerate(segments):
    c1 = float(INITIAL_CAPITAL)
    c3 = float(INITIAL_CAPITAL)
    s1 = s3 = 0

    for date_fmt in seg:
        hits1, p1 = screen_original(client, date_fmt)
        if hits1 is not None and not hits1.empty:
            n = len(hits1)
            tc = c1 * 0.3 if n == 1 else c1
            per = tc / n
            for _, row in hits1.iterrows():
                buy = row['close']
                sell = p1.get(row['ts_code'], buy)
                sh = int(per / buy / 100) * 100
                if sh >= 100:
                    c1 += sh * (sell - buy)
            s1 += n

        hits3, p3 = screen_v3(client, date_fmt)
        if hits3 is not None and not hits3.empty:
            n = len(hits3)
            tc = c3 * 0.3 if n == 1 else c3
            per = tc / n
            for _, row in hits3.iterrows():
                buy = row['close']
                sell = p3.get(row['ts_code'], buy)
                sh = int(per / buy / 100) * 100
                if sh >= 100:
                    c3 += sh * (sell - buy)
            s3 += n

    ret1 = (c1 / INITIAL_CAPITAL - 1) * 100
    ret3 = (c3 / INITIAL_CAPITAL - 1) * 100
    winner = 'V3' if ret3 > ret1 else '原版'
    label = f'{seg[0][4:]}~{seg[-1][4:]}'
    print(f'{si + 1:<4} {label:<22s} {ret1:>+7.2f}% {ret3:>+7.2f}%  {winner:>6s}  {s1:>5} {s3:>5}')

# 统计
print()
orig_seg_rets = []
v3_seg_rets = []
for si, seg in enumerate(segments):
    c1 = float(INITIAL_CAPITAL)
    c3 = float(INITIAL_CAPITAL)
    for date_fmt in seg:
        hits1, p1 = screen_original(client, date_fmt)
        if hits1 is not None and not hits1.empty:
            n = len(hits1)
            tc = c1 * 0.3 if n == 1 else c1
            per = tc / n
            for _, row in hits1.iterrows():
                buy = row['close']
                sell = p1.get(row['ts_code'], buy)
                sh = int(per / buy / 100) * 100
                if sh >= 100:
                    c1 += sh * (sell - buy)
        hits3, p3 = screen_v3(client, date_fmt)
        if hits3 is not None and not hits3.empty:
            n = len(hits3)
            tc = c3 * 0.3 if n == 1 else c3
            per = tc / n
            for _, row in hits3.iterrows():
                buy = row['close']
                sell = p3.get(row['ts_code'], buy)
                sh = int(per / buy / 100) * 100
                if sh >= 100:
                    c3 += sh * (sell - buy)
    orig_seg_rets.append((c1 / INITIAL_CAPITAL - 1) * 100)
    v3_seg_rets.append((c3 / INITIAL_CAPITAL - 1) * 100)

print(f'  {"":<20s} {"原版":>10s} {"V3改进":>10s}')
print(f'  {"平均段收益":<20s} {np.mean(orig_seg_rets):>+9.2f}% {np.mean(v3_seg_rets):>+9.2f}%')
print(f'  {"正段数":<20s} {sum(1 for r in orig_seg_rets if r > 0):>9}/{len(orig_seg_rets)} {sum(1 for r in v3_seg_rets if r > 0):>9}/{len(v3_seg_rets)}')
print(f'  {"最差段":<20s} {min(orig_seg_rets):>+9.2f}% {min(v3_seg_rets):>+9.2f}%')
print(f'  {"最佳段":<20s} {max(orig_seg_rets):>+9.2f}% {max(v3_seg_rets):>+9.2f}%')

# 7月重点对比
print()
print('【7月重点对比】')
july_orig = [r for r in rec_orig if r['date'].startswith('2026-07')]
july_v3 = [r for r in rec_v3 if r['date'].startswith('2026-07')]
for name, jr in [('原版', july_orig), ('V3改进', july_v3)]:
    final = jr[-1]['nav']
    start_nav = jr[0]['nav'] if jr else 0
    ret = (final / start_nav - 1) * 100 if start_nav > 0 else 0
    td = sum(1 for r in jr if r['picks'] > 0)
    wins = sum(1 for r in jr if r['pnl'] > 0)
    losses = sum(1 for r in jr if r['pnl'] < 0)
    wr = f'{wins}/{wins + losses}' if (wins + losses) > 0 else '-'
    print(f'  {name}: 收益{ret:+.2f}%, 交易{td}天, 胜率{wr}')
    for r in jr:
        if r['picks'] > 0:
            sign = '+' if r['pnl'] >= 0 else ''
            d = r['date']
            pk = r['picks']
            pn = r['pnl']
            print(f'    {d} | {pk}只 | {sign}{pn:,.0f}')

print()
print('完成!')
