# QuantLibra 量衡 · 完整代码

> 全天候 ETF 轮动策略 × AI 回测可信度审计系统
> 参赛方向：方向二 · 量化投资策略与金融科技工具研发

---

## 1. 后端服务器 server.py

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QuantLibra 投资建议网站后端 —— 纯标准库 HTTP 服务器，零依赖。

用法：
  export TUSHARE_TOKEN=你的token      # 或运行时传
  python3 server.py                    # 默认 http://localhost:8088

启动后自动打开浏览器。API：
  GET /api/signal?strategy=v6     → V6 双动量轮动信号
  GET /api/signal?strategy=s123   → S123 三策略组合信号
"""
import json
import math
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import strategies

HOST = '0.0.0.0'
PORT = 8088
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 合并全部池子，一次拉齐（V6 + S1 + S2 + S3 的 ETF，用于信号与回测）
ALL_ETFS = {**strategies.V6_ETFS, **strategies.S1_ETFS,
            **strategies.S2_ETFS, **strategies.S3_ETFS}

# 简单数据缓存：{code: [closes]} 缓存 30 分钟，避免每次请求都拉 Tushare
_cache = {'data': None, 'date': None, 'ts': 0.0}
CACHE_TTL = 1800  # 30 分钟

# 历史数据缓存（回测用，数据量大，缓存 1 小时）
_history_cache = {'data': None, 'dates': None, 'ts': 0.0}
HISTORY_TTL = 3600


def get_data(token):
    """带缓存的拉数据。"""
    if _cache['data'] and (time.time() - _cache['ts']) < CACHE_TTL:
        return _cache['data'], _cache['date']
    closes, date = strategies.fetch_tushare(ALL_ETFS, token)
    _cache['data'] = closes
    _cache['date'] = date
    _cache['ts'] = time.time()
    return closes, date


def get_history(token):
    """带缓存的历史数据（2019 至今）。"""
    if _history_cache['data'] and (time.time() - _history_cache['ts']) < HISTORY_TTL:
        return _history_cache['data'], _history_cache['dates']
    closes, dates = strategies.fetch_history(ALL_ETFS, token)
    _history_cache['data'] = closes
    _history_cache['dates'] = dates
    _history_cache['ts'] = time.time()
    return closes, dates


# 基准（沪深300）缓存
_bench_cache = {'closes': None, 'dates': None, 'ts': 0.0}


def get_benchmark(token):
    """带缓存的沪深300 基准数据。"""
    if _bench_cache['closes'] and (time.time() - _bench_cache['ts']) < HISTORY_TTL:
        return _bench_cache['closes'], _bench_cache['dates']
    closes, dates = strategies.fetch_index_history('000300.SH', token)
    _bench_cache['closes'] = closes
    _bench_cache['dates'] = dates
    _bench_cache['ts'] = time.time()
    return closes, dates


# 快讯缓存（配合前端 60 秒自动刷新）
_news_cache = {'data': None, 'ts': 0.0}
NEWS_TTL = 60


def get_news():
    """带缓存的快讯（60 秒内复用）。"""
    if _news_cache['data'] and (time.time() - _news_cache['ts']) < NEWS_TTL:
        return _news_cache['data']
    news = strategies.fetch_news()
    _news_cache['data'] = news
    _news_cache['ts'] = time.time()
    return news


def calc_metrics(nav):
    """累计净值 → 总收益 / 年化 / 最大回撤 / 夏普 / 卡玛。"""
    total = nav[-1] - 1.0
    n = len(nav)
    annual = (nav[-1] ** (250.0 / n) - 1.0) if n > 0 and nav[-1] > 0 else 0.0
    # 日收益 → 年化波动率 → 夏普（无风险利率取 0）
    daily = [nav[i] / nav[i - 1] - 1.0 for i in range(1, n)]
    if daily:
        mean_d = sum(daily) / len(daily)
        var_d = sum((d - mean_d) ** 2 for d in daily) / len(daily)
        annual_vol = math.sqrt(var_d) * math.sqrt(250.0)
        sharpe = annual / annual_vol if annual_vol > 0 else 0.0
    else:
        sharpe = 0.0
    # 最大回撤
    peak = nav[0]
    max_dd = 0.0
    for v in nav:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak)
    calmar = annual / max_dd if max_dd > 0 else 0.0
    return {
        'total_return': round(total, 4),
        'annual': round(annual, 4),
        'max_drawdown': round(max_dd, 4),
        'sharpe': round(sharpe, 3),
        'calmar': round(calmar, 3),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默请求日志
        pass

    def _send(self, body, content_type, status=200):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(json.dumps(obj, ensure_ascii=False), 'application/json', status)

    def _file(self, rel_path, content_type):
        path = os.path.join(BASE_DIR, rel_path)
        if not os.path.exists(path):
            self._json({'error': 'file not found'}, 404)
            return
        with open(path, 'rb') as fp:
            self._send(fp.read(), content_type)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ('/', '/index.html'):
            self._file('static/index.html', 'text/html')
            return
        if path == '/api/signal':
            self._handle_signal(parse_qs(parsed.query))
            return
        if path == '/api/backtest':
            self._handle_backtest(parse_qs(parsed.query))
            return
        if path == '/api/advice':
            self._handle_advice(parse_qs(parsed.query))
            return
        if path == '/api/news':
            self._handle_news()
            return
        if path.startswith('/static/'):
            rel = path.lstrip('/')
            ct = 'text/css' if rel.endswith('.css') else 'application/javascript'
            self._file(rel, ct)
            return
        self._json({'error': 'not found: ' + path}, 404)

    def _handle_signal(self, qs):
        strategy = qs.get('strategy', ['v6'])[0]
        token = strategies.get_token()
        if not token:
            self._json({'error': '服务端未配置 TUSHARE_TOKEN 环境变量'}, 500)
            return
        try:
            closes, date = get_data(token)
            if strategy == 'v6':
                sig = strategies.v6_signal(closes)
            elif strategy == 's123':
                sig = strategies.s123_signal(closes, token)
            else:
                self._json({'error': '未知策略：' + strategy}, 400)
                return
            sig['date'] = date
            self._json(sig)
        except Exception as e:
            self._json({'error': '生成信号失败：' + str(e)}, 500)

    def _handle_backtest(self, qs):
        strategy = qs.get('strategy', ['v6'])[0]
        token = strategies.get_token()
        if not token:
            self._json({'error': '服务端未配置 TUSHARE_TOKEN 环境变量'}, 500)
            return
        try:
            closes, dates = get_history(token)
            if strategy == 'v6':
                sub = {c: closes[c] for c in strategies.V6_ETFS if c in closes}
                nav = strategies.v6_backtest(sub)
            elif strategy == 's123':
                s1_sub = {c: closes[c] for c in strategies.S1_ETFS if c in closes}
                s2_sub = {c: closes[c] for c in strategies.S2_ETFS if c in closes}
                s3_sub = {c: closes[c] for c in strategies.S3_ETFS if c in closes}
                nav = strategies.s123_backtest(s1_sub, s2_sub, s3_sub)
            else:
                self._json({'error': '未知策略：' + strategy}, 400)
                return
            metrics = calc_metrics(nav)
            d = dates[-len(nav):] if len(dates) >= len(nav) else dates

            # 沪深300 基准，归一化到回测起点
            bench_nav = None
            try:
                bench_closes, _ = get_benchmark(token)
                bench_sub = bench_closes[-len(nav):] if len(bench_closes) >= len(nav) else bench_closes
                if bench_sub and bench_sub[0] > 0:
                    bench_nav = [round(c / bench_sub[0], 6) for c in bench_sub]
            except Exception:
                bench_nav = None  # 基准拉取失败不影响主曲线

            self._json({'strategy': strategy, 'dates': d, 'nav': nav,
                        'benchmark': bench_nav, **metrics})
        except Exception as e:
            self._json({'error': '回测失败：' + str(e)}, 500)

    def _handle_advice(self, qs):
        strategy = qs.get('strategy', ['v6'])[0]
        try:
            capital = int(qs.get('capital', ['100000'])[0])
        except ValueError:
            capital = 100000
        token = strategies.get_token()
        if not token:
            self._json({'error': '服务端未配置 TUSHARE_TOKEN 环境变量'}, 500)
            return
        try:
            closes, date = get_data(token)
            prices = {c: round(closes[c][-1], 3) for c in closes}
            if strategy == 'v6':
                sig = strategies.v6_signal(closes)
            elif strategy == 's123':
                sig = strategies.s123_signal(closes, token)
            else:
                self._json({'error': '未知策略：' + strategy}, 400)
                return
            sig['_price'] = prices
            sig['date'] = date
            sentiment = strategies.fetch_sentiment(token)
            news = get_news()
            advice = strategies.calc_advice(strategy, sig, capital)
            self._json({'strategy': strategy, 'date': date, 'signal': sig,
                        'sentiment': sentiment, 'news': news, 'advice': advice})
        except Exception as e:
            self._json({'error': '生成建议失败：' + str(e)}, 500)

    def _handle_news(self):
        try:
            self._json({'news': get_news()})
        except Exception as e:
            self._json({'error': '拉取快讯失败：' + str(e)}, 500)


def open_browser():
    time.sleep(1.5)
    webbrowser.open('http://localhost:%d' % PORT)


def main():
    token = strategies.get_token()
    if not token:
        print('⚠️  未检测到 TUSHARE_TOKEN 环境变量。')
        print('   请先：export TUSHARE_TOKEN=你的token')
        print('   或临时：TUSHARE_TOKEN=你的token python3 server.py')
        sys.exit(1)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print('QuantLibra 已启动：http://localhost:%d' % PORT)
    print('数据源：Tushare（缓存 30 分钟） | Ctrl+C 退出')
    threading.Thread(target=open_browser, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')


if __name__ == '__main__':
    main()
```

## 2. 策略与数据逻辑 strategies.py

```python
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
```

## 3. 前端页面 static/index.html

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>QuantLibra · 全天候 ETF 轮动投资建议</title>
<style>
:root{
  --bg-0:#071827; --bg-1:#0B1F31; --bg-2:#0A2134;
  --block-0:#0A2638; --block-1:#102E40;
  --text-hi:#E8EDF5; --text-mid:#8899B4; --text-low:#55697C;
  --brand:#00D9B8;
  --up:#E54949;      /* 上涨红 */
  --down:#22B573;    /* 下跌绿 */
  --blue:#1597FF; --yellow:#F4C430;
  --line:#16445F;
  --font-mono:'SF Mono','JetBrains Mono','Consolas','Menlo',monospace;
  --font-sans:'Inter','Segoe UI',system-ui,-apple-system,'PingFang SC',sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:var(--font-sans);background:var(--bg-0);color:var(--text-hi);overflow-x:hidden;line-height:1.55}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;background:linear-gradient(165deg,var(--bg-0) 0%,var(--bg-1) 50%,var(--bg-2) 100%)}
::selection{background:var(--brand);color:#04121a}
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:var(--bg-0)}::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}
.mono{font-family:var(--font-mono)}
.up{color:var(--up)}.down{color:var(--down)}.blue{color:var(--blue)}.yellow{color:var(--yellow)}

/* ── 导航 ── */
.nav{position:fixed;top:0;left:0;right:0;z-index:100;height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 40px;background:rgba(7,24,39,.85);backdrop-filter:blur(14px);border-bottom:1px solid rgba(22,69,95,.5)}
.nav::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;background:linear-gradient(90deg,transparent,var(--brand),transparent);opacity:.5}
.nav-logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.05em;text-decoration:none;color:var(--text-hi)}
.nav-logo .tri{color:var(--brand);font-size:1.1em}
.nav-logo .brand{font-family:var(--font-mono);letter-spacing:-.5px}
.nav-tag{font-size:.72em;color:var(--text-low);border:1px solid var(--line);padding:4px 12px;border-radius:3px;letter-spacing:2px}

/* ── 通用 section ── */
.section{position:relative;z-index:1;padding:64px 40px}
.wrap{max-width:1280px;margin:0 auto;position:relative}
.sec-index{font-family:var(--font-mono);font-size:.74em;color:var(--brand);letter-spacing:3px;margin-bottom:20px;display:flex;align-items:center;gap:14px}
.sec-index::after{content:'';width:56px;height:1px;background:var(--brand);opacity:.6}
.sec-title{font-size:clamp(1.6em,3vw,2.4em);font-weight:900;letter-spacing:-1px;line-height:1.05;margin-bottom:8px}
.sec-sub{color:var(--text-mid);font-size:.9em;letter-spacing:1px;margin-bottom:36px}

/* ── Hero ── */
.hero{position:relative;min-height:88vh;overflow:hidden;z-index:1;display:flex;align-items:center}
.hero canvas{position:absolute;inset:0;width:100%;height:100%;z-index:0}
.hero-inner{position:relative;z-index:2;display:grid;grid-template-columns:50% 50%;gap:30px;padding:130px 60px 40px;align-items:center;max-width:1440px;margin:0 auto;width:100%}
.hero-left .kicker{font-family:var(--font-mono);font-size:.78em;color:var(--brand);letter-spacing:4px;margin-bottom:18px}
.hero-left h1{font-size:clamp(2.4em,5.5vw,4.6em);font-weight:900;line-height:.98;letter-spacing:-2px;margin-bottom:14px}
.hero-left h1 .accent{color:var(--brand)}
.hero-left .tagline{font-size:clamp(1.1em,2vw,1.5em);font-weight:700;margin-bottom:20px;letter-spacing:1px;color:var(--text-hi)}
.hero-left .desc{display:inline-block;padding:10px 0 10px 16px;border-left:3px solid var(--brand);font-size:.94em;color:var(--text-mid);letter-spacing:1px;line-height:1.9}
.hero-right{position:relative;min-height:420px}
.kpi-hero{position:absolute;top:6%;right:2%;text-align:right}
.kpi-hero .v{font-family:var(--font-mono);font-size:clamp(3.6em,8vw,6.5em);font-weight:900;letter-spacing:-3px;line-height:1;color:var(--up)}
.kpi-hero .l{font-family:var(--font-mono);font-size:.76em;color:var(--text-mid);letter-spacing:4px;margin-top:10px}

/* ── 策略选择（非对称） ── */
.picker{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:8px}
.strategy-card{position:relative;background:var(--block-0);border:1px solid var(--line);border-radius:6px;padding:30px;cursor:pointer;transition:all .25s;overflow:hidden}
.strategy-card:first-child{border-top:3px solid var(--brand)}
.strategy-card:last-child{border-top:3px solid var(--blue)}
.strategy-card:hover{transform:translateY(-3px);border-color:var(--brand)}
.strategy-card.active{border-color:var(--brand);box-shadow:0 0 0 1px var(--brand)}
.sc-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px}
.sc-head h3{font-size:1.4em;font-weight:800;letter-spacing:-.5px}
.sc-head .badge{font-family:var(--font-mono);font-size:.7em;color:var(--brand);letter-spacing:1px}
.strategy-card p{color:var(--text-mid);font-size:.88em;margin-bottom:14px;line-height:1.7}
.tags{display:flex;flex-wrap:wrap;gap:6px}
.tags span{font-size:.72em;color:var(--text-low);border:1px solid var(--line);padding:3px 10px;border-radius:3px;letter-spacing:1px}

/* ── 结果容器 ── */
.result{display:none;margin-top:28px}
.result.show{display:block;animation:fadeIn .5s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}

/* 决策大 KPI */
.decision{position:relative;background:var(--block-0);border:1px solid var(--line);border-left:3px solid var(--brand);border-radius:6px;padding:32px;margin-bottom:24px;display:flex;align-items:center;gap:28px;flex-wrap:wrap}
.decision .d-label{font-family:var(--font-mono);font-size:.72em;color:var(--text-low);letter-spacing:2px}
.decision .d-value{font-family:var(--font-mono);font-size:clamp(2em,4vw,3.2em);font-weight:900;letter-spacing:-1px;color:var(--brand);line-height:1.1}
.decision .d-note{color:var(--text-mid);font-size:.9em}

/* 面板 */
.panel{background:var(--block-0);border:1px solid var(--line);border-radius:6px;padding:26px;margin-bottom:20px}
.panel h4{font-size:1.02em;margin-bottom:16px;letter-spacing:.5px;display:flex;align-items:center;gap:8px}
.panel h4 .p-sub{font-size:.74em;color:var(--text-low);font-weight:400;letter-spacing:1px;margin-left:auto}

/* 表格 */
table{width:100%;border-collapse:collapse;font-size:.86em}
th{color:var(--text-low);font-weight:500;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);letter-spacing:1px;font-size:.76em}
td{padding:11px 10px;border-bottom:1px solid rgba(22,69,95,.25);font-family:var(--font-mono)}
td.name{font-family:var(--font-sans)}
tr:hover td{background:rgba(0,217,184,.03)}
.rank-1{color:var(--brand);font-weight:700}
.num-right{text-align:right}
.pos{color:var(--up)}.neg{color:var(--down)}

/* 情绪温度计 */
.sentiment-head{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.sentiment-score{font-family:var(--font-mono);font-size:clamp(3em,6vw,4.5em);font-weight:900;letter-spacing:-2px;line-height:1;color:var(--brand)}
.sentiment-level{font-size:1.05em;color:var(--text-mid)}
.sentiment-bar{position:relative;height:14px;border-radius:7px;background:linear-gradient(90deg,var(--down) 0%,var(--yellow) 35%,var(--text-mid) 50%,var(--blue) 75%,var(--up) 100%);margin:14px 0 6px}
.sentiment-marker{position:absolute;top:-7px;width:3px;height:28px;background:var(--text-hi)}
.sentiment-labels{display:flex;justify-content:space-between;font-size:.7em;color:var(--text-low);letter-spacing:1px}

/* 因子条（红利配比 / 指数） */
.factor-row{display:flex;align-items:center;gap:18px;padding:14px 0;border-bottom:1px solid rgba(22,69,95,.3)}
.factor-row .fname{min-width:110px;font-weight:600;font-size:.9em;letter-spacing:.5px}
.factor-row .fbar{flex:1;height:20px;position:relative;background:rgba(22,69,95,.2);border-radius:2px;overflow:hidden}
.factor-row .fbar .fill{height:100%;background:linear-gradient(90deg,var(--brand),var(--blue));border-radius:2px}
.factor-row .fval{font-family:var(--font-mono);font-weight:700;min-width:60px;text-align:right;color:var(--brand)}

/* 资金输入 + 建议 */
.capital-bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px}
.capital-bar label{color:var(--text-mid);font-size:.88em;letter-spacing:1px}
.capital-bar input{width:200px;padding:12px 16px;background:rgba(7,24,39,.6);border:1px solid var(--line);border-radius:4px;color:var(--text-hi);font-family:var(--font-mono);font-size:1.05em}
.capital-bar input:focus{outline:none;border-color:var(--brand)}
.btn-run{background:var(--brand);color:#04121a;border:none;padding:12px 24px;border-radius:4px;font-weight:800;font-size:.9em;letter-spacing:2px;cursor:pointer;font-family:var(--font-mono);transition:.2s}
.btn-run:hover{box-shadow:0 0 26px rgba(0,217,184,.3)}
.advice-total{font-family:var(--font-mono);color:var(--brand);font-size:.92em;text-align:right;margin-top:12px}

/* 回测 */
.bt-hero{display:flex;gap:36px;align-items:flex-end;margin-bottom:26px;flex-wrap:wrap}
.bt-main{font-family:var(--font-mono);font-size:clamp(2.6em,5vw,4em);font-weight:900;letter-spacing:-2px;line-height:1;color:var(--up)}
.bt-main .l{font-size:.2em;color:var(--text-mid);letter-spacing:3px;display:block;margin-top:10px;font-family:var(--font-sans)}
.bt-kpis{display:flex;gap:30px;flex-wrap:wrap}
.bt-kpi .v{font-family:var(--font-mono);font-size:2em;font-weight:800;letter-spacing:-1px}
.bt-kpi .l{font-size:.72em;color:var(--text-low);letter-spacing:1px;margin-top:4px}
.bt-chart{position:relative;background:var(--block-1);border:1px solid var(--line);border-radius:6px;height:340px;overflow:hidden}
.bt-chart canvas{width:100%;height:100%;display:block}

/* 快讯 */
.news-filter{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.news-filter-btn{background:transparent;border:1px solid var(--line);color:var(--text-mid);padding:5px 14px;border-radius:3px;font-size:.78em;cursor:pointer;letter-spacing:1px;transition:.2s}
.news-filter-btn:hover{border-color:var(--brand);color:var(--brand)}
.news-filter-btn.active{background:var(--brand);color:#04121a;border-color:var(--brand)}
.news-list{max-height:360px;overflow-y:auto}
.news-item{display:flex;gap:12px;padding:11px 4px;border-bottom:1px solid rgba(22,69,95,.25);font-size:.86em}
.news-item .n-time{color:var(--text-low);font-family:var(--font-mono);flex-shrink:0}
.news-item a{color:var(--text-mid);text-decoration:none;transition:color .2s;line-height:1.5}
.news-item a:hover{color:var(--brand)}
.news-tag{display:inline-block;font-size:.68em;color:var(--blue);border:1px solid var(--line);padding:1px 7px;border-radius:2px;margin-right:5px}

.note{font-size:.8em;color:var(--text-low);background:rgba(22,69,95,.15);border-left:3px solid var(--blue);padding:10px 14px;border-radius:0 4px 4px 0;margin-top:12px;line-height:1.7}
.error{background:rgba(229,73,73,.08);border:1px solid rgba(229,73,73,.3);border-radius:6px;padding:16px 20px;color:var(--up);font-size:.9em;margin-bottom:16px}
.loading{text-align:center;padding:50px;color:var(--text-mid);font-family:var(--font-mono);letter-spacing:2px}
.spinner{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--brand);border-radius:50%;margin:0 auto 16px;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* Footer */
.footer{position:relative;z-index:1;padding:70px 40px 40px;background:var(--bg-1);border-top:1px solid var(--line);overflow:hidden}
.footer .big{font-family:var(--font-mono);font-size:clamp(3em,9vw,7em);font-weight:900;letter-spacing:-3px;line-height:1;color:rgba(232,237,245,.08);text-align:center}
.footer .risk{max-width:640px;margin:18px auto 0;text-align:center;color:var(--text-low);font-size:.78em;line-height:1.8}

/* 响应式 */
@media(max-width:900px){
  .section{padding:48px 20px}
  .nav{padding:0 20px}
  .hero-inner{grid-template-columns:1fr;padding:110px 24px 40px}
  .hero-right{min-height:260px}
  .picker{grid-template-columns:1fr}
}
@media(max-width:600px){
  .bt-hero{flex-direction:column;align-items:flex-start}
  .decision{flex-direction:column;align-items:flex-start;gap:10px}
}
</style>
</head>
<body>

<nav class="nav">
  <a class="nav-logo" href="#top"><span class="tri">△</span><span class="brand">QuantLibra</span></a>
  <div class="nav-tag">全天候 ETF 轮动</div>
</nav>

<!-- Hero -->
<section class="hero" id="top">
  <canvas id="heroCanvas"></canvas>
  <div class="hero-inner">
    <div class="hero-left">
      <div class="kicker">ALL-WEATHER ETF ROTATION</div>
      <h1><span class="mono">QuantLibra</span><br><span class="accent">量衡</span></h1>
      <div class="tagline">全天候 ETF 轮动 × AI 回测审计</div>
      <div class="desc">让数据驱动决策<br>让回测经得起追问</div>
    </div>
    <div class="hero-right">
      <div class="kpi-hero">
        <div class="v" id="heroKpi">--</div>
        <div class="l" id="heroKpiLabel">今日目标持仓</div>
      </div>
    </div>
  </div>
</section>

<!-- 主内容 -->
<section class="section" id="main">
  <div class="wrap">
    <div class="sec-index">STRATEGY SELECT</div>
    <h2 class="sec-title">选择策略，获取投资建议</h2>
    <p class="sec-sub">V6 双动量轮动 · 满仓单押最强标的　/　S123 三策略组合 · 低相关天然对冲</p>

    <div class="picker">
      <div class="strategy-card" data-strategy="v6" onclick="pick('v6', this)">
        <div class="sc-head"><h3>V6 双动量轮动</h3><span class="badge">单策略 · 满仓单押</span></div>
        <p>斜率动量 × 效率动量，排名加权融合，周频轮动四个低相关资产，满仓持有最强标的。</p>
        <div class="tags"><span>红利</span><span>创业板</span><span>纳指</span><span>黄金</span></div>
      </div>
      <div class="strategy-card" data-strategy="s123" onclick="pick('s123', this)">
        <div class="sc-head"><h3>S123 三策略组合</h3><span class="badge">组合 · 全天候</span></div>
        <p>轮动 50% + 红利 30% + QDII 套利 20%，三腿低相关、此起彼伏、天然对冲，回撤更低。</p>
        <div class="tags"><span>S1 轮动</span><span>S2 红利</span><span>S3 套利</span></div>
      </div>
    </div>

    <div class="result" id="result"></div>
  </div>
</section>

<footer class="footer">
  <div class="big">QUANTBALANCE</div>
  <div class="risk">风险提示：本系统仅供量化研究和技术学习使用，不构成任何投资建议。回测收益不代表未来表现。股市有风险，投资需谨慎。数据来源于 Tushare 金融开放平台与同花顺公开接口。</div>
</footer>

<script>
const $ = id => document.getElementById(id);
let currentStrategy = null;

function esc(s){return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}

function pick(strategy, el) {
  currentStrategy = strategy;
  document.querySelectorAll('.strategy-card').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  load(strategy);
}

function loading(msg) {
  $('result').innerHTML = '<div class="loading"><div class="spinner"></div>' + msg + '</div>';
  $('result').classList.add('show');
}

function showError(msg) {
  $('result').innerHTML = '<div class="error">⚠️ ' + esc(msg) + '</div>';
}

async function load(strategy) {
  const capital = parseInt($('capitalInput') ? $('capitalInput').value : 300000) || 100000;
  loading('正在拉取行情 + 情绪 + 快讯 + 回测…（首次约需 30 秒）');
  try {
    const [advResp, btResp] = await Promise.all([
      fetch('/api/advice?strategy=' + strategy + '&capital=' + capital),
      fetch('/api/backtest?strategy=' + strategy),
    ]);
    const adv = await advResp.json();
    const bt = await btResp.json();
    if (adv.error) { showError(adv.error); return; }
    // Hero KPI 更新
    const tgt = strategy === 'v6' ? adv.signal.target_name : adv.signal.s1.target_name;
    $('heroKpi').textContent = tgt || '空仓';
    $('heroKpiLabel').textContent = '今日目标持仓 · 数据截至 ' + adv.date;
    const signalHtml = strategy === 'v6' ? renderV6(adv.signal) : renderS123(adv.signal);
    const sentimentHtml = renderSentiment(adv.sentiment);
    const adviceHtml = renderAdvice(adv.advice);
    const newsHtml = renderNews(adv.news);
    const btHtml = bt.error ? '<div class="note">回测不可用：' + esc(bt.error) + '</div>' : renderBacktest(bt);
    $('result').innerHTML = signalHtml + sentimentHtml + adviceHtml + btHtml + newsHtml;
    if (!bt.error) drawChart(bt);
    startNewsRefresh();
  } catch (e) {
    showError('请求失败：' + e.message + '（请确认后端 server.py 已启动）');
  }
}

function reload() { if (currentStrategy) load(currentStrategy); }

function sign(v){return v>0?'<span class="pos">'+v+'</span>':(v<0?'<span class="neg">'+v+'</span>':v)}

function renderV6(d) {
  const rows = d.ranking.map((r, i) => `
    <tr>
      <td class="${r.rank===1?'rank-1':''}">#${i+1}</td>
      <td class="name">${esc(r.name)} <span style="color:var(--text-low)">${r.code}</span></td>
      <td class="num-right">${sign(r.slope)}</td>
      <td class="num-right">${sign(r.eff)}</td>
      <td class="num-right ${r.rank===1?'rank-1':''}">${r.rank.toFixed(1)}</td>
    </tr>`).join('');
  return `
    <div class="decision">
      <div class="d-label">今日目标持仓</div>
      <div class="d-value">${d.target ? esc(d.target_name + ' ' + d.target) : '空仓'}</div>
      <div class="d-note">${esc(d.decision)}</div>
    </div>
    <div class="panel">
      <h4>动量排名<span class="p-sub">综合排名 = 0.5×斜率排名 + 0.5×效率排名，越小越强</span></h4>
      <table>
        <thead><tr><th>#</th><th class="name">标的</th><th class="num-right">斜率动量</th><th class="num-right">效率动量</th><th class="num-right">综合排名</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="note">V6 满仓单押最强标的，历史回撤约 25%，单日可能 -6%+，适合高风险偏好。信号用「截至上一交易日」收盘价计算，符合抗未来函数原则。</div>
    </div>`;
}

function renderS123(d) {
  const s1 = d.s1, s2 = d.s2, s3 = d.s3;
  const s1rows = s1.ranking.map((r, i) => `
    <tr>
      <td class="${r.rank===1?'rank-1':''}">#${i+1}</td>
      <td class="name">${esc(r.name)} <span style="color:var(--text-low)">${r.code}</span></td>
      <td class="num-right">${sign(r.score)}</td>
    </tr>`).join('');
  const s2rows = s2.weights.map(w => `
    <div class="factor-row">
      <div class="fname">${esc(w.name)}</div>
      <div class="fbar"><div class="fill" style="width:${(w.weight*100).toFixed(1)}%"></div></div>
      <div class="fval">${(w.weight*100).toFixed(1)}%</div>
    </div>`).join('');
  const s3rows = (s3 || []).map(r => {
    const isDiscount = r.premium < 0;
    return `<tr>
      <td class="name">${esc(r.name)} <span style="color:var(--text-low)">${r.code}</span></td>
      <td class="num-right">${r.unit_nav}</td>
      <td class="num-right">${r.price}</td>
      <td class="num-right ${isDiscount?'neg':'pos'}">${(r.premium*100).toFixed(2)}%</td>
      <td>${isDiscount?'<span class="neg">折价·买入</span>':'<span class="pos">溢价</span>'}</td>
    </tr>`;
  }).join('');
  return `
    <div class="decision">
      <div class="d-label">S1 轮动 · 今日目标持仓（50% 资金）</div>
      <div class="d-value">${s1.target ? esc(s1.target_name + ' ' + s1.target) : '空仓'}</div>
      <div class="d-note">三腿低相关（&lt;0.25），此起彼伏、天然对冲</div>
    </div>
    <div class="panel">
      <h4>S1 · 4ETF 轮动<span class="p-sub">年化收益×R² 打分，取第一</span></h4>
      <table>
        <thead><tr><th>#</th><th class="name">标的</th><th class="num-right">动量得分</th></tr></thead>
        <tbody>${s1rows || '<tr><td colspan="3" class="name">全部标的被短动量过滤剔除</td></tr>'}</tbody>
      </table>
    </div>
    <div class="panel">
      <h4>S2 · 红利配置<span class="p-sub">权重 ∝ 目标比例 ÷ 波动率 · 月频再平衡</span></h4>
      ${s2rows}
    </div>
    <div class="panel">
      <h4>S3 · QDII 折溢价套利<span class="p-sub">固定权重 50/30/20 · 折价 &lt; 0 为买入机会</span></h4>
      ${s3rows ? `<table>
        <thead><tr><th class="name">标的</th><th class="num-right">净值</th><th class="num-right">市价</th><th class="num-right">折溢价率</th><th>状态</th></tr></thead>
        <tbody>${s3rows}</tbody>
      </table>` : '<div class="note">暂无 QDII 折溢价数据</div>'}
    </div>`;
}

function renderSentiment(sent) {
  if (!sent) return '';
  const north = sent.north;
  const northTxt = north ? (north.north_money / 10000).toFixed(1) + ' 亿' : 'N/A';
  const vol = sent.volume;
  const volTxt = vol ? vol.today_amount + ' 亿' : 'N/A';
  const volRatioTxt = vol ? ((vol.ratio >= 1 ? '放量' : '缩量') + ' ' + vol.ratio + '×') : '';
  const idxRows = (sent.indexes || []).map(i => {
    const cls = i.change >= 0 ? 'pos' : 'neg';
    return `<div class="factor-row">
      <div class="fname">${esc(i.name)}</div>
      <div class="fbar"><div class="fill" style="width:${Math.min(100, Math.abs(i.change)*12)}%;background:${i.change>=0?'var(--up)':'var(--down)'}"></div></div>
      <div class="fval ${cls}">${i.change>0?'+':''}${i.change}%</div>
    </div>`;
  }).join('');
  const markerLeft = Math.max(0, Math.min(100, sent.score));
  return `
    <div class="panel">
      <h4>市场情绪<span class="p-sub">北向资金 + 三大指数 + 成交额 三因子</span></h4>
      <div class="sentiment-head">
        <span class="sentiment-score">${sent.score}</span>
        <span class="sentiment-level">${esc(sent.level)}</span>
        <span style="color:var(--text-low);font-size:.85em">北向 <b class="mono">${northTxt}</b> · 成交额 <b class="mono">${volTxt}</b> <span class="${vol && vol.ratio>=1?'pos':'neg'}">${volRatioTxt}</span></span>
      </div>
      <div class="sentiment-bar"><div class="sentiment-marker" style="left:${markerLeft}%"></div></div>
      <div class="sentiment-labels"><span>悲观</span><span>偏空</span><span>中性</span><span>偏多</span><span>乐观</span></div>
      <div style="margin-top:14px">${idxRows}</div>
    </div>`;
}

function renderAdvice(adv) {
  if (!adv) return '';
  const rows = (adv.actions || []).map(a => {
    const sharesCell = a.shares > 0 ? a.shares : (a.note ? '<span class="neg">0</span>' : '0');
    const amountCell = a.note ? '<span class="neg" style="font-size:.82em">' + esc(a.note) + '</span>' : a.amount.toLocaleString();
    return `<tr>
      <td class="name">${esc(a.leg)}</td>
      <td class="name">${esc(a.name)} <span style="color:var(--text-low)">${a.code}</span></td>
      <td class="num-right">${a.price}</td>
      <td class="num-right">${sharesCell}</td>
      <td class="num-right">${amountCell}</td>
    </tr>`;
  }).join('');
  const totalHtml = adv.total_amount > 0
    ? '实际投入合计：¥' + adv.total_amount.toLocaleString()
    : '<span class="neg">投入资金不足以买入一手</span>';
  return `
    <div class="panel">
      <h4>当日投资建议<span class="p-sub">投入资金 ¥${adv.capital.toLocaleString()} · 100 股整数手</span></h4>
      <div class="capital-bar">
        <label>投入资金（元）</label>
        <input type="number" id="capitalInput" value="${adv.capital}" min="1000" step="1000">
        <button class="btn-run" onclick="reload()">RUN</button>
      </div>
      ${adv.actions.length ? `<table>
        <thead><tr><th class="name">组合腿</th><th class="name">标的</th><th class="num-right">现价</th><th class="num-right">股数</th><th class="num-right">金额</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="advice-total">${totalHtml}</div>` : '<div class="note">当前信号为空仓，建议观望，暂不买入。</div>'}
    </div>`;
}

function renderBacktest(bt) {
  const ret = (bt.total_return*100).toFixed(1);
  const ann = (bt.annual*100).toFixed(1);
  const dd = (bt.max_drawdown*100).toFixed(1);
  const sharpe = bt.sharpe != null ? bt.sharpe.toFixed(2) : '—';
  const calmar = bt.calmar != null ? bt.calmar.toFixed(2) : '—';
  return `
    <div class="panel">
      <h4>历史回测<span class="p-sub">2019 至今 · 不复权价 + 0.3% 换仓成本</span></h4>
      <div class="bt-hero">
        <div class="bt-main">${ret}%<span class="l">累计收益</span></div>
        <div class="bt-kpis">
          <div class="bt-kpi"><div class="v blue">${ann}%</div><div class="l">年化</div></div>
          <div class="bt-kpi"><div class="v neg">${dd}%</div><div class="l">最大回撤</div></div>
          <div class="bt-kpi"><div class="v yellow">${sharpe}</div><div class="l">夏普</div></div>
          <div class="bt-kpi"><div class="v blue">${calmar}</div><div class="l">卡玛</div></div>
        </div>
      </div>
      <div class="bt-chart"><canvas id="btChart"></canvas></div>
      <div class="note">回测为本地简化复现（不复权价、0.3% 换仓成本、S123 的 S3 腿按 QDII 等权近似）；精确口径以聚宽回测为准（V6 ≈ 290~368%、S123 ≈ 167%）。</div>
    </div>`;
}

function renderNews(news) {
  if (!news || !news.length) return '';
  const tagCount = {};
  news.forEach(n => (n.tags || []).forEach(t => { tagCount[t] = (tagCount[t] || 0) + 1; }));
  const allTags = Object.entries(tagCount).sort((a, b) => b[1] - a[1]).slice(0, 6).map(e => e[0]);
  const filterBtns = ['全部'].concat(allTags).map(t =>
    `<button class="news-filter-btn ${t === '全部' ? 'active' : ''}" data-tag="${esc(t)}" onclick="filterNews('${esc(t)}', this)">${esc(t)}</button>`).join('');
  const items = news.map(n => {
    const tagChips = (n.tags || []).map(t => `<span class="news-tag">${esc(t)}</span>`).join('');
    return `<div class="news-item" data-tags="${esc((n.tags || []).join(','))}">
      <span class="n-time">${esc(n.time)}</span>
      <div style="flex:1;min-width:0">
        <a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>
        ${tagChips ? '<div style="margin-top:3px">' + tagChips + '</div>' : ''}
      </div>
    </div>`;
  }).join('');
  return `
    <div class="panel" id="newsPanel">
      <h4>当日财经快讯<span class="p-sub">同花顺 · 点击跳转 · 每 60 秒自动刷新</span></h4>
      <div class="news-filter">${filterBtns}</div>
      <div class="news-list">${items}</div>
    </div>`;
}

function filterNews(tag, btn) {
  document.querySelectorAll('.news-filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.news-item').forEach(item => {
    const tags = item.getAttribute('data-tags') || '';
    item.style.display = (tag === '全部' || tags.includes(tag)) ? '' : 'none';
  });
}

let newsTimer = null;
function startNewsRefresh() {
  if (newsTimer) clearInterval(newsTimer);
  newsTimer = setInterval(refreshNews, 60000);
}
async function refreshNews() {
  const panel = $('newsPanel');
  if (!panel) return;
  try {
    const resp = await fetch('/api/news');
    const data = await resp.json();
    if (data.news && data.news.length) {
      const tmp = document.createElement('div');
      tmp.innerHTML = renderNews(data.news);
      panel.innerHTML = tmp.firstChild.innerHTML;
    }
  } catch (e) {}
}

/* 回测图（红涨绿跌） */
function drawChart(bt) {
  const canvas = $('btChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const nav = bt.nav, dates = bt.dates, bench = bt.benchmark;
  if (!nav || nav.length < 2) return;
  const W = canvas.parentElement.offsetWidth, H = canvas.parentElement.offsetHeight;
  canvas.width = W; canvas.height = H;
  const pad = {l:52, r:16, t:18, b:28};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  let viewStart = 0, viewEnd = nav.length - 1;
  let dragStartX = null, dragStartView = null, hoverX = null;

  function redraw() {
    const visNav = nav.slice(viewStart, viewEnd + 1);
    const visDates = dates.slice(viewStart, viewEnd + 1);
    const visBench = bench ? bench.slice(viewStart, viewEnd + 1) : null;
    const all = visBench ? visNav.concat(visBench) : visNav;
    const minV = Math.min.apply(null, all), maxV = Math.max.apply(null, all);
    const range = (maxV - minV) || 1;
    const nVis = visNav.length;
    ctx.clearRect(0, 0, W, H);
    ctx.font = '11px monospace';
    for (let g = 0; g <= 4; g++) {
      const y = pad.t + ih * g / 4;
      ctx.strokeStyle = 'rgba(22,69,95,.25)';
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillStyle = '#55697C';
      ctx.fillText((maxV - range * g / 4).toFixed(2), 6, y + 4);
    }
    if (visBench && visBench.length > 1) {
      ctx.strokeStyle = '#8899B4';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      for (let i = 0; i < visBench.length; i++) {
        const x = pad.l + iw * i / (visBench.length - 1);
        const y = pad.t + ih * (1 - (visBench[i] - minV) / range);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.strokeStyle = '#00D9B8';
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let i = 0; i < nVis; i++) {
      const x = pad.l + iw * i / (nVis - 1);
      const y = pad.t + ih * (1 - (visNav[i] - minV) / range);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.lineTo(pad.l + iw, pad.t + ih);
    ctx.lineTo(pad.l, pad.t + ih);
    ctx.closePath();
    ctx.fillStyle = 'rgba(0,217,184,.07)';
    ctx.fill();
    ctx.fillStyle = '#55697C';
    ctx.fillText(visDates[0], pad.l, H - 8);
    ctx.fillText(visDates[visDates.length - 1], W - pad.r - 78, H - 8);
    ctx.fillStyle = '#00D9B8'; ctx.fillRect(pad.l, 5, 12, 3);
    ctx.fillStyle = '#E8EDF5'; ctx.fillText('策略', pad.l + 16, 10);
    if (visBench) {
      ctx.fillStyle = '#8899B4'; ctx.fillRect(pad.l + 52, 5, 12, 3);
      ctx.fillStyle = '#8899B4'; ctx.fillText('沪深300', pad.l + 68, 10);
    }
    if (hoverX !== null && hoverX >= pad.l && hoverX <= W - pad.r) {
      const ratio = (hoverX - pad.l) / iw;
      const idx = Math.max(0, Math.min(nav.length - 1, Math.round(viewStart + ratio * (viewEnd - viewStart))));
      const px = pad.l + iw * (idx - viewStart) / (viewEnd - viewStart);
      const py = pad.t + ih * (1 - (nav[idx] - minV) / range);
      ctx.strokeStyle = '#55697C';
      ctx.beginPath(); ctx.moveTo(px, pad.t); ctx.lineTo(px, pad.t + ih); ctx.stroke();
      ctx.fillStyle = '#00D9B8';
      ctx.beginPath(); ctx.arc(px, py, 4, 0, 2 * Math.PI); ctx.fill();
      const text = dates[idx] + '  净值 ' + nav[idx].toFixed(3);
      ctx.fillStyle = 'rgba(7,24,39,.94)';
      const tw = ctx.measureText(text).width + 16;
      const tx = (px + 12 + tw > W) ? px - tw - 12 : px + 12;
      ctx.fillRect(tx, 4, tw, 22);
      ctx.fillStyle = '#E8EDF5';
      ctx.fillText(text, tx + 8, 20);
    }
  }

  canvas.onmousemove = function (e) {
    const rect = canvas.getBoundingClientRect();
    const sx = (e.clientX - rect.left) * (W / rect.width);
    if (dragStartX !== null) {
      const span = dragStartView.end - dragStartView.start;
      const dx = (e.clientX - dragStartX) * (W / rect.width);
      const shift = Math.round(-dx / iw * span);
      let ns = dragStartView.start + shift, ne = dragStartView.end + shift;
      if (ne > nav.length - 1) { ns -= (ne - (nav.length - 1)); ne = nav.length - 1; }
      if (ns < 0) { ne -= ns; ns = 0; }
      viewStart = Math.max(0, ns); viewEnd = Math.min(nav.length - 1, ne);
    } else { hoverX = sx; }
    redraw();
  };
  canvas.onmousedown = function (e) { dragStartX = e.clientX; dragStartView = {start: viewStart, end: viewEnd}; };
  canvas.onmouseup = canvas.onmouseleave = function () { dragStartX = null; if (canvas.onmouseleave) { hoverX = null; } redraw(); };
  canvas.onwheel = function (e) {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (W / rect.width);
    const ratio = Math.max(0, Math.min(1, (mx - pad.l) / iw));
    const span = viewEnd - viewStart;
    const zoom = e.deltaY < 0 ? 0.75 : 1.33;
    const newSpan = Math.max(10, Math.min(nav.length - 1, Math.round(span * zoom)));
    const centerIdx = viewStart + ratio * span;
    let ns = Math.round(centerIdx - ratio * newSpan);
    let ne = ns + newSpan;
    if (ns < 0) { ns = 0; ne = newSpan; }
    if (ne > nav.length - 1) { ne = nav.length - 1; ns = ne - newSpan; }
    viewStart = Math.max(0, ns); viewEnd = Math.min(nav.length - 1, ne);
    redraw();
  };
  redraw();
}

/* Hero 数据轨迹背景 */
(function(){
  const c = document.getElementById('heroCanvas');
  if(!c) return;
  const ctx = c.getContext('2d');
  function resize(){ c.width = c.offsetWidth; c.height = c.offsetHeight; }
  resize(); window.addEventListener('resize', resize);
  const colors = ['rgba(0,217,184,','rgba(34,181,115,','rgba(21,151,255,','rgba(244,196,48,'];
  const focus = {x:0.80, y:0.30};
  const tracks = [];
  for(let i=0;i<24;i++){
    const s=i%4; let x,y;
    if(s===0){x=-20;y=Math.random()*c.height}else if(s===1){x=c.width+20;y=Math.random()*c.height}else if(s===2){x=Math.random()*c.width;y=-20}else{x=Math.random()*c.width;y=c.height+20}
    tracks.push({x,y,fx:focus.x*c.width+(Math.random()-.5)*120,fy:focus.y*c.height+(Math.random()-.5)*120,ci:Math.floor(Math.random()*4),w:Math.random()<.3?2.2:(Math.random()<.6?1.2:.6),speed:.002+Math.random()*.004,t:Math.random()});
  }
  function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    ctx.strokeStyle='rgba(22,69,95,.14)';ctx.lineWidth=1;
    for(let x=64;x<c.width;x+=64){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,c.height);ctx.stroke()}
    for(let y=64;y<c.height;y+=64){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(c.width,y);ctx.stroke()}
    tracks.forEach(t=>{
      t.t+=t.speed;if(t.t>1)t.t=0;
      const px=t.x+(t.fx-t.x)*t.t,py=t.y+(t.fy-t.y)*t.t;
      const cx=(t.x+t.fx)/2+(Math.random()-.5)*60,cy=(t.y+t.fy)/2-120;
      ctx.beginPath();ctx.moveTo(t.x,t.y);ctx.quadraticCurveTo(cx,cy,px,py);
      ctx.strokeStyle=colors[t.ci]+(0.06+Math.sin(t.t*Math.PI)*.2)+')';ctx.lineWidth=t.w;ctx.stroke();
      if(t.t>.05){ctx.fillStyle=colors[t.ci]+'0.7)';ctx.beginPath();ctx.arc(px,py,t.w+.8,0,Math.PI*2);ctx.fill()}
    });
    requestAnimationFrame(draw);
  }
  draw();
})();
</script>
</body>
</html>
```

## 4. 回测审计工具 backtest_auditor.py

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测审计器（Backtest Auditor）—— 量化回测「可信度」静态审计工具

自动扫描聚宽(JoinQuant)策略代码，检测会让回测收益虚高的常见偏差。
审计结论基于项目里已实证的反模式（见 Downloads/之后的Ai策略/策略诊断结论.md）：

  规则 1  幸存者偏差      get_index_stocks 未传 date，用「今天成分股」回测「过去」
  规则 2  涨跌停成交假象   LimitOrderStyle 用 0.99/1.01×last_price 追板
  规则 3  流动性幻觉      小市值 + 持仓集中，忽略真实冲击成本
  规则 4  未来函数        end_date=context.current_dt 用当天数据做当天决策
  规则 5  成本/滑点缺失   未设置 set_order_cost / set_slippage
  规则 6  过度拟合        全局参数堆积（>60 个可调参数）
  规则 7  参数挖掘痕迹    大量 enable/use 实验开关（p-hacking）
  规则 8  样本外验证缺失  参数多但无样本外/训练验证划分
  规则 9  基准不当        小市值策略用沪深300基准虚高超额

用法：
  python3 backtest_auditor.py <策略文件.py> [--out 报告.md]
"""

