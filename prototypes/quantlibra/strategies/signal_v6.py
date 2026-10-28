#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V6 双动量轮动 · 持仓信号输出（配合手动跟单）

纯标准库实现，零依赖（不装 numpy/pandas/akshare 也能跑）。
逻辑照搬 allweather_rotation_v6.py：斜率动量 + 效率动量 → 排名 → 冷却期 → 目标持仓。

【数据准备】—— 这是唯一需要你做的事：
  准备一个 CSV，4 只 ETF 的日线收盘价，至少 26 个交易日（推荐 60 个）。
  列名固定为：date,510880,159915,513100,518880
  例（用 Excel / 东方财富 / 券商APP 导出后存成 CSV，UTF-8）：

      date,510880,159915,513100,518880
      2026-07-01,3.123,2.456,4.789,5.678
      2026-07-02,3.156,2.490,4.812,5.701
      ...

【用法】：
  python3 signal_v6.py --tushare --token xxx  # Tushare 数据源（更稳定，需 token + 2000 积分）
  python3 signal_v6.py --fetch                # 腾讯数据源（免费兜底，无需 key）
  python3 signal_v6.py --fetch --commit       # + 记录换仓状态
  python3 signal_v6.py 数据.csv               # 或用本地 CSV 数据算信号
  python3 signal_v6.py 数据.csv --commit      # CSV + 记录换仓状态

  token 也可用环境变量 TUSHARE_TOKEN 传入，避免出现在命令行历史里。

【持仓状态】：
  脚本维护 state.json 记录「当前持仓 + 建仓日期」，用于冷却期判断。
  首次使用请先手动把 state.json 里的 holding 改成你实际持有的 ETF 代码，
  或直接删掉 state.json（视为空仓）。
