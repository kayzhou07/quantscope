"""
分析所有交易信号的特征，找出能过滤亏损的特征
"""
import io, sys
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import logging
logging.basicConfig(level=logging.WARNING)
from config import INITIAL_CAPITAL, LIMIT_UP_MIN_DAYS, DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient

START = '20260620'
END = '20260721'
client = TushareClient()
trade_cal = client.pro.trade_cal(exchange='SSE', start_date=START, end_date=END)
trading_days = sorted(trade_cal[trade_cal['is_open'] == 1]['cal_date'].tolist())


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10


all_stocks = client.get_all_stocks()
name_map = dict(zip(all_stocks['ts_code'], all_stocks['name'])) if not all_stocks.empty else {}
trades = []

for date_fmt in trading_days:
    td = f'{date_fmt[:4]}-{date_fmt[4:6]}-{date_fmt[6:8]}'

    prev_date = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    recent_days = client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 5)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        continue
    daily_data = client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 5])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        continue
    dates = sorted(daily_data.keys(), reverse=True)

    df_t = daily_data[dates[0]][['ts_code', 'close', 'pct_chg', 'vol', 'amount']].copy()
    df_t.columns = ['ts_code', 'close', 'pct_chg', 'vol', 'amount']
    df_y = daily_data[dates[1]][['ts_code', 'close', 'pct_chg', 'vol', 'amount']].copy()
    df_y.columns = ['ts_code', 'close_y', 'pct_chg_y', 'vol_y', 'amount_y']

    merged = df_t.merge(df_y, on='ts_code', how='inner')

    if len(dates) >= 3:
        df_y2 = daily_data[dates[2]][['ts_code', 'pct_chg', 'vol']].copy()
        df_y2.columns = ['ts_code', 'pct_chg_y2', 'vol_y2']
        merged = merged.merge(df_y2, on='ts_code', how='left')
    else:
        merged['pct_chg_y2'] = np.nan
        merged['vol_y2'] = np.nan

    if len(dates) >= 4:
        df_y3 = daily_data[dates[3]][['ts_code', 'pct_chg', 'vol']].copy()
        df_y3.columns = ['ts_code', 'pct_chg_y3', 'vol_y3']
        merged = merged.merge(df_y3, on='ts_code', how='left')
    else:
        merged['pct_chg_y3'] = np.nan
        merged['vol_y3'] = np.nan

    merged['is_a'] = merged['ts_code'].apply(lambda c: c.split('.')[0][:2] in {'00', '60', '30', '68'})

    def is_limit(row, col='pct_chg'):
        return row[col] >= (get_limit_pct(row['ts_code']) * 100 * 0.995)

    merged['lt'] = merged.apply(lambda r: is_limit(r, 'pct_chg'), axis=1)
    merged['ly'] = merged.apply(lambda r: is_limit(r, 'pct_chg_y'), axis=1)
    merged['vd'] = merged['vol'] < merged['vol_y']
    hits = merged[merged['lt'] & merged['ly'] & merged['vd'] & merged['is_a']]

    today_data = client.get_daily_batch(date_fmt)
    today_info = dict(zip(today_data['ts_code'], zip(today_data['open'], today_data['close'])))

    if hits.empty:
        continue

    for _, row in hits.iterrows():
        code = row['ts_code']
        if code not in today_info:
            continue
        today_open, today_close = today_info[code]
        prev_close = row['close']
        ret = (today_close / prev_close - 1) * 100

        vol_ratio = row['vol'] / row['vol_y'] if row['vol_y'] > 0 else 1
        pct_t = row['pct_chg']

        limit_days = 2
        if not pd.isna(row.get('pct_chg_y2')) and row['pct_chg_y2'] >= (get_limit_pct(code) * 100 * 0.995):
            limit_days = 3
        if not pd.isna(row.get('pct_chg_y3')) and row['pct_chg_y3'] >= (get_limit_pct(code) * 100 * 0.995):
            limit_days = 4

        vol_shrink_days = 0
        if row['vol'] < row['vol_y']:
            vol_shrink_days = 1
            if not pd.isna(row.get('vol_y2')) and row['vol_y'] < row['vol_y2']:
                vol_shrink_days = 2
                if not pd.isna(row.get('vol_y3')) and row['vol_y2'] < row['vol_y3']:
                    vol_shrink_days = 3

        short = code.split('.')[0]
        board = short[:2]

        trades.append({
            'date': td, 'code': short, 'name': name_map.get(code, short), 'board': board,
            'prev_close': prev_close, 'today_close': today_close, 'ret': ret,
            'vol_ratio': round(vol_ratio, 3), 'pct_t': pct_t,
            'limit_days': limit_days, 'vol_shrink_days': vol_shrink_days,
        })