import re
import sys
import os
from dataclasses import dataclass, field, asdict

# ---------------- 数据结构 ----------------

@dataclass
class Finding:
    rule_id: str          # 规则编号
    rule_name: str        # 规则名
    severity: str         # 高 / 中 / 低
    line: int             # 触发行号（1-based）
    snippet: str          # 触发代码片段
    why: str              # 为什么是问题
    fix: str              # 怎么修

@dataclass
class Rule:
    rule_id: str
    name: str
    severity: str
    summary: str
    scan: callable        # scan(lines: list[str]) -> list[Finding]

# ---------------- 规则实现 ----------------

def _line_snippet(line):
    return line.strip()[:80]

# 规则1：幸存者偏差
def _scan_survivorship(lines):
    out = []
    pat_call = re.compile(r'get_index_stocks\(')
    for i, line in enumerate(lines):
        if not pat_call.search(line):
            continue
        # 检查该行（及其续行）的调用括号内是否有 date 参数
        # 简化：只看同一行内有没有 date=；跨行调用罕见，这里逐行判断并给提示
        if 'date' not in line:
            out.append(Finding(
                'R1', '幸存者偏差', '高', i + 1, _line_snippet(line),
                'get_index_stocks 未传 date 参数，会用「当前」成分股回测「历史」：'
                '期间退市/ST/被剔除的股票被天然排除，选股池被污染，收益虚高。',
                '改为 get_index_stocks(code, date=context.previous_date) 或逐日取当日成分股。',
            ))
    return out