"""
import csv
import datetime
import json
import math
import os
import subprocess
import sys

ETFS = {
    '510880': '上证红利',
    '159915': '创业板',
    '513100': '纳指100',
    '518880': '黄金',
}
LOOKBACK = 25          # 回看窗口
SLOPE_W = 0.5          # 斜率动量排名权重
EFF_W = 0.5            # 效率动量排名权重
MIN_HOLD_DAYS = 14     # 冷却期（自然日）
FORCE_SELL_RANK = 3    # 强制卖出排名阈值
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state.json')


# ---------------- 信号计算（纯标准库，照搬 V6） ----------------

def linreg_slope_r2(y):
    """对 y 做 x=0..n-1 的最小二乘，返回 (斜率, R²)。"""
    n = len(y)
    x_mean = (n - 1) / 2.0
    y_mean = sum(y) / n
    sxx = sum((i - x_mean) ** 2 for i in range(n))
    sxy = sum((i - x_mean) * (yi - y_mean) for i, yi in enumerate(y))
    syy = sum((yi - y_mean) ** 2 for yi in y)
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    r = sxy / math.sqrt(sxx * syy)
    return slope, r * r  # R² = r²


def calc_slope_momentum(closes):
    """斜率动量 = 斜率 × R²（归一化收盘价做回归）。"""
    y = [c / closes[0] for c in closes]
    slope, r2 = linreg_slope_r2(y)
    return slope * r2


def calc_eff_momentum(closes):
    """效率动量 = 区间涨跌幅 × 效率系数（|位移|/路程）。"""
    ret = closes[-1] / closes[0] - 1.0
    daily = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
    path = sum(abs(d) for d in daily)
    if path < 1e-12:
        return 0.0
    return ret * (abs(ret) / path)


def rank_dict(scores):
    """分数 → 排名（第 1 名 = 最高）。返回 {code: rank}。"""
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return {code: i + 1 for i, (code, _) in enumerate(ranked)}


# ---------------- 数据读取 ----------------

def load_csv(path):
    """读 CSV，返回 {code: [closes...]}，取每列最后 LOOKBACK+1 个值。"""
    rows = []
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as fp:
        for row in csv.DictReader(fp):
            rows.append(row)
    if len(rows) < LOOKBACK + 1:
        raise ValueError('数据不足：需要至少 %d 个交易日，CSV 只有 %d 行' % (LOOKBACK + 1, len(rows)))
    closes = {code: [] for code in ETFS}
    for row in rows:
        for code in ETFS:
            v = row.get(code)
            if v in (None, ''):
                continue
            try:
                closes[code].append(float(v))
            except ValueError:
                continue
    out = {}
    for code, series in closes.items():
        if len(series) < LOOKBACK + 1:
            raise ValueError('ETF %s(%s) 数据不足 %d 天' % (code, ETFS[code], LOOKBACK + 1))
        out[code] = series[-(LOOKBACK + 1):]
    return out, rows[-1].get('date', '?')

# ---------------- 自动拉数据（腾讯行情接口，免费无需 key） ----------------

TENCENT_SECID = {
    '510880': 'sh510880',
    '159915': 'sz159915',
    '513100': 'sh513100',
    '518880': 'sh518880',
}

def fetch_etf_closes(days=60):
    """从腾讯行情接口拉 4 只 ETF 的前复权日线，返回 {code: [closes]} 和最新日期。"""
    end = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=days * 3)).isoformat()
    closes = {}
    latest_date = '?'
    for code, secid in TENCENT_SECID.items():
        url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,%s,%s,%d,qfq'
               % (secid, start, end, days))
        proc = subprocess.run(['curl', '-s', '-m', '20', url], capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout)
            node = data['data'][secid]
            klines = node.get('qfqday') or node.get('day') or []
            series = [float(k[2]) for k in klines]  # k = [date, open, close, high, low, volume]
        except (ValueError, KeyError, json.JSONDecodeError):
            raise RuntimeError('拉取 %s(%s) 失败，响应异常：%s' % (ETFS[code], code, proc.stdout[:200]))
        if len(series) < LOOKBACK + 1:
            raise RuntimeError('ETF %s(%s) 数据不足 %d 天（拉到 %d 天）' % (ETFS[code], code, LOOKBACK + 1, len(series)))
        closes[code] = series[-(LOOKBACK + 1):]
        if klines:
            latest_date = klines[-1][0]
    return closes, latest_date

# ---------------- 自动拉数据（Tushare，需 token + 2000 积分） ----------------

TUSHARE_TSCODE = {
    '510880': '510880.SH',
    '159915': '159915.SZ',
    '513100': '513100.SH',
    '518880': '518880.SH',
}
TUSHARE_URL = 'https://api.tushare.pro'

def fetch_tushare_closes(token, days=60):
    """用 Tushare fund_daily 接口拉 4 只 ETF 的日线收盘价（不复权），返回 {code: [closes]} 和最新日期。"""
    end = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.date.today() - datetime.timedelta(days=days * 3)).strftime('%Y%m%d')
    closes = {}
    latest_date = '?'
    for code, tscode in TUSHARE_TSCODE.items():
        body = json.dumps({
            'api_name': 'fund_daily',
            'token': token,
            'params': {'ts_code': tscode, 'start_date': start, 'end_date': end},
            'fields': 'trade_date,close',
        })
        proc = subprocess.run(['curl', '-s', '-m', '20', '-X', 'POST', TUSHARE_URL,
                               '-H', 'Content-Type: application/json', '-d', body],
                              capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError('Tushare %s(%s) 响应解析失败：%s' % (ETFS[code], code, proc.stdout[:200]))
        if data.get('code') != 0:
            raise RuntimeError('Tushare %s(%s) 拉取失败：%s' % (ETFS[code], code, data.get('msg')))
        items = data['data']['items']  # 倒序 [trade_date, close]
        series = [float(it[1]) for it in reversed(items)]  # 反转为正序（旧→新）
        if len(series) < LOOKBACK + 1:
            raise RuntimeError('ETF %s(%s) 数据不足 %d 天（拉到 %d 天）' % (ETFS[code], code, LOOKBACK + 1, len(series)))
        closes[code] = series[-(LOOKBACK + 1):]
        latest_date = items[0][0]
    return closes, latest_date

# ---------------- 状态管理 ----------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    return {'holding': None, 'entry_date': None}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)

# ---------------- 决策（照搬 V6） ----------------

def decide(closes, state, today):
    slope_m = {c: calc_slope_momentum(closes[c]) for c in ETFS}
    eff_m = {c: calc_eff_momentum(closes[c]) for c in ETFS}
    slope_rank = rank_dict(slope_m)
    eff_rank = rank_dict(eff_m)
    composite = {c: SLOPE_W * slope_rank[c] + EFF_W * eff_rank[c] for c in ETFS}
    best = min(composite, key=composite.get)

    current = state.get('holding')
    entry = state.get('entry_date')

    # 持有天数（统一计算）
    days_held = 0
    if current and entry:
        try:
            days_held = (today - datetime.date.fromisoformat(entry)).days
        except ValueError:
            days_held = 0

    # 决策树（空仓判断不受冷却期限制）
    if eff_m[best] <= 0:
        target = None  # 最强标的都在跌 -> 空仓
    elif current is None:
        target = best
    elif best == current:
        target = current
    else:
        if days_held < MIN_HOLD_DAYS and composite[current] <= FORCE_SELL_RANK:
            target = current  # 冷却期内且排名还行 -> 持有
        else:
            target = best

    return {
        'slope_m': slope_m, 'eff_m': eff_m, 'composite': composite,
        'best': best, 'current': current, 'target': target,
        'days_held': days_held,
    }

# ---------------- 报告输出 ----------------

def fmt_etf(code):
    return '%s %s' % (ETFS.get(code, code), code)

def render(r, data_date):
    L = []
    bar = '═' * 52
    L.append(bar)
    L.append('  V6 双动量轮动 · 今日持仓信号')
    L.append('  数据截至：%s' % data_date)
    L.append(bar)
    L.append('')
    L.append('【动量排名】（第 1 名最强）')
    for i, code in enumerate(sorted(r['composite'], key=lambda c: r['composite'][c]), 1):
        L.append('  #%d  %s   斜率动量 %+.5f   效率动量 %+.5f' % (
            i, fmt_etf(code), r['slope_m'][code], r['eff_m'][code]))
    L.append('')
    L.append('【决策】')
    L.append('  当前持仓：%s' % (fmt_etf(r['current']) if r['current'] else '空仓'))
    L.append('  目标持仓：%s' % (fmt_etf(r['target']) if r['target'] else '空仓'))
    L.append('')
    if r['target'] == r['current']:
        if r['target'] is None:
            L.append('  → 保持空仓（最强标的 %s 效率动量 %.4f ≤ 0）' % (
                fmt_etf(r['best']), r['eff_m'][r['best']]))
        elif r['current'] == r['best']:
            L.append('  → 继续持有 %s（仍是第 1 名）' % fmt_etf(r['current']))
        else:
            L.append('  → 继续持有 %s（冷却期内，已持 %d 天，排名仍在前 %d）' % (
                fmt_etf(r['current']), r['days_held'], FORCE_SELL_RANK))
    elif r['current'] is None:
        L.append('  → 买入！全仓买入 %s' % fmt_etf(r['target']))
    elif r['target'] is None:
        L.append('  → 卖出！清仓 %s，转为空仓' % fmt_etf(r['current']))
    else:
        L.append('  → 换仓！卖出 %s，全仓买入 %s' % (
            fmt_etf(r['current']), fmt_etf(r['target'])))
    L.append('')
    L.append('【操作步骤】')
    if r['target'] == r['current']:
        L.append('  无需操作。')
    else:
        step = 0
        if r['current']:
            step += 1
            L.append('  %d. 在券商 APP 卖出全部 %s（代码 %s）' % (step, ETFS[r['current']], r['current']))
        if r['target']:
            step += 1
            L.append('  %d. 用卖出所得全仓买入 %s（代码 %s）' % (step, ETFS[r['target']], r['target']))
        else:
            step += 1
            L.append('  %d. 资金留在账户，等待下次信号。' % step)
    L.append('')
    L.append(bar)
    L.append('  说明：信号用「截至上一交易日」的收盘价计算，符合 V6 抗未来函数原则。')
    L.append(bar)
    return '\n'.join(L)

# ---------------- 主流程 ----------------

def main(argv):
    commit = '--commit' in argv
    fetch = '--fetch' in argv
    use_tushare = '--tushare' in argv

    if use_tushare:
        token = None
        if '--token' in argv:
            token = argv[argv.index('--token') + 1]
        token = token or os.environ.get('TUSHARE_TOKEN')
        if not token:
            print('缺少 Tushare token：请用 --token xxx 或设置环境变量 TUSHARE_TOKEN')
            return 1
        closes, data_date = fetch_tushare_closes(token)
    elif fetch:
        closes, data_date = fetch_etf_closes()
    else:
        if len(argv) < 2:
            print(__doc__)
            return 1
        closes, data_date = load_csv(argv[1])
    state = load_state()
    today = datetime.date.today()
    r = decide(closes, state, today)

    print(render(r, data_date))

    if commit:
        new_state = {
            'holding': r['target'],
            'entry_date': today.isoformat() if r['target'] and r['target'] != r['current'] else state.get('entry_date'),
        }
        save_state(new_state)
        print('\n[已提交] 持仓状态已更新为 %s（建仓日 %s）\n' % (
            new_state['holding'] or '空仓', new_state['entry_date'] or '无'))
    else:
        print('\n（未加 --commit，本次只显示信号、不改 state.json。确认后加 --commit 记录换仓。）\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
