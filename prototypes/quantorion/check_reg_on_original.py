"""
检查监管过滤对原版策略的影响
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import logging
logging.basicConfig(level=logging.WARNING)
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from config import DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient

START = '20260105'
END = '20260721'
client = TushareClient()
trade_cal = client.pro.trade_cal(exchange='SSE', start_date=START, end_date=END)
trading_days = sorted(trade_cal[trade_cal['is_open'] == 1]['cal_date'].tolist())

all_stocks = client.get_all_stocks()
name_map = dict(zip(all_stocks['ts_code'], all_stocks['name']))


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10


def is_limit_up(pct_chg, code):
    return pct_chg >= (get_limit_pct(code) * 100 * 0.995)


basic_cache = {}


def get_basic_cached(date_fmt):
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


def calc_reg_score(code, prev_date_fmt, daily_data_multi, limit_days):
    score = 0
    dates = sorted(daily_data_multi.keys(), reverse=True)

    if len(dates) >= 1:
        t_row = daily_data_multi[dates[0]][daily_data_multi[dates[0]]['ts_code'] == code]
        if not t_row.empty:
            current = t_row.iloc[0]['close']
            lb = dates[:11]
            if len(lb) >= 2:
                o_row = daily_data_multi[lb[-1]][daily_data_multi[lb[-1]]['ts_code'] == code]
                if not o_row.empty:
                    ret_10d = (current / o_row.iloc[0]['close'] - 1) * 100
                    if ret_10d >= 100:
                        score += 30
                    elif ret_10d >= 70:
                        score += 20
                    elif ret_10d >= 50:
                        score += 10

    basic_info = get_basic_cached(prev_date_fmt)
    if code in basic_info:
        turnover, pe, pe_ttm = basic_info[code]
        if turnover is not None and not np.isnan(turnover):
            if turnover <= 2:
                score += 30
            elif turnover <= 5:
                score += 20
            elif turnover <= 8:
                score += 10

    if limit_days >= 7:
        score += 25
    elif limit_days >= 5:
        score += 20
    elif limit_days >= 3:
        score += 10

    if code in basic_info:
        turnover, pe, pe_ttm = basic_info[code]
        if pe is not None and not np.isnan(pe):
            if pe < 0 or pe >= 200:
                score += 15
            elif pe >= 100:
                score += 10

    return score


print('扫描原版全量信号中的监管高风险股...')
print()

reg_filtered = []
total_signals = 0

for date_fmt in trading_days:
    prev_date = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    recent_days = client.get_recent_trading_days(prev_date, 12)
    if len(recent_days) < 3:
        continue
    daily_data = client.get_multi_day_daily(recent_days[:12])
    if len(daily_data) < 3:
        continue
    dates = sorted(daily_data.keys(), reverse=True)

    df_t = daily_data[dates[0]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_t.columns = ['ts_code', 'close', 'pct_chg', 'vol']
    df_y = daily_data[dates[1]][['ts_code', 'close', 'pct_chg', 'vol']].copy()
    df_y.columns = ['ts_code', 'close_y', 'pct_chg_y', 'vol_y']
    merged = df_t.merge(df_y, on='ts_code', how='inner')

    for i, d in enumerate(dates[2:8], start=2):
        extra = daily_data[d][['ts_code', 'pct_chg', 'vol']].copy()
        extra.columns = ['ts_code', 'pct_chg_' + str(i), 'vol_' + str(i)]
        merged = merged.merge(extra, on='ts_code', how='left')

    merged['is_a'] = merged['ts_code'].apply(
        lambda c: c.split('.')[0][:2] in {'00', '60', '30', '68'}
    )
    merged['lt'] = merged.apply(lambda r: is_limit_up(r['pct_chg'], r['ts_code']), axis=1)
    merged['ly'] = merged.apply(lambda r: is_limit_up(r['pct_chg_y'], r['ts_code']), axis=1)
    merged['vd'] = merged['vol'] < merged['vol_y']
    hits = merged[merged['lt'] & merged['ly'] & merged['vd'] & merged['is_a']].copy()

    if hits.empty:
        continue

    def calc_days(row):
        days = 2
        for i in range(2, 8):
            col = 'pct_chg_' + str(i)
            if col in row and not pd.isna(row[col]) and is_limit_up(row[col], row['ts_code']):
                days += 1
            else:
                break
        return days

    hits['limit_days'] = hits.apply(calc_days, axis=1)
    total_signals += len(hits)

    for _, row in hits.iterrows():
        code = row['ts_code']
        ld = int(row['limit_days'])
        score = calc_reg_score(code, prev_date, daily_data, ld)

        if score >= 60:
            short = code.split('.')[0]
            name = name_map.get(code, short)
            td = date_fmt[:4] + '-' + date_fmt[4:6] + '-' + date_fmt[6:8]

            # 获取该股当日表现
            eod_data = client.get_daily_batch(date_fmt)
            today_close = None
            if not eod_data.empty:
                match = eod_data[eod_data['ts_code'] == code]
                if not match.empty:
                    today_close = match.iloc[0]['close']

            actual_ret = ''
            if today_close is not None:
                ret = (today_close / row['close'] - 1) * 100
                actual_ret = ' actual=' + ('+' if ret >= 0 else '') + str(round(ret, 1)) + '%'

            level = 'RED>=' if score >= 80 else ('ORG60-79' if score >= 60 else '')
            reg_filtered.append({
                'date': td, 'name': name, 'code': short,
                'score': score, 'ld': ld, 'actual_ret': actual_ret
            })
            print(level + ' ' + td + ' ' + name + '(' + short +
                  ') score=' + str(score) + ' ld=' + str(ld) + actual_ret)

print()
print('=' * 50)
print('全期原版总信号: ' + str(total_signals))
print('监管评分>=60: ' + str(len(reg_filtered)) + ' 只')
print('监管评分>=80: ' + str(sum(1 for r in reg_filtered if r['score'] >= 80)) + ' 只')

# 统计这些高危股的表现
if reg_filtered:
    high_risk = [r for r in reg_filtered if r['score'] >= 80]
    print()
    print('>=80分极高风险股详情:')
    for r in high_risk:
        print('  ' + r['date'] + ' ' + r['name'] + '(' + r['code'] + ') ld=' + str(r['ld']) + r['actual_ret'])

print()
print('完成!')