# 规则2：涨跌停成交假象
def _scan_limit_order(lines):
    out = []
    # 0.9x 折扣卖出 / 1.0x 溢价买入，且价格锚定 last_price —— 追板/挂板信号
    pat = re.compile(r'LimitOrderStyle\(\s*(0\.9\d|1\.0\d)\s*\*\s*.*?\.last_price')
    for i, line in enumerate(lines):
        m = pat.search(line)
        if m:
            out.append(Finding(
                'R2', '涨跌停成交假象', '高', i + 1, _line_snippet(line),
                '用 0.99/1.01×last_price 挂限价单追涨跌停，一字板实盘买不进/卖不出，'
                '回测却按成交计入，把买不到的利润算成自己的。',
                '涨跌停附近改为不成交（检查 high_limit/low_limit 后跳过），或改用更贴近真实的价格。',
            ))
    return out

# 规则3：流动性幻觉（小市值 + 持仓集中）
def _scan_liquidity(lines):
    out = []
    cap_min = cap_max = None
    holdings = None
    for i, line in enumerate(lines):
        m = re.search(r'filter_market_cap_min[\'\"]?\s*[:=]\s*(\d+)', line)
        if m:
            cap_min = int(m.group(1))
        m = re.search(r'filter_market_cap_max[\'\"]?\s*[:=]\s*(\d+)', line)
        if m:
            cap_max = int(m.group(1))
        m = re.search(r'holdings_num[\'\"]?\s*[:=]\s*(\d+)', line)
        if m:
            holdings = int(m.group(1))
    if cap_max is not None and cap_max <= 150:
        why = ('市值上限 %.0f 亿，属小市值区间；小票真实冲击成本、容量远高于回测假设，'
               '资金稍大就打滑。' % cap_max)
        if holdings is not None and holdings <= 5:
            why += ' 且持仓集中（holdings_num=%d），进一步放大单票冲击。' % holdings
        # 锚定到 cap_max 定义行
        line_no = next((i + 1 for i, l in enumerate(lines) if 'filter_market_cap_max' in l), 1)
        out.append(Finding(
            'R3', '流动性幻觉', '高', line_no,
            _line_snippet(lines[line_no - 1]),
            why,
            '小市值策略需实测单票冲击成本/容量上限，或在报告中明确标注为「小资金上限运行」。',
        ))
    return out