print(f'总交易信号: {len(trades)} 笔\n')


def show_dim(label, groups):
    print(f'【{label}】')
    for name, sub in groups:
        if not sub:
            continue
        avg = np.mean([t['ret'] for t in sub])
        win = sum(1 for t in sub if t['ret'] > 0)
        print(f'  {name}: {len(sub)}笔, 平均{avg:+.1f}%, 胜率{win}/{len(sub)}')
    print()


# 1. 连板天数
show_dim('维度1: 连板天数', [
    (f'连板{d}天', [t for t in trades if t['limit_days'] == d])
    for d in sorted(set(t['limit_days'] for t in trades))
])

# 2. 缩量程度
show_dim('维度2: 缩量程度', [
    (f'{lo}-{hi}', [t for t in trades if lo <= t['vol_ratio'] < hi])
    for lo, hi in [(0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.0)]
])

# 3. 板块
show_dim('维度3: 板块', [
    (f'{b}开头', [t for t in trades if t['board'] == b])
    for b in sorted(set(t['board'] for t in trades))
])

# 4. 缩量天数
show_dim('维度4: 连续缩量天数', [
    (f'{d}天', [t for t in trades if t['vol_shrink_days'] == d])
    for d in sorted(set(t['vol_shrink_days'] for t in trades))
])

# 5. 股价
show_dim('维度5: 股价区间', [
    (f'<10元', [t for t in trades if t['prev_close'] < 10]),
    (f'10-30元', [t for t in trades if 10 <= t['prev_close'] < 30]),
    (f'30-100元', [t for t in trades if 30 <= t['prev_close'] < 100]),
    (f'>100元', [t for t in trades if t['prev_close'] >= 100]),
])

# 6. 涨停强度
show_dim('维度6: 前日涨停强度', [
    (f'>9.95%(硬板)', [t for t in trades if t['pct_t'] >= 9.95]),
    (f'9.0-9.95%', [t for t in trades if 9.0 <= t['pct_t'] < 9.95]),
    (f'<9%(弱板)', [t for t in trades if t['pct_t'] < 9.0]),
])

# === 综合方案 ===
print('=' * 60)
print('【综合】潜在改进方案 vs 原始回测')
print('=' * 60)

schemes = [
    ('原始(全量)', lambda t: True),
    ('缩量<0.8', lambda t: t['vol_ratio'] < 0.8),
    ('缩量<0.7', lambda t: t['vol_ratio'] < 0.7),
    ('仅2连板', lambda t: t['limit_days'] == 2),
    ('缩量<0.8 + 2连板', lambda t: t['vol_ratio'] < 0.8 and t['limit_days'] == 2),
    ('缩量<0.7 + 2连板', lambda t: t['vol_ratio'] < 0.7 and t['limit_days'] == 2),
    ('股价<20元', lambda t: t['prev_close'] < 20),
    ('缩量<0.8 + 股价<20', lambda t: t['vol_ratio'] < 0.8 and t['prev_close'] < 20),
    ('缩量<0.8 + 2连板 + 股价<30', lambda t: t['vol_ratio'] < 0.8 and t['limit_days'] == 2 and t['prev_close'] < 30),
    ('缩量<0.6', lambda t: t['vol_ratio'] < 0.6),
    ('缩量<0.7 + 股价<20', lambda t: t['vol_ratio'] < 0.7 and t['prev_close'] < 20),
]

for name, fn in schemes:
    sub = [t for t in trades if fn(t)]
    if not sub:
        print(f'  {name}: 0笔')
        continue
    avg = np.mean([t['ret'] for t in sub])
    win = sum(1 for t in sub if t['ret'] > 0)
    total = sum(t['ret'] for t in sub)
    # 估算复利效果
    est_ret = total  # 简单累加作为参考
    print(f'  {name:<30s}: {len(sub):>3}笔, 均收益{avg:>+6.1f}%, 累计{total:>+7.1f}%, 胜率{win}/{len(sub)}')
