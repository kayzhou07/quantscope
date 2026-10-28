#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略信号逻辑：V6 双动量轮动 + S123 三策略组合（S1 轮动 + S2 红利）。
纯标准库实现，数据经 Tushare HTTP API（curl）获取。

V6（照搬 allweather_rotation_v6.py）：斜率动量 + 效率动量 → 排名 → 选第一。
S123（照搬 3万ETF版_S123组合版.py 的 S1/S2）：S1 年化收益×R² 轮动 + S2 波动率倒数红利配比。
"""
import datetime
import json
import math
import os
import urllib.request

TUSHARE_URL = 'https://api.tushare.pro'


def _post_tushare(payload, timeout=30):
    """POST JSON 到 Tushare，返回解析后的 dict（用 urllib，跨平台无 curl 依赖）。"""
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        TUSHARE_URL, data=body,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _get_json(url, timeout=20):
    """GET 返回解析后的 dict。"""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _post_raw(body_str, timeout=30):
    """POST JSON 字符串到 Tushare，返回解析后的 dict。"""
    req = urllib.request.Request(
        TUSHARE_URL, data=body_str.encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

# ---- ETF 池定义 ---------------------------------------------------------
V6_ETFS = {
    '510880': ('510880.SH', '上证红利'),
    '159915': ('159915.SZ', '创业板'),
    '513100': ('513100.SH', '纳指100'),
    '518880': ('518880.SH', '黄金'),
}
S1_ETFS = {
    '518880': ('518880.SH', '黄金'),
    '513100': ('513100.SH', '纳指100'),
    '159915': ('159915.SZ', '创业板'),
    '511220': ('511220.SH', '城投债'),
}
S2_ETFS = {
    '515080': ('515080.SH', '中证红利'),
    '510880': ('510880.SH', '上证红利'),
    '512890': ('512890.SH', '红利低波'),
    '515450': ('515450.SH', '红利低波50'),
}
S2_TARGET_RATIO = {'515080': 0.25, '510880': 0.20, '512890': 0.30, '515450': 0.25}
# S3 QDII 套利候选池（跨境 ETF，折溢价回归）
S3_ETFS = {
    '513100': ('513100.SH', '纳指ETF'),
    '513500': ('513500.SH', '标普500'),
    '513520': ('513520.SH', '日经ETF'),
    '513030': ('513030.SH', '德国30'),
    '159920': ('159920.SZ', '恒生ETF'),
    '513880': ('513880.SH', '日经225'),
}


def get_token(argv_token=None):
    """token 优先级：显式参数 > 环境变量 TUSHARE_TOKEN。"""
    return argv_token or os.environ.get('TUSHARE_TOKEN')


# ---- 数据获取（Tushare，curl 直调） --------------------------------------

def fetch_tushare(etf_pool, token, days=90):
    """拉取一组 ETF 的日线收盘价，返回 {code: [closes...]}（正序，旧→新）和最新日期。"""
    if not token:
        raise RuntimeError('缺少 Tushare token：请设置环境变量 TUSHARE_TOKEN')
    end = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.date.today() - datetime.timedelta(days=days * 3)).strftime('%Y%m%d')
    result = {}
    latest_date = '?'
    for code, (tscode, _name) in etf_pool.items():
        body = json.dumps({
            'api_name': 'fund_daily',
            'token': token,
            'params': {'ts_code': tscode, 'start_date': start, 'end_date': end},
            'fields': 'trade_date,close',
        })
        try:
            data = _post_raw(body)
        except Exception as e:
            raise RuntimeError('Tushare %s 拉取失败：%s' % (tscode, e))
        if data.get('code') != 0:
            raise RuntimeError('Tushare %s 拉取失败：%s' % (tscode, data.get('msg')))
        items = data['data']['items']  # 倒序 [trade_date, close]
        series = [float(it[1]) for it in reversed(items)]
        result[code] = series
        latest_date = items[0][0]
    return result, latest_date


# ---- 数据获取（Tushare，curl 直调） --------------------------------------

def fetch_nav(etf_pool, token):
    """拉 QDII ETF 的最新单位净值，返回 {code: (nav_date, unit_nav)}。"""
    result = {}
    for code, (tscode, _name) in etf_pool.items():
        body = json.dumps({
            'api_name': 'fund_nav',
            'token': token,
            'params': {'ts_code': tscode},
            'fields': 'nav_date,unit_nav',
        })
        try:
            data = _post_raw(body)
        except Exception:
            continue
        if data.get('code') != 0:
            continue
        items = data['data']['items']  # 倒序 [nav_date, unit_nav]
        if items:
            result[code] = (items[0][0], float(items[0][1]))
    return result


def fetch_close_on(tscode, date, token):
    """拉某 ETF 在指定交易日的收盘价，取不到返回 None。"""
    body = json.dumps({
        'api_name': 'fund_daily',
        'token': token,
        'params': {'ts_code': tscode, 'start_date': date, 'end_date': date},
        'fields': 'trade_date,close',
    })
    try:
        data = _post_raw(body)
    except Exception:
        return None
    if data.get('code') != 0:
        return None
    items = data['data']['items']
    return float(items[0][1]) if items else None


# ---- 通用工具 -----------------------------------------------------------

def linreg_slope_r2(y):
    """普通最小二乘，返回 (斜率, R²)。"""
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
    return slope, r * r


def wlinreg_annualized_r2(y):
    """S1 的加权回归（近端权重更大）：返回 (年化收益, R²)。照搬 S1 源码。"""
    n = len(y)
    x = list(range(n))
    weights = [1.0 + i / (n - 1) for i in range(n)]  # linspace(1,2,n)
    W = [w * w for w in weights]                       # W = weights²
    W_sum = sum(W)
    x_bar = sum(W[i] * x[i] for i in range(n)) / W_sum
    y_bar = sum(W[i] * y[i] for i in range(n)) / W_sum
    var_x = sum(W[i] * (x[i] - x_bar) ** 2 for i in range(n))
    if var_x == 0:
        return 0.0, 0.0
    slope = sum(W[i] * (x[i] - x_bar) * (y[i] - y_bar) for i in range(n)) / var_x
    annualized = math.exp(slope * 250) - 1.0
    y_pred = [slope * x[i] + (y_bar - slope * x_bar) for i in range(n)]
    ss_res = sum(weights[i] * (y[i] - y_pred[i]) ** 2 for i in range(n))
    y_mean = sum(y) / n
    ss_tot = sum(weights[i] * (y[i] - y_mean) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return annualized, r2


def rank_dict(scores):
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return {code: i + 1 for i, (code, _) in enumerate(ranked)}


def _last_n(series, n):
    return series[-n:]


# ---- V6 双动量轮动 ------------------------------------------------------

def v6_signal(closes, pool=V6_ETFS, lookback=25):
    """V6：斜率动量 + 效率动量，各排名后等权合成，选第一。"""
    slope_m = {}
    eff_m = {}
    for code in pool:
        s = _last_n(closes[code], lookback + 1)
        y = [c / s[0] for c in s]
        slope, r2 = linreg_slope_r2(y)
        slope_m[code] = slope * r2
        ret = s[-1] / s[0] - 1.0
        daily = [s[i] / s[i - 1] - 1.0 for i in range(1, len(s))]
        path = sum(abs(d) for d in daily)
        eff_m[code] = ret * (abs(ret) / path) if path > 1e-12 else 0.0

    slope_rank = rank_dict(slope_m)
    eff_rank = rank_dict(eff_m)
    composite = {c: 0.5 * slope_rank[c] + 0.5 * eff_rank[c] for c in pool}
    best = min(composite, key=composite.get)

    ranking = [{
        'code': c, 'name': pool[c][1],
        'slope': round(slope_m[c], 5), 'eff': round(eff_m[c], 5),
        'rank': composite[c],
    } for c in sorted(composite, key=composite.get)]

    # 空仓判断：最强标的效率动量 <= 0
    target = best if eff_m[best] > 0 else None
    decision = '买入 ' + pool[target][1] if target else '空仓（最强标的效率动量 ≤ 0，全池走弱）'
    return {
        'strategy': 'v6',
        'ranking': ranking,
        'target': target,
        'target_name': pool[target][1] if target else '空仓',
        'decision': decision,
        'best_eff': round(eff_m[best], 5),
    }


# ---- S123 三策略组合 ----------------------------------------------------

def s1_signal(closes, pool=S1_ETFS, lookback=25, short_lb=10):
    """S1：短动量过滤 + 年化收益×R² 加权回归，取第一。"""
    scores = {}
    for code in pool:
        s = _last_n(closes[code], lookback + 1)
        if len(s) < lookback + 1:
            continue
        # 短动量过滤：10 天年化收益 < 0 剔除
        short_ret = s[-1] / s[-(short_lb + 1)] - 1.0 if len(s) >= short_lb + 1 else 0.0
        short_ann = (1 + short_ret) ** (250 / short_lb) - 1.0
        if short_ann < 0:
            continue
        log_price = [math.log(p) for p in s]
        annualized, r2 = wlinreg_annualized_r2(log_price)
        scores[code] = annualized * r2

    if not scores:
        return {'ranking': [], 'target': None, 'target_name': '空仓', 'note': '全部标的短动量过滤剔除'}

    ranked = rank_dict(scores)
    best = min(ranked, key=ranked.get)
    ranking = [{
        'code': c, 'name': pool[c][1], 'score': round(scores[c], 5), 'rank': ranked[c],
    } for c in sorted(ranked, key=ranked.get)]
    return {
        'ranking': ranking,
        'target': best,
        'target_name': pool[best][1],
        'score': round(scores[best], 5),
    }


def s2_signal(closes, pool=S2_ETFS, vol_lb=60):
    """S2：红利配置，权重 ∝ 目标比例 / 波动率（低波动多配）。"""
    inv_vol = {}
    for code in pool:
        s = _last_n(closes[code], vol_lb + 1)
        if len(s) > 20:
            daily = [s[i] / s[i - 1] - 1.0 for i in range(1, len(s))]
            mean = sum(daily) / len(daily)
            var = sum((d - mean) ** 2 for d in daily) / len(daily)
            vol = math.sqrt(var)
        else:
            vol = 0.02
        inv_vol[code] = S2_TARGET_RATIO[code] / max(vol, 1e-6)
    total = sum(inv_vol.values())
    weights = [{
        'code': c, 'name': pool[c][1],
        'weight': round(inv_vol[c] / total, 4),
        'target_ratio': S2_TARGET_RATIO[c],
    } for c in pool]
    weights.sort(key=lambda w: -w['weight'])
    return {'weights': weights}


def s3_signal(token, pool=S3_ETFS):
    """S3：QDII 折溢价 = 价格 / 净值 - 1，输出折价排序（折价 < 0 为买入机会）。"""
    nav_data = fetch_nav(pool, token)
    results = []
    for code, (tscode, name) in pool.items():
        if code not in nav_data:
            continue
        nav_date, unit_nav = nav_data[code]
        price = fetch_close_on(tscode, nav_date, token)
        if price is None:
            continue
        premium = price / unit_nav - 1.0
        results.append({
            'code': code, 'name': name,
            'nav_date': nav_date, 'unit_nav': round(unit_nav, 4),
            'price': round(price, 4), 'premium': round(premium, 4),
        })
    results.sort(key=lambda x: x['premium'])
    return results


def s123_signal(closes, token=None):
    """S123 组合信号：S1 轮动 + S2 红利 + S3 折溢价套利。"""
    s3 = s3_signal(token, S3_ETFS) if token else []
    return {
        'strategy': 's123',
        's1': s1_signal(closes, S1_ETFS),
        's2': s2_signal(closes, S2_ETFS),
        's3': s3,
        'alloc': {'S1轮动': '50%', 'S2红利': '30%', 'S3套利': '20%'},
    }


# ---- 历史数据获取 + 回测 ------------------------------------------------

def fetch_history(etf_pool, token, start='20190101'):
    """拉长历史日线（正序），返回 ({code: [closes]}, dates)。dates 取第一只 ETF 的交易日。"""
    end = datetime.date.today().strftime('%Y%m%d')
    result = {}
    dates = []
    for code, (tscode, _name) in etf_pool.items():
        body = json.dumps({
            'api_name': 'fund_daily',
            'token': token,
            'params': {'ts_code': tscode, 'start_date': start, 'end_date': end},
            'fields': 'trade_date,close',
        })
        try:
            data = _post_raw(body)
        except Exception:
            raise RuntimeError('Tushare %s 历史数据解析失败' % tscode)
        if data.get('code') != 0:
            raise RuntimeError('Tushare %s 历史数据拉取失败：%s' % (tscode, data.get('msg')))
        items = data['data']['items']  # 倒序 [trade_date, close]
        items.reverse()
        result[code] = [float(it[1]) for it in items]
        if not dates:
            dates = [it[0] for it in items]
    return result, dates


def v6_backtest(closes, lookback=25, step=5, cost=0.003, min_hold=10, force_rank=3):
    """V6 周频回测（含换仓成本 + 冷却期），返回累计净值列表。"""
    codes = list(closes.keys())
    n = min(len(closes[c]) for c in codes)
    nav = 1.0
    navs = []
    holding = None
    entry_idx = -min_hold
    for i in range(lookback, n):
        if (i - lookback) % step == 0:
            sub = {c: closes[c][i - lookback:i + 1] for c in codes}
            sig = v6_signal(sub)
            best = sig['target']
            composite = {r['code']: r['rank'] for r in sig['ranking']}
            if holding is None:
                new_holding = best
            elif best == holding:
                new_holding = holding
            elif (i - entry_idx) < min_hold and composite.get(holding, 99) <= force_rank:
                new_holding = holding  # 冷却期内且排名还行 → 持有
            else:
                new_holding = best
            if new_holding != holding:
                nav *= (1 - cost)  # 换仓成本
                entry_idx = i
            holding = new_holding
        if holding and i > 0:
            nav *= closes[holding][i] / closes[holding][i - 1]
        navs.append(round(nav, 6))
    return navs


def s1_backtest(closes, lookback=25, step=5, cost=0.003):
    """S1 轮动回测（含换仓成本），返回累计净值列表。"""
    codes = list(closes.keys())
    n = min(len(closes[c]) for c in codes)
    nav = 1.0
    navs = []
    holding = None
    for i in range(lookback, n):
        if (i - lookback) % step == 0:
            sub = {c: closes[c][i - lookback:i + 1] for c in codes}
            new_holding = s1_signal(sub, S1_ETFS)['target']
            if new_holding != holding:
                nav *= (1 - cost)  # 换仓成本
            holding = new_holding
        if holding and i > 0:
            nav *= closes[holding][i] / closes[holding][i - 1]
        navs.append(round(nav, 6))
    return navs


def _s2_weights(closes, i, vol_lb=60):
    """S2 在时点 i 的波动率倒数加权。"""
    codes = list(closes.keys())
    inv_vol = {}
    for c in codes:
        s = closes[c][i - vol_lb:i + 1]
        daily = [s[k] / s[k - 1] - 1 for k in range(1, len(s))]
        mean = sum(daily) / len(daily)
        var = sum((d - mean) ** 2 for d in daily) / len(daily)
        vol = math.sqrt(var) if var > 0 else 0.02
        inv_vol[c] = S2_TARGET_RATIO[c] / max(vol, 1e-6)
    total = sum(inv_vol.values())
    return {c: inv_vol[c] / total for c in codes}


def s2_backtest(closes, vol_lb=60, step=20, cost=0.003):
    """S2 红利配置回测（月频再平衡，含再平衡成本），返回累计净值列表。"""
    codes = list(closes.keys())
    n = min(len(closes[c]) for c in codes)
    nav = 1.0
    navs = []
    weights = {c: 1.0 / len(codes) for c in codes}
    for i in range(vol_lb, n):
        if (i - vol_lb) % step == 0:
            new_weights = _s2_weights(closes, i, vol_lb)
            turnover = sum(abs(new_weights[c] - weights[c]) for c in codes) / 2.0
            nav *= (1 - cost * turnover)  # 再平衡成本
            weights = new_weights
        if i > 0:
            daily = sum(weights[c] * (closes[c][i] / closes[c][i - 1] - 1) for c in codes)
            nav *= (1 + daily)
        navs.append(round(nav, 6))
    return navs


def s3_backtest(closes, cost=0.003):
    """S3 腿近似回测：QDII ETF 池等权买入持有收益（含初始买入成本）。"""
    codes = list(closes.keys())
    if not codes:
        return []
    n = min(len(closes[c]) for c in codes)
    nav = 1.0 * (1 - cost)  # 初始买入成本
    navs = []
    w = 1.0 / len(codes)
    for i in range(1, n):
        daily = sum(w * (closes[c][i] / closes[c][i - 1] - 1) for c in codes)
        nav *= (1 + daily)
        navs.append(round(nav, 6))
    return navs


def s123_backtest(s1_closes, s2_closes, s3_closes=None):
    """S123 组合回测：S1(50%) + S2(30%) + S3(20%)。S3 缺数据时按现金近似。"""
    s1_nav = s1_backtest(s1_closes)
    s2_nav = s2_backtest(s2_closes)
    s3_nav = s3_backtest(s3_closes) if s3_closes else None
    n = min(len(s1_nav), len(s2_nav))
    if s3_nav:
        n = min(n, len(s3_nav))
    combo = []
    for i in range(n):
        s3_v = s3_nav[i] if s3_nav else 1.0
        v = 0.5 * s1_nav[i] + 0.3 * s2_nav[i] + 0.2 * s3_v
        combo.append(round(v, 6))
    return combo


def fetch_index_history(tscode, token, start='20190101'):
    """拉指数历史日线（正序），返回 (closes, dates)。用于基准曲线。"""
    end = datetime.date.today().strftime('%Y%m%d')
    body = json.dumps({
        'api_name': 'index_daily',
        'token': token,
        'params': {'ts_code': tscode, 'start_date': start, 'end_date': end},
        'fields': 'trade_date,close',
    })
    try:
        data = _post_raw(body)
    except Exception:
        raise RuntimeError('Tushare 指数 %s 历史数据解析失败' % tscode)
    if data.get('code') != 0:
        raise RuntimeError('Tushare 指数 %s 拉取失败：%s' % (tscode, data.get('msg')))
    items = data['data']['items']  # 倒序 [trade_date, close]
    items.reverse()
    return [float(it[1]) for it in items], [it[0] for it in items]


# ---- 市场情绪 + 财经快讯 + 资金建议 --------------------------------------

def fetch_north_money(token):
    """拉最新北向资金净流入（单位：万）。"""
    end = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.date.today() - datetime.timedelta(days=10)).strftime('%Y%m%d')
    body = json.dumps({'api_name': 'moneyflow_hsgt', 'token': token,
                       'params': {'start_date': start, 'end_date': end},
                       'fields': 'trade_date,north_money'})
    try:
        data = _post_raw(body)
    except Exception:
        return None
    if data.get('code') != 0:
        return None
    items = data['data']['items']  # 倒序 [trade_date, north_money]
    return {'date': items[0][0], 'north_money': float(items[0][1])} if items else None


def fetch_index_change(token, tscode):
    """拉某指数最近两日收盘价，返回涨跌幅（%）。"""
    end = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.date.today() - datetime.timedelta(days=10)).strftime('%Y%m%d')
    body = json.dumps({'api_name': 'index_daily', 'token': token,
                       'params': {'ts_code': tscode, 'start_date': start, 'end_date': end},
                       'fields': 'trade_date,close'})
    try:
        data = _post_raw(body)
    except Exception:
        return None
    if data.get('code') != 0:
        return None
    items = data['data']['items']  # 倒序
    if len(items) >= 2:
        return (float(items[0][1]) / float(items[1][1]) - 1.0) * 100
    return None


SENTIMENT_INDEXES = {'沪深300': '000300.SH', '创业板': '399006.SZ', '中证500': '000905.SH'}


def fetch_sentiment(token):
    """市场情绪：北向资金 + 三大指数涨跌 + 成交额放量比 → 情绪分（0-100）与等级。"""
    north = fetch_north_money(token)
    index_changes = []
    for name, tscode in SENTIMENT_INDEXES.items():
        chg = fetch_index_change(token, tscode)
        if chg is not None:
            index_changes.append({'name': name, 'change': round(chg, 2)})
    volume = fetch_market_volume(token)

    score = 50.0
    # 三因子权重：北向资金（封顶±15）+ 指数涨跌（封顶±25）+ 成交额放量（封顶±10）
    if north:
        north_yi = north['north_money'] / 10000.0  # 万元 → 亿元
        score += max(-15.0, min(15.0, north_yi / 10.0 * 2.0))  # 每 10 亿 ±2 分
    if index_changes:
        avg = sum(c['change'] for c in index_changes) / len(index_changes)
        score += max(-25.0, min(25.0, avg * 5.0))  # 每 1% ±5 分
    if volume:
        score += max(-10.0, min(10.0, (volume['ratio'] - 1.0) * 15.0))  # 放量比每 0.1 ±1.5 分
    score = max(0, min(100, round(score, 1)))

    if score >= 70:
        level = '乐观'
    elif score >= 58:
        level = '偏多'
    elif score >= 42:
        level = '中性'
    elif score >= 30:
        level = '偏空'
    else:
        level = '悲观'

    return {
        'score': score,
        'level': level,
        'north': north,
        'indexes': index_changes,
        'volume': volume,
    }


def fetch_news(limit=12):
    """拉同花顺财经快讯，返回 [{title, url, time}]。"""
    url = ('https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&pagesize=%d' % limit)
    try:
        data = _get_json(url)
        lst = data.get('data', {}).get('list', [])
    except Exception:
        return []
    out = []
    for item in lst:
        title = (item.get('title') or '').strip()
        docurl = item.get('url') or ''
        ctime = item.get('ctime') or 0
        tags = [t.get('name') for t in (item.get('tags') or []) if t.get('name')]
        try:
            t = datetime.datetime.fromtimestamp(int(ctime)).strftime('%H:%M')
        except (ValueError, OSError):
            t = ''
        if title and docurl:
            out.append({'title': title[:80], 'url': docurl, 'time': t, 'tags': tags})
    return out


def fetch_market_volume(token, tscode='000300.SH', days=10):
    """拉沪深300 最近 N 日成交额，返回今日成交额（亿）与放量比（今日/前5日均额）。"""
    end = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.date.today() - datetime.timedelta(days=days * 3)).strftime('%Y%m%d')
    body = json.dumps({'api_name': 'index_daily', 'token': token,
                       'params': {'ts_code': tscode, 'start_date': start, 'end_date': end},
                       'fields': 'trade_date,amount'})
    try:
        data = _post_raw(body)
    except Exception:
        return None
    if data.get('code') != 0:
        return None
    items = data['data']['items']  # 倒序 [trade_date, amount]
    amounts = [float(it[1]) for it in items]
    if len(amounts) < 2:
        return None
    today_amt = amounts[0] / 100000.0  # 千元 → 亿元
    prev_avg = sum(amounts[1:6]) / min(5, len(amounts) - 1) / 100000.0
    ratio = today_amt / prev_avg if prev_avg > 0 else 1.0
    return {'today_amount': round(today_amt, 1), 'ratio': round(ratio, 2)}


def _calc_shares(budget, price):
    """算股数（100 股整数手），返回 (shares, note)。资金不足一手时 note 说明。"""
    if price <= 0 or budget <= 0:
        return 0, ''
    shares = int(budget / price / 100) * 100
    if shares == 0:
        return 0, '资金不足一手（一手约 ¥%.0f）' % (price * 100)
    return shares, ''


def calc_advice(strategy, signal, capital):
    """根据策略信号 + 投入资金，算具体买卖股数（ETF 100 股整数手）。"""
    capital = max(0, int(capital))
    actions = []
    if strategy == 'v6':
        tgt = signal.get('target')
        if tgt:
            price = signal.get('_price', {}).get(tgt, 0)
            shares, note = _calc_shares(capital, price)
            actions.append({'leg': '主仓', 'name': signal.get('target_name', ''), 'code': tgt,
                            'price': round(price, 3), 'shares': shares,
                            'amount': round(shares * price, 2), 'note': note})
    else:  # s123
        s1 = signal.get('s1', {})
        s2 = signal.get('s2', {})
        tgt = s1.get('target')
        prices = signal.get('_price', {})
        if tgt:
            price = prices.get(tgt, 0)
            shares, note = _calc_shares(capital * 0.5, price)
            actions.append({'leg': 'S1轮动(50%)', 'name': s1.get('target_name', ''), 'code': tgt,
                            'price': round(price, 3), 'shares': shares,
                            'amount': round(shares * price, 2), 'note': note})
        for w in s2.get('weights', []):
            price = prices.get(w['code'], 0)
            shares, note = _calc_shares(capital * 0.3 * w['weight'], price)
            actions.append({'leg': 'S2红利(30%)', 'name': w['name'], 'code': w['code'],
                            'price': round(price, 3), 'shares': shares,
                            'amount': round(shares * price, 2), 'note': note})
    total_amount = round(sum(a['amount'] for a in actions), 2)
    return {'capital': capital, 'actions': actions, 'total_amount': total_amount}