# 规则4：未来函数（end_date 用当天）
def _scan_lookahead(lines):
    out = []
    pat = re.compile(r'end_date\s*=\s*context\.current_dt(?!\.previous)')
    for i, line in enumerate(lines):
        if pat.search(line):
            out.append(Finding(
                'R4', '未来函数', '高', i + 1, _line_snippet(line),
                '数据终点用了 context.current_dt（当天），当天行情/成分尚未完整，'
                '等于用「今天」的数据做「今天」的决策，回测收益含未来信息。',
                '统一改用 end_date=context.previous_date，只用已收盘的昨日数据。',
            ))
    return out

# 规则5：成本/滑点缺失
def _scan_cost(lines):
    out = []
    has_cost = any('set_order_cost' in l for l in lines)
    has_slip = any('set_slippage' in l for l in lines)
    if not has_cost and not has_slip:
        out.append(Finding(
            'R5', '成本/滑点缺失', '中', 1, '(全局)',
            '未设置 set_order_cost / set_slippage，回测按零成本成交，'
            '高换手策略的真实成本会显著侵蚀收益。',
            '显式设置 set_order_cost(佣金+印花税) 与 set_slippage(滑点)，用更严口径重跑。',
        ))
    return out

# 规则6：过度拟合（参数堆积）
def _count_params(lines):
    return sum(1 for l in lines if re.match(r'^\s*g\.[a-zA-Z_]\w*\s*=', l))

