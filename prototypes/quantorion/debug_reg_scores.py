"""调试监管评分"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import logging
logging.basicConfig(level=logging.WARNING)
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from config import DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient

client = TushareClient()
all_stocks = client.get_all_stocks()
industry_map = dict(zip(all_stocks['ts_code'], all_stocks['industry']))
name_map = dict(zip(all_stocks['ts_code'], all_stocks['name']))


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10


def is_limit_up(pct_chg, code):
    return pct_chg >= (get_limit_pct(code) * 100 * 0.995)


trade_cal = client.pro.trade_cal(exchange='SSE', start_date='20260701', end_date='20260721')
trading_days = sorted(trade_cal[trade_cal['is_open'] == 1]['cal_date'].tolist())

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


def calc_reg_score(code, prev_date_fmt, daily_data_multi, limit_days):
    score = 0
    dates = sorted(daily_data_multi.keys(), reverse=True)
    details = []

    # 10日涨幅
    if len(dates) >= 1:
        today_close = daily_data_multi[dates[0]]
        today_close = today_close[today_close['ts_code'] == code]
        if not today_close.empty:
            current = today_close.iloc[0]['close']
            lookback_dates = dates[:11]
            if len(lookback_dates) >= 2:
                old_date = lookback_dates[-1]
                old_data = daily_data_multi[old_date]
                old_row = old_data[old_data['ts_code'] == code]
                if not old_row.empty:
                    old_close = old_row.iloc[0]['close']
                    ret_10d = (current / old_close - 1) * 100
                    if ret_10d >= 100:
                        score += 30
                        details.append('10D+' + str(int(ret_10d)) + '%:30')
                    elif ret_10d >= 70:
                        score += 20
                        details.append('10D+' + str(int(ret_10d)) + '%:20')
                    elif ret_10d >= 50:
                        score += 10
                        details.append('10D+' + str(int(ret_10d)) + '%:10')

    # 换手率
    basic_info = get_basic_cached(prev_date_fmt)
    if code in basic_info:
        turnover, pe, pe_ttm = basic_info[code]
        if turnover is not None and not np.isnan(turnover):
            if turnover <= 2:
                score += 30
                details.append('T/O' + str(round(turnover, 1)) + '%:30')
            elif turnover <= 5:
                score += 20
                details.append('T/O' + str(round(turnover, 1)) + '%:20')
            elif turnover <= 8:
                score += 10
                details.append('T/O' + str(round(turnover, 1)) + '%:10')

    # 连板
    if limit_days >= 7:
        score += 25
        details.append(str(limit_days) + 'B:25')
    elif limit_days >= 5:
        score += 20
        details.append(str(limit_days) + 'B:20')
    elif limit_days >= 3:
        score += 10
        details.append(str(limit_days) + 'B:10')

    # PE
    if code in basic_info:
        turnover, pe, pe_ttm = basic_info[code]
        if pe is not None and not np.isnan(pe):
            if pe < 0 or pe >= 200:
                score += 15
                details.append('PE' + str(int(pe)) + ':15')
            elif pe >= 100:
                score += 10
                details.append('PE' + str(int(pe)) + ':10')

    return score, details


print('=== 7月监管评分 (V3通过的所有票) ===')
print()

for date_fmt in trading_days:
    td = date_fmt[:4] + '-' + date_fmt[4:6] + '-' + date_fmt[6:8]
    prev_date = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    industry_count, main_themes = detect_main_themes(prev_date)

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

    merged['is_a'] = merged['ts_code'].apply(lambda c: c.split('.')[0][:2] in {'00', '60', '30', '68'})
    merged['lt'] = merged.apply(lambda r: is_limit_up(r['pct_chg'], r['ts_code']), axis=1)
    merged['ly'] = merged.apply(lambda r: is_limit_up(r['pct_chg_y'], r['ts_code']), axis=1)
    merged['vd'] = merged['vol'] < merged['vol_y'] * 0.7
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
    hits['industry'] = hits['ts_code'].map(industry_map).fillna('未知')

    for _, row in hits.iterrows():
        code = row['ts_code']
        ind = row['industry']
        ld = int(row['limit_days'])
        if ind not in main_themes:
            continue
        if ld >= 4:
            continue

        score, details = calc_reg_score(code, prev_date, daily_data, ld)
        short = code.split('.')[0]
        name = name_map.get(code, short)

        risk_icon = '**'
        if score >= 80:
            risk_icon = 'RED'
        elif score >= 60:
            risk_icon = 'ORG'
        elif score >= 40:
            risk_icon = 'YEL'
        else:
            risk_icon = 'GRN'

        print(risk_icon + ' ' + td + ' ' + name + '(' + short + ') score=' + str(score) +
              ' ld=' + str(ld) + ' | ' + ' '.join(details))

print()
print('Done!')
