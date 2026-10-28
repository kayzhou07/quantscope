"""
动态切换回测：根据市场环境自动选择原版或V3
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

all_stocks = client.get_all_stocks()
industry_map = dict(zip(all_stocks['ts_code'], all_stocks['industry']))


def get_limit_pct(code):
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10


def is_limit_up(pct_chg, code):
    return pct_chg >= (get_limit_pct(code) * 100 * 0.995)


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


# ═══════════════════════════════════════════
# 市场环境评分
# ═══════════════════════════════════════════
def calc_market_health(date_fmt):
    """
    计算市场健康度评分 (0-100)
    使用 date_fmt 当天收盘数据判断市场状态
    分数越高 = 市场越好 → 偏向原版
    分数越低 = 市场越差 → 偏向V3
    """
    score = 0
    daily = client.get_daily_batch(date_fmt)
    if daily.empty:
        return 50  # 无数据默认中性

    # ── 1. 涨跌比 (0-30分) ──
    up = int((daily['pct_chg'] > 0).sum())
    down = int((daily['pct_chg'] < 0).sum())
    total = up + down
    if total > 0:
        up_ratio = up / total
        if up_ratio >= 0.7:
            score += 30
        elif up_ratio >= 0.55:
            score += 20
        elif up_ratio >= 0.4:
            score += 10
        else:
            score += 0  # 普跌

    # ── 2. 涨停家数 (0-25分) ──
    limit_up_count = int((daily['pct_chg'] >= 9.9).sum())
    if limit_up_count >= 80:
        score += 25
    elif limit_up_count >= 50:
        score += 20
    elif limit_up_count >= 30:
        score += 10
    else:
        score += 0

    # ── 3. 指数趋势 (0-25分) ──
    # 用上证指数近似
    try:
        sh = daily[daily['ts_code'] == '000001.SH']
        if not sh.empty:
            sh_pct = sh.iloc[0]['pct_chg']
            if sh_pct > 1.0:
                score += 25
            elif sh_pct > 0.3:
                score += 15
            elif sh_pct > -0.3:
                score += 10
            elif sh_pct > -1.0:
                score += 5
            else:
                score += 0
    except Exception:
        score += 10

    # ── 4. 连板环境 (0-20分) ──
    # 涨停股次日平均表现(粗略: 检查涨停股有多少次日继续涨)
    # 简化: 用涨停家数是否在增加来判断投机热度
    # 这里用limit_up_count本身 >50 就说明投机活跃
    if limit_up_count >= 60:
        score += 20
    elif limit_up_count >= 40:
        score += 15
    elif limit_up_count >= 20:
        score += 10
    else:
        score += 0

    return score


# 缓存市场健康度分数
_health_cache = {}

def get_smoothed_health(date_fmt, window=5):
    """
    获取平滑后的市场健康度 (N日均值)
    避免单日波动导致误切换
    """
    if date_fmt in _health_cache:
        return _health_cache[date_fmt]

    # 获取 date_fmt 及之前N-1个交易日
    recent = client.get_recent_trading_days(date_fmt, window)
    scores = []
    for d in recent[:window]:
        if d not in _health_cache:
            _health_cache[d] = calc_market_health(d)
        scores.append(_health_cache[d])

    if not scores:
        return 50

    # 加权: 最近一天权重更高
    if len(scores) >= 3:
        weights = [0.4, 0.25, 0.15] + [0.2 / (len(scores) - 3)] * (len(scores) - 3)
        weights = weights[:len(scores)]
        # 归一化
        total_w = sum(weights)
        weights = [w / total_w for w in weights]
        smoothed = sum(s * w for s, w in zip(scores, weights))
    else:
        smoothed = np.mean(scores)

    return smoothed


def get_strategy_mode(date_fmt):
    """
    根据平滑市场健康度决定策略模式
    三级切换:
    - health >= 55: 原版(激进)
    - health 40-54: 原版+缩量过滤(温和)
    - health < 40: V3(防御)
    """
    score = get_smoothed_health(date_fmt, window=10)

    if score >= 55:
        return 'original', round(score, 1)
    elif score >= 40:
        return 'hybrid', round(score, 1)
    else:
        return 'v3', round(score, 1)


# ═══════════════════════════════════════════
# 原版选股
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
# V3选股
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
        extra.columns = ['ts_code', 'pct_chg_' + str(i), 'vol_' + str(i)]
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
            col = 'pct_chg_' + str(i)
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
# 混合版 (原版+缩量0.8过滤, 温和模式)
# ═══════════════════════════════════════════
def screen_hybrid(client, date_fmt):
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
    merged['vd'] = merged['vol'] < merged['vol_y'] * 0.8  # 温和缩量过滤
    hits = merged[merged['lt'] & merged['ly'] & merged['vd'] & merged['is_a']]
    eod_data = client.get_daily_batch(date_fmt)
    eod_prices = dict(zip(eod_data['ts_code'], eod_data['close'])) if not eod_data.empty else {}
    return hits, eod_prices


# ═══════════════════════════════════════════
# 动态切换回测
# ═══════════════════════════════════════════
def run_dynamic_backtest():
    cash = float(INITIAL_CAPITAL)
    records = []
    mode_log = []

    for date_fmt in trading_days:
        td = date_fmt[:4] + '-' + date_fmt[4:6] + '-' + date_fmt[6:8]

        # 用前一日收盘数据判断市场环境
        prev_trade_day = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
        # 找到前一个实际交易日
        recent_td = client.get_recent_trading_days(prev_trade_day, 2)
        if len(recent_td) >= 1:
            prev_close_date = recent_td[0]
        else:
            prev_close_date = prev_trade_day

        mode, health = get_strategy_mode(prev_close_date)

        if mode == 'original':
            hits, prices = screen_original(client, date_fmt)
        elif mode == 'hybrid':
            hits, prices = screen_hybrid(client, date_fmt)
        else:
            hits, prices = screen_v3(client, date_fmt)

        if hits is None or hits.empty:
            records.append({
                'date': td, 'nav': cash, 'picks': 0, 'pnl': 0,
                'mode': mode, 'health': health
            })
            continue

        n = len(hits)
        trade_cash = cash * 0.3 if n == 1 else cash
        per = trade_cash / n
        day_pnl = 0
        for _, row in hits.iterrows():
            buy = row['close']
            sell = prices.get(row['ts_code'], buy)
            shares = int(per / buy / 100) * 100
            if shares >= 100:
                day_pnl += shares * (sell - buy)
        cash += day_pnl

        records.append({
            'date': td, 'nav': round(cash, 2), 'picks': n,
            'pnl': round(day_pnl, 2), 'mode': mode, 'health': health
        })

        if day_pnl != 0:
            mode_log.append((td, mode, health, n, day_pnl))

    return records, mode_log


print('动态切换回测...')
print()

rec_dyn, mode_log = run_dynamic_backtest()

# 也跑纯原版和纯V3做对比
rec_orig_records = []
cash = float(INITIAL_CAPITAL)
for date_fmt in trading_days:
    td = date_fmt[:4] + '-' + date_fmt[4:6] + '-' + date_fmt[6:8]
    hits, prices = screen_original(client, date_fmt)
    if hits is None or hits.empty:
        rec_orig_records.append({'date': td, 'nav': cash, 'picks': 0, 'pnl': 0})
        continue
    n = len(hits)
    tc = cash * 0.3 if n == 1 else cash
    per = tc / n
    day_pnl = 0
    for _, row in hits.iterrows():
        buy = row['close']
        sell = prices.get(row['ts_code'], buy)
        sh = int(per / buy / 100) * 100
        if sh >= 100:
            day_pnl += sh * (sell - buy)
    cash += day_pnl
    rec_orig_records.append({'date': td, 'nav': round(cash, 2), 'picks': n, 'pnl': round(day_pnl, 2)})

rec_v3_records = []
cash = float(INITIAL_CAPITAL)
for date_fmt in trading_days:
    td = date_fmt[:4] + '-' + date_fmt[4:6] + '-' + date_fmt[6:8]
    hits, prices = screen_v3(client, date_fmt)
    if hits is None or hits.empty:
        rec_v3_records.append({'date': td, 'nav': cash, 'picks': 0, 'pnl': 0})
        continue
    n = len(hits)
    tc = cash * 0.3 if n == 1 else cash
    per = tc / n
    day_pnl = 0
    for _, row in hits.iterrows():
        buy = row['close']
        sell = prices.get(row['ts_code'], buy)
        sh = int(per / buy / 100) * 100
        if sh >= 100:
            day_pnl += sh * (sell - buy)
    cash += day_pnl
    rec_v3_records.append({'date': td, 'nav': round(cash, 2), 'picks': n, 'pnl': round(day_pnl, 2)})

# ── 汇总 ──
print('=' * 65)
print('  动态切换 vs 原版 vs V3 回测对比')
print('=' * 65)
print()

for name, rec in [('原版', rec_orig_records), ('V3', rec_v3_records), ('动态切换', rec_dyn)]:
    final = rec[-1]['nav']
    ret = (final / INITIAL_CAPITAL - 1) * 100
    td_count = sum(1 for r in rec if r['picks'] > 0)
    sig = sum(r['picks'] for r in rec)
    wins = sum(1 for r in rec if r['pnl'] > 0)
    losses = sum(1 for r in rec if r['pnl'] < 0)
    wr = str(wins) + '/' + str(wins + losses) if (wins + losses) > 0 else '-'
    peak = INITIAL_CAPITAL
    max_dd = 0
    for r in rec:
        if r['nav'] > peak:
            peak = r['nav']
        dd = (r['nav'] / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    print(name + ': ' + str(final) + ' | ' + ('+' if ret >= 0 else '') +
          str(round(ret, 2)) + '% | 交易' + str(td_count) + '天 | 信号' +
          str(sig) + '只 | 胜' + wr + ' | 回撤' + str(round(max_dd, 1)) + '%')

# ── 模式切换日志 ──
print()
print('【切换日志】')
orig_days = sum(1 for r in rec_dyn if r.get('mode') == 'original')
hyb_days = sum(1 for r in rec_dyn if r.get('mode') == 'hybrid')
v3_days = sum(1 for r in rec_dyn if r.get('mode') == 'v3')
print('原版: ' + str(orig_days) + '天 | 混合: ' + str(hyb_days) + '天 | V3: ' + str(v3_days) + '天')

# 按月统计切换
print()
print('【月度模式分布】')
monthly_modes = {}
for r in rec_dyn:
    m = r['date'][:7]
    if m not in monthly_modes:
        monthly_modes[m] = {'orig': 0, 'hyb': 0, 'v3': 0, 'pnl': 0}
    mode = r.get('mode', 'original')
    monthly_modes[m][mode] = monthly_modes[m].get(mode, 0) + 1
    monthly_modes[m]['pnl'] += r.get('pnl', 0)

for m in sorted(monthly_modes.keys()):
    mm = monthly_modes[m]
    print('  ' + m + ': 原版' + str(mm['orig']) + ' 混合' + str(mm['hyb']) +
          ' V3' + str(mm['v3']) + ' | 盈亏' +
          ('+' if mm['pnl'] >= 0 else '') + str(round(mm['pnl'], 0)))

# ── 10日分段 ──
SEG_LEN = 10
segments = [trading_days[i:i + SEG_LEN] for i in range(0, len(trading_days), SEG_LEN)]

print()
print('【' + str(SEG_LEN) + '日分段（每段50万起）】')
print('段    区间                   原版        V3      动态     胜负')

for si, seg in enumerate(segments):
    results = []
    for fn in [screen_original, screen_v3]:
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

    # 动态版
    c = float(INITIAL_CAPITAL)
    for date_fmt in seg:
        prev_trade_day = (datetime.strptime(date_fmt, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
        recent_td = client.get_recent_trading_days(prev_trade_day, 2)
        prev_close_date = recent_td[0] if len(recent_td) >= 1 else prev_trade_day
        mode, health = get_strategy_mode(prev_close_date)
        if mode == 'original':
            fn = screen_original
        elif mode == 'hybrid':
            fn = screen_hybrid
        else:
            fn = screen_v3
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
    w = '原版' if r1 == best else ('V3' if r2 == best else '动态')
    label = seg[0][4:] + '~' + seg[-1][4:]
    r1s = ('+' if r1 >= 0 else '') + str(round(r1, 2)) + '%'
    r2s = ('+' if r2 >= 0 else '') + str(round(r2, 2)) + '%'
    r3s = ('+' if r3 >= 0 else '') + str(round(r3, 2)) + '%'
    print(str(si + 1).ljust(4) + ' ' + label.ljust(22) + ' ' +
          r1s.rjust(8) + ' ' + r2s.rjust(8) + ' ' + r3s.rjust(8) + '  ' + w)

print()
print('Done!')