def _count_switches(lines):
    return sum(1 for l in lines if re.match(r'^\s*g\.(?:enable|use)_[a-zA-Z_]\w*\s*=', l))

def _scan_overfit(lines):
    out = []
    n = _count_params(lines)
    if n > 150:
        out.append(Finding(
            'R6', '过度拟合(参数堆积)', '高', 1, '(全局) 约 %d 个全局参数' % n,
            '全局变量/可调参数多达 %d 个，远超「少数几个参数」的可解释区间；'
            '参数越多，回测越容易拟合历史噪声而非真实规律，样本外大概率失效。' % n,
            '冻结绝大多数参数，只保留有经济学含义的核心参数（<10 个），'
            '并用样本外区间或 walk-forward 验证稳健性。',
        ))
    elif n > 60:
        out.append(Finding(
            'R6', '过度拟合(参数堆积)', '中', 1, '(全局) 约 %d 个全局参数' % n,
            '全局变量约 %d 个，参数偏多，存在拟合历史噪声的风险。' % n,
            '精简参数，并补充样本外验证。',
        ))
    return out

# 规则7：参数挖掘痕迹（实验开关堆积）
def _scan_switch_mining(lines):
    out = []
    n = _count_switches(lines)
    if n > 15:
        line_no = next((i + 1 for i, l in enumerate(lines)
                        if re.match(r'^\s*g\.(?:enable|use)_[a-zA-Z_]\w*\s*=', l)), 1)
        out.append(Finding(
            'R7', '参数挖掘痕迹(实验开关堆积)', '中', line_no,
            _line_snippet(lines[line_no - 1]),
            '多达 %d 个 enable/use 实验开关。大量「试了又关」的开关是参数挖掘(p-hacking)的'
            '典型痕迹：反复调整开关组合直到回测好看，容易把偶然收益当成规律。' % n,
            '收敛开关：把默认关闭的实验开关从代码里删掉，只保留真正生效的逻辑，并做样本外验证。',
        ))
    return out

