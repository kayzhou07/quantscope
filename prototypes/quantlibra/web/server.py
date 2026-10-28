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

HOST = os.environ.get('QUANTSCOPE_HOST', '127.0.0.1')
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
