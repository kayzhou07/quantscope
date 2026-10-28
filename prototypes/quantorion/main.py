"""
缩量连板选股策略：筛选连续涨停+缩量的A股，前日收盘买入，当日收盘卖出。
"""
import io, json, logging, re, sys, time
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import os as _os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import LIMIT_UP_MIN_DAYS, DEFAULT_LIMIT_UP_PCT
from tushare_client import TushareClient
from ai_sentiment import analyze_sentiment
from market_news import fetch_daily_news, fetch_stock_news_summary, get_market_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="缩量连板选股", version="2.0.0")
tushare_client = TushareClient()

# ── 涨停幅度 ────────────────────────────────────────────────────────
def get_limit_pct(code: str) -> float:
    for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
        if code.startswith(prefix):
            return pct
    return 0.10

# ── 输入解析 ────────────────────────────────────────────────────────
class AdviceRequest(BaseModel):
    input: str

def parse_input(text: str) -> tuple[float, str]:
    cash_match = re.search(r"资金为(\d+(?:\.\d+)?)元", text)
    if not cash_match:
        raise ValueError("无法解析资金数额")
    current_cash = float(cash_match.group(1))
    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not date_match:
        raise ValueError("无法解析目标日期")
    y, m, d = date_match.groups()
    target_date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return current_cash, target_date

# ── 缩量连板选股 ────────────────────────────────────────────────────
def get_limit_up_advice(current_cash: float, target_date: str) -> list[dict]:
    """筛选连续涨停+缩量的A股，无持仓上限，前日收盘买入"""
    date_fmt = target_date.replace("-", "")

    if not tushare_client.is_trading_day(date_fmt):
        return []

    # 获取最近交易日的行情数据
    prev_date = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    recent_days = tushare_client.get_recent_trading_days(prev_date, LIMIT_UP_MIN_DAYS + 2)
    if len(recent_days) < LIMIT_UP_MIN_DAYS + 1:
        return []

    daily_data = tushare_client.get_multi_day_daily(recent_days[:LIMIT_UP_MIN_DAYS + 1])
    if len(daily_data) < LIMIT_UP_MIN_DAYS + 1:
        return []

    dates = sorted(daily_data.keys(), reverse=True)
    df_t = daily_data[dates[0]][["ts_code", "close", "pct_chg", "vol"]].copy()
    df_t.columns = ["ts_code", "close", "pct_chg", "vol"]
    df_y = daily_data[dates[1]][["ts_code", "close", "pct_chg", "vol"]].copy()
    df_y.columns = ["ts_code", "close_y", "pct_chg_y", "vol_y"]

    merged = df_t.merge(df_y, on="ts_code", how="inner")

    # A股过滤：仅主板/创业板/科创板
    valid_prefixes = {"00", "60", "30", "68"}
    merged["is_a"] = merged["ts_code"].apply(
        lambda c: c.split(".")[0][:2] in valid_prefixes
    )

    # 涨停判断
    def is_limit(row, col="pct_chg"):
        return row[col] >= (get_limit_pct(row["ts_code"]) * 100 * 0.995)

    merged["lt"] = merged.apply(lambda r: is_limit(r, "pct_chg"), axis=1)
    merged["ly"] = merged.apply(lambda r: is_limit(r, "pct_chg_y"), axis=1)
    merged["vd"] = merged["vol"] < merged["vol_y"]  # 缩量

    hits = merged[merged["lt"] & merged["ly"] & merged["vd"] & merged["is_a"]]
    hits = hits.sort_values("vol", ascending=True)

    if hits.empty:
        logger.info("未找到缩量连板股")
        return []

    # 获取股票名称
    all_stocks = tushare_client.get_all_stocks()
    name_map = {}
    if not all_stocks.empty:
        name_map = dict(zip(all_stocks["ts_code"], all_stocks["name"]))

    logger.info(f"缩量连板筛选: 找到 {len(hits)} 只")

    results = []
    per_stock_cash = current_cash / len(hits)
    for _, row in hits.iterrows():
        code = row["ts_code"]
        short = code.split(".")[0]
        buy_price = row["close"]  # 前日收盘价
        volume = int(per_stock_cash / buy_price / 100) * 100
        if volume < 100:
            continue
        results.append({
            "symbol": short,
            "symbol_name": name_map.get(code, short),
            "volume": volume,
        })

    return results

# ── API ─────────────────────────────────────────────────────────────
@app.post("/investment_advice")
async def investment_advice(request: AdviceRequest) -> dict:
    start = time.time()
    try:
        current_cash, target_date = parse_input(request.input)
        logger.info(f"请求: 资金={current_cash}, 日期={target_date}")
        result = get_limit_up_advice(current_cash, target_date)
        sentiment = analyze_sentiment(result) if result else {}
        symbols = [s["symbol"] for s in result] if result else []
        date_fmt = target_date.replace("-", "")
        news = fetch_daily_news(tushare_client, date_fmt, limit=8)
        stock_news = fetch_stock_news_summary(tushare_client, symbols, date_fmt) if symbols else {}
        elapsed = time.time() - start
        response = {"stocks": result, "sentiment": sentiment, "news": news, "stock_news": stock_news}
        logger.info(f"响应: {json.dumps(response, ensure_ascii=False)} (耗时 {elapsed:.2f}s)")
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market_overview")
async def market_overview():
    """获取市场全景数据 + AI摘要 + 新闻"""
    try:
        prev = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        today = datetime.now().strftime("%Y%m%d")

        indices = {}
        for code, name in [("000001.SH","上证指数"),("399001.SZ","深证成指"),("399006.SZ","创业板指"),("000688.SH","科创50")]:
            try:
                df = tushare_client.pro.index_daily(ts_code=code, start_date=prev, end_date=today, fields="trade_date,close,pct_chg,vol")
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    indices[name] = {"close": round(float(row["close"]),2), "pct_chg": round(float(row["pct_chg"]),2)}
            except: pass

        regime_data = tushare_client.get_market_regime(prev)
        regime = regime_data.get("regime","unknown")

        try:
            df_mkt = tushare_client.get_daily_batch(prev)
            mkt_stat = {"up":int((df_mkt["pct_chg"]>0).sum()),"down":int((df_mkt["pct_chg"]<0).sum()),"flat":int((df_mkt["pct_chg"]==0).sum()),"limit_up":int((df_mkt["pct_chg"]>=9.9).sum()),"total":len(df_mkt)} if not df_mkt.empty else {}
        except: mkt_stat = {}

        news = fetch_daily_news(tushare_client, prev, limit=15)
        summary = get_market_summary(indices, regime, mkt_stat)

        return {"indices":indices,"regime":regime,"market_stat":mkt_stat,"date":prev,"news":news,"ai_summary":summary}
    except Exception as e:
        return {"error":str(e)}

@app.get("/api/backtest_detail")
async def backtest_detail():
    """返回回测详情数据（预计算缓存）"""
    import json as _json
    cache_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "backtest_cache.json")
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"error": "backtest data not available"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    return _serve_static("index.html")

@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page():
    return _serve_static("backtest.html")

@app.get("/factors", response_class=HTMLResponse)
async def factors_page():
    return _serve_static("factors.html")

def _serve_static(filename):
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static", filename)
    if _os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Page not found</h1>")

# ── CLI ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        current_cash, target_date = parse_input(query)
        result = get_limit_up_advice(current_cash, target_date)
        print(json.dumps(result, ensure_ascii=False))
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8080)