# 规则8：样本外验证缺失（依赖参数规模）
def _scan_no_oos(lines):
    out = []
    n = _count_params(lines)
    if n <= 60:
        return out  # 参数少的小策略没有 OOS 不算问题
    oos_pat = re.compile(r'样本外|样本内|验证集|训练集|out[_-]?of[_-]?sample|walk[_-]?forward|oos')
    if not any(oos_pat.search(l) for l in lines):
        out.append(Finding(
            'R8', '样本外验证缺失', '中', 1,
            '(全局) %d 个参数但无样本外/验证划分' % n,
            '策略有 %d 个参数，但代码里没有任何样本外/训练验证划分——'
            '意味着所有参数都对着同一段历史反复调出来，无法证明稳健性。' % n,
            '划分样本内/样本外两段：样本内调参、样本外只看一次；或做 walk-forward 滚动验证。',
        ))
    return out

# 规则9：基准不当（小市值策略配沪深300基准）
def _scan_benchmark(lines):
    out = []
    is_small = False
    for l in lines:
        m = re.search(r'filter_market_cap_max[\'\"]?\s*[:=]\s*(\d+)', l)
        if m and int(m.group(1)) <= 150:
            is_small = True
            break
    if not is_small:
        return out
    for i, l in enumerate(lines):
        if re.search(r'set_benchmark\(\s*[\'"]000300\.XSHG[\'"]', l):
            out.append(Finding(
                'R9', '基准不当', '低', i + 1, _line_snippet(l),
                '小市值策略用沪深300(大盘蓝筹)做基准，会系统性高估超额收益——'
                '小市值牛市里沪深300往往大幅跑输，换个匹配基准超额就大幅缩水。',
                '改用与小市值更匹配的基准，如中证2000(932000)或国证2000(399303)。',
            ))
            break
    return out

RULES = [
    Rule('R1', '幸存者偏差', '高', '成分股时间错位', _scan_survivorship),
    Rule('R2', '涨跌停成交假象', '高', '极端价成交假设', _scan_limit_order),
    Rule('R3', '流动性幻觉', '高', '小市值容量失真', _scan_liquidity),
    Rule('R4', '未来函数', '高', '当天数据当天决策', _scan_lookahead),
    Rule('R5', '成本/滑点缺失', '中', '零成本成交', _scan_cost),
    Rule('R6', '过度拟合', '高', '参数堆积', _scan_overfit),
    Rule('R7', '参数挖掘痕迹', '中', '实验开关堆积', _scan_switch_mining),
    Rule('R8', '样本外验证缺失', '中', '无OOS划分', _scan_no_oos),
    Rule('R9', '基准不当', '低', '基准错配', _scan_benchmark),
]

# ---------------- 报告生成 ----------------

def _severity_rank(sev):
    return {'高': 0, '中': 1, '低': 2}.get(sev, 3)

def generate_report(path, findings):
    """生成结构化 markdown 审计报告（模板版；可被 LLM 层进一步改写）"""
    findings = sorted(findings, key=lambda f: (_severity_rank(f.severity), f.rule_id, f.line))
    n_high = sum(1 for f in findings if f.severity == '高')
    n_mid = sum(1 for f in findings if f.severity == '中')
    n_low = sum(1 for f in findings if f.severity == '低')

    # 可信度评级
    if n_high >= 2:
        verdict = '⚠️ 高风险：存在 %d 处高严重度偏差，回测收益大概率虚高，不可直接采信' % n_high
    elif n_high == 1:
        verdict = '⚠️ 中风险：发现 1 处高严重度偏差，需修复后重新回测'
    elif n_mid >= 1:
        verdict = 'ℹ️ 低风险：无高严重度偏差，但有 %d 处中等提示，建议补足后复核' % n_mid
    else:
        verdict = '✅ 未发现已知偏差模式，回测可信度较高（规则审计的覆盖范围见底部）'

    lines = []
    lines.append('# 回测审计报告')
    lines.append('')
    lines.append('- **审计对象**：`%s`' % os.path.basename(path))
    lines.append('- **检测规则**：%d 条（幸存者偏差 / 涨跌停成交 / 流动性 / 未来函数 / 成本滑点）' % len(RULES))
    lines.append('- **发现问题**：高 %d · 中 %d · 低 %d' % (n_high, n_mid, n_low))
    lines.append('')
    lines.append('## 总体结论')
    lines.append('')
    lines.append(verdict)
    lines.append('')

    if not findings:
        lines.append('（未触发任何规则）')
        lines.append('')

    for f in findings:
        lines.append('## [%s] %s —— %s' % (f.rule_id, f.rule_name, f.severity))
        lines.append('')
        lines.append('- **位置**：第 %d 行' % f.line)
        lines.append('- **代码**：`%s`' % f.snippet.replace('`', "'"))
        lines.append('- **问题**：%s' % f.why)
        lines.append('- **修复**：%s' % f.fix)
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('*本报告由规则引擎静态审计生成，覆盖 %d 类已知偏差模式；'
                 '静态分析有盲区，未覆盖「多重检验校正 / 交易逻辑与数据时序对齐 / '
                 '换手率与容量敏感性」等更隐蔽偏差，需人工复核。*' % len(RULES))
    return '\n'.join(lines)

# ---------------- 主流程 ----------------

def audit_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as fp:
        text = fp.read()
    lines = text.splitlines()
    findings = []
    for rule in RULES:
        try:
            findings.extend(rule.scan(lines))
        except Exception as e:  # 规则异常不应中断整体审计
            print('  [warn] 规则 %s 执行失败: %s' % (rule.rule_id, e), file=sys.stderr)
    return findings

def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    out_path = None
    if '--out' in argv:
        out_path = argv[argv.index('--out') + 1]

    findings = audit_file(path)
    report = generate_report(path, findings)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as fp:
            fp.write(report)
        print('报告已写入: %s' % out_path)
    else:
        print(report)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
```

## 5. LLM 报告层 llm_report.py

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 报告层 —— 把 backtest_auditor 的结构化发现，转成一份叙事化的专业审计报告。

后端：DeepSeek（OpenAI 兼容接口），默认模型 deepseek-v4-pro。
需要设置 DEEPSEEK_API_KEY 环境变量（或传 --api-key）。

用法：
  DEEPSEEK_API_KEY=sk-xxx python3 llm_report.py <策略文件.py>
  DEEPSEEK_API_KEY=sk-xxx python3 llm_report.py <策略文件.py> --out 报告.md

数据流：
  backtest_auditor.audit_file() → findings(结构化) → build_llm_prompt() → DeepSeek → 叙事报告
"""
import json
import os
import subprocess
import sys

import backtest_auditor as auditor

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"

SYSTEM_PROMPT = (
    "你是一名资深量化回测审计专家，专门识别会让回测收益虚高的偏差。"
    "你会收到一个规则引擎从策略代码里检测出的偏差清单（JSON），"
    "请把它改写为一份面向量化策略开发者的专业审计报告（Markdown）。"
)


def build_llm_prompt(file_name, findings):
    """把结构化发现序列化为给 LLM 的输入。"""
    items = [{
        'rule_id': f.rule_id,
        'rule_name': f.rule_name,
        'severity': f.severity,
        'line': f.line,
        'snippet': f.snippet,
        'why': f.why,
        'fix': f.fix,
    } for f in findings]
    payload = {'file': file_name, 'findings': items}
    return (
        "以下是回测审计规则引擎对策略代码 `{f}` 检测出的 {n} 个偏差，"
        "请生成一份中文审计报告，结构要求：\n"
        "1. 【总体结论】一句话定性这个回测结果可信不可信，为什么；\n"
        "2. 【分项说明】按偏差类型分组，每组讲清楚：这是什么问题、为什么会让收益虚高、"
        "影响大概多大、怎么修；\n"
        "3. 【修复优先级】按严重程度排序给出修复顺序；\n"
        "4. 【一句话建议】给开发者一句话行动建议。\n"
        "不要罗列 JSON，要用连贯的叙述，像资深同行在帮你审代码。\n\n"
        "偏差清单 JSON：\n"
        "{json}"
    ).format(f=file_name, n=len(findings), json=json.dumps(payload, ensure_ascii=False, indent=2))


def call_llm(prompt, api_key=None, model=DEFAULT_MODEL):
    """调用 DeepSeek（OpenAI 兼容接口）生成报告。用 curl 以复用系统信任链。"""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "缺少 API key：请设置环境变量 DEEPSEEK_API_KEY，或用 --api-key 传入。"
        )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": 8000,
        "temperature": 0.2,  # 报告类任务，低温度更稳定
    })
    proc = subprocess.run(
        ["curl", "-s", "-m", "180", DEEPSEEK_URL,
         "-H", "Content-Type: application/json",
         "-H", "Authorization: Bearer " + key,
         "-d", body],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("curl 调用失败: " + proc.stderr)
    data = json.loads(proc.stdout)
    if "choices" not in data:
        raise RuntimeError("API 返回异常: " + proc.stdout[:500])
    return data["choices"][0]["message"]["content"]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    out_path = None
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    model = DEFAULT_MODEL
    if '--out' in argv:
        out_path = argv[argv.index('--out') + 1]
    if '--api-key' in argv:
        api_key = argv[argv.index('--api-key') + 1]
    if '--model' in argv:
        model = argv[argv.index('--model') + 1]

    findings = auditor.audit_file(path)
    if not findings:
        report = "# 回测审计报告\n\n✅ 规则引擎未检出已知偏差，无需 LLM 深化。\n"
    else:
        prompt = build_llm_prompt(os.path.basename(path), findings)
        report = call_llm(prompt, api_key=api_key, model=model)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as fp:
            fp.write(report)
        print('报告已写入:', out_path)
    else:
        print(report)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
```

## 6. V6 双动量轮动策略（聚宽版）

```python
# =====================================================================
# 全天候轮动策略 V6 —— 双动量排名加权 + 冷却期
# 平台：聚宽（JoinQuant）
#
# 核心思路（参考《ETF双动量轮动策略》）：
#   不再只用一种动量，而是把「斜率动量」和「效率动量」用排名加权合并：
#
#   斜率动量 = 斜率 × R²          （斜率=涨速，R²=趋势稳定性，V3 的指标）
#   效率动量 = 区间涨跌幅 × 效率系数（效率系数=位移/路程，考夫曼 ER）
#
#   对每个 ETF 分别算出两个动量的「排名」（第1名=最强），
#   再按权重（默认等权 0.5/0.5）把两个排名加总，选综合排名第一的 ETF。
#   注意：是「合并两个排名」，不是「用一个替换另一个」。
#
#   冷却期（降低换手、改善持仓体验）：
#   - MIN_HOLD_DAYS：必须持满这些天数才允许「正常换仓」
#   - FORCE_SELL_RANK：即使未持满，排名跌破该阈值也强制卖出
#
# 候选池（四个低相关资产）：
#   红利 510880  创业板 159915  纳指 513100  黄金 518880
#
# 参数：
#   g.slope_weight / g.eff_weight  斜率/效率排名权重（默认等权）
#   g.min_hold_days                最小持仓天数（冷却期）
#   g.force_sell_rank              强制卖出排名阈值
# =====================================================================

import numpy as np


def initialize(context):
    # ---------------- 基础设置 ----------------
    set_benchmark('000300.XSHG')        # 基准：沪深300
    set_option('use_real_price', True)  # 用真实价格撮合
    log.set_level('order', 'error')     # 减少下单日志噪音

    # ---------------- 策略参数（可调） ----------------
    g.lookback = 25        # 回看窗口：过去 25 天
    g.rebalance = 'weekly' # 调仓频率：'daily' 每日 / 'weekly' 每周一
    g.slope_weight = 0.5   # 斜率动量排名权重
    g.eff_weight = 0.5     # 效率动量排名权重
    g.min_hold_days = 14   # 冷却期：最小持仓天数（14 自然日 ≈ 2 周）
    g.force_sell_rank = 3  # 强制卖出：排名跌出前 3 名则强制换仓
    g.entry_date = None    # 当前持仓的建仓日期（用于计算持仓天数）

    # ---------------- 候选池：四个低相关 ETF ----------------
    g.etfs = [
        '510880.XSHG',  # 红利   上证红利ETF（价值）
        '159915.XSHE',  # 创业板 易方达创业板ETF（成长）
        '513100.XSHG',  # 纳指   国泰纳斯达克100ETF（外盘 QDII）
        '518880.XSHG',  # 黄金   华安黄金ETF（商品）
    ]

    # ---------------- 设置调仓时间 ----------------
    if g.rebalance == 'daily':
        run_daily(trade, time='14:50')               # 每个交易日 14:50
    elif g.rebalance == 'weekly':
        run_weekly(trade, weekday=1, time='14:50')   # 每周一 14:50
    else:
        raise ValueError("g.rebalance 只能是 'daily' 或 'weekly'")


def calc_slope_momentum(context):
    """斜率动量 = 斜率 × R²（V3 指标，用归一化收盘价做线性回归）"""
    n = g.lookback
    m = {}
    for etf in g.etfs:
        df = attribute_history(etf, n, '1d', ['close'], df=True)
        if len(df) < n:
            m[etf] = -float('inf')
            continue
        close = df['close'].values.astype(float)
        y = close / close[0]           # 归一化，斜率 = 日涨速
        if np.std(y) < 1e-12:
            m[etf] = 0.0
            continue
        x = np.arange(n, dtype=float)
        slope = np.polyfit(x, y, 1)[0]
        r = np.corrcoef(x, y)[0, 1]
        m[etf] = slope * (r * r)
    return m


def calc_eff_momentum(context):
    """效率动量 = 区间涨跌幅 × 效率系数（文章公式，用简单涨跌幅）"""
    n = g.lookback
    m = {}
    for etf in g.etfs:
        df = attribute_history(etf, n, '1d', ['close'], df=True)
        if len(df) < n:
            m[etf] = -float('inf')
            continue
        close = df['close'].values.astype(float)
        ret = close[-1] / close[0] - 1          # 区间涨跌幅（简单）
        daily = np.diff(close) / close[:-1]     # 每日涨跌幅（简单）
        path = np.sum(np.abs(daily))            # 路程
        if path < 1e-12:
            m[etf] = 0.0
            continue
        er = abs(ret) / path                    # 效率系数 = 位移/路程
        m[etf] = ret * er                       # 效率动量（有方向）
    return m


def rank_dict(scores):
    """把分数转成排名：第 1 名 = 分数最高。返回 {etf: rank}"""
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return {etf: i + 1 for i, (etf, _) in enumerate(ranked)}


def trade(context):
    # 1) 计算两种动量及其排名，加权合成综合排名
    slope_m = calc_slope_momentum(context)
    eff_m = calc_eff_momentum(context)
    slope_rank = rank_dict(slope_m)
    eff_rank = rank_dict(eff_m)
    composite = {etf: g.slope_weight * slope_rank[etf] + g.eff_weight * eff_rank[etf]
                 for etf in g.etfs}
    best_etf = min(composite, key=composite.get)   # 综合排名第一（排名和最小）

    # 2) 找到当前实际持有的 ETF（本策略最多持有一只）
    current_etf = None
    for etf in list(context.portfolio.positions.keys()):
        if context.portfolio.positions[etf].total_amount > 0:
            current_etf = etf
            break

    # 3) 决策：空仓判断（不受冷却期限制）+ 冷却期轮动
    if eff_m[best_etf] <= 0:
        target_etf = None                 # 最强标的都在跌 -> 空仓
    elif current_etf is None:
        target_etf = best_etf             # 空仓 -> 买入最强
    elif best_etf == current_etf:
        target_etf = current_etf          # 已持有最强 -> 不动
    else:
        # 冷却期逻辑
        days_held = 0
        if g.entry_date is not None:
            days_held = (context.current_dt.date() - g.entry_date).days
        current_rank = composite[current_etf]
        if days_held < g.min_hold_days and current_rank <= g.force_sell_rank:
            target_etf = current_etf      # 冷却期内且排名还行 -> 继续持有
        else:
            target_etf = best_etf         # 持够了或排名跌破阈值 -> 换仓

    # 4) 目标没变，直接返回
    if target_etf == current_etf:
        return

    # 5) 卖出旧持仓（若旧持仓停牌则跳过，避免下单失败）
    if current_etf is not None:
        if get_current_data()[current_etf].paused:
            log.info('%s 停牌，无法卖出，本次暂不换仓' % current_etf)
            return
        order_target(current_etf, 0)

    # 6) 买入新目标（未上市 / 停牌则跳过）
    if target_etf is not None:
        if len(attribute_history(target_etf, 1, '1d', ['close'])) == 0:
            log.info('%s 尚未上市或无数据，本次不买入' % target_etf)
            g.entry_date = None
            return
        if get_current_data()[target_etf].paused:
            log.info('%s 停牌，本次不买入' % target_etf)
            g.entry_date = None
            return
        order_value(target_etf, context.portfolio.available_cash)
        g.entry_date = context.current_dt.date()   # 记录建仓日期
    else:
        g.entry_date = None                        # 空仓，清空建仓日期

    # 7) 打印调仓日志（只在真正换仓时打印）
    log.info('综合排名: ' + ', '.join(
        '%s=#%d' % (k, composite[k]) for k in sorted(composite, key=composite.get)))
    if target_etf is None:
        log.info('空仓：最强标的 %s 效率动量 %.4f <= 0' % (best_etf, eff_m[best_etf]))
    elif current_etf is None:
        log.info('建仓：全仓买入 %s（斜率动量 %.4f，效率动量 %.4f）' % (
            target_etf, slope_m[target_etf], eff_m[target_etf]))
    else:
        log.info('换仓：卖出 %s，全仓买入 %s（斜率动量 %.4f，效率动量 %.4f）' % (
            current_etf, target_etf, slope_m[target_etf], eff_m[target_etf]))
```

## 7. V6 本地信号脚本（纯标准库）

```python
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
```

## 8. 一键启动脚本 start.sh

```bash
#!/bin/bash
# QuantLibra 一键启动脚本
# 用法：./start.sh   （或 bash start.sh）

cd "$(dirname "$0")"

# 1. 检查 token
if [ -z "$TUSHARE_TOKEN" ]; then
  echo "❌ 未设置 TUSHARE_TOKEN 环境变量。"
  echo "   请先执行：export TUSHARE_TOKEN=你的token"
  echo "   然后重新运行：./start.sh"
  exit 1
fi

# 2. 检查端口是否已在运行
if lsof -i :8088 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✅ 服务器已在运行（端口 8088）"
else
  echo "🚀 启动服务器..."
  nohup python3 -u server.py > server.log 2>&1 &
  sleep 2
  echo "✅ 服务器已启动（PID $!），日志：server.log"
fi

# 3. 打开浏览器
echo "🌐 打开 http://localhost:8088"
sleep 1
open http://localhost:8088 2>/dev/null || xdg-open http://localhost:8088 2>/dev/null || echo "请手动打开 http://localhost:8088"

echo ""
echo "停止服务器：pkill -f 'server.py'"
```

## 9. systemd 开机自启服务

```ini
[Unit]
Description=QuantLibra ETF Rotation Web
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/quant_web
Environment=TUSHARE_TOKEN=在这里填入你的Tushare令牌
ExecStart=/usr/bin/python3 -u server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
