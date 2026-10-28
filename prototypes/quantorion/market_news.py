"""
财经新闻聚合 + AI市场摘要
"""
import json, logging, os, time, re
from datetime import datetime
import requests

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".news_cache.json")
CACHE_TTL = 1800  # 30分钟


def _load_cache():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if time.time() - d.get("ts", 0) < CACHE_TTL:
                return d.get("news", []), d.get("summary", "")
    except Exception:
        pass
    return None, ""


def _save_cache(news, summary=""):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "news": news, "summary": summary}, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch_163_money() -> list[dict]:
    """网易财经快讯"""
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://money.163.com/"}
        resp = requests.get(
            "https://money.163.com/special/002557S6/news_json.js",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200 and "[" in resp.text:
            import re as _re
            items = _re.findall(r'"title":"([^"]+)".*?"time":"([^"]+)"', resp.text)
            news = []
            for title, t in items[:20]:
                title = title.strip()
                if title and len(title) > 6:
                    url = _re.findall(r'"url":"([^"]+)"', resp.text[i:i+300] if i < len(resp.text)-300 else resp.text[i:])
                    news.append({"time": t[-5:] if len(t) > 5 else t, "title": title[:150], "source": "网易财经", "url": url[0] if url else ""})
            if news:
                logger.info(f"网易财经获取 {len(news)} 条")
                return news
    except Exception as e:
        logger.warning(f"网易财经失败: {e}")
    return []


def _fetch_10jqka() -> list[dict]:
    """同花顺财经快讯"""
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://news.10jqka.com.cn/"}
        resp = requests.get(
            "https://news.10jqka.com.cn/tapp/news/push/stock/",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("data", [])
            # 尝试多种data结构
            items = []
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                for k in ["list", "items", "news", "result", "roll_data"]:
                    if k in raw and isinstance(raw[k], list):
                        items = raw[k]
                        break
                if not items:
                    # data本身可能有title字段的list嵌套在某个key下
                    for v in raw.values():
                        if isinstance(v, list) and v and isinstance(v[0], dict) and "title" in v[0]:
                            items = v
                            break
            news = []
            for item in (items or [])[:20]:
                title = item.get("title", "") or item.get("content", "")
                title = title.strip()
                if title and len(title) > 6:
                    news.append({
                        "time": item.get("ctime", "")[-8:-3] if item.get("ctime") else "",
                        "title": title[:150],
                        "source": "同花顺",
                        "url": item.get("url", "") or item.get("shareurl", "") or ""
                    })
            if news:
                logger.info(f"同花顺获取 {len(news)} 条")
                return news
    except Exception as e:
        logger.warning(f"同花顺失败: {e}")
    return []


def _fetch_sina_finance() -> list[dict]:
    """新浪财经要闻"""
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = requests.get(
            "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=20",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            news = []
            for item in items[:20]:
                title = item.get("title", "").strip()
                if title and len(title) > 6:
                    news.append({"time": item.get("ctime", "")[-8:-3] if item.get("ctime") else "",
                                 "title": title[:150], "source": "新浪财经",
                                 "url": item.get("url", "")})
            if news:
                logger.info(f"新浪财经获取 {len(news)} 条")
                return news
    except Exception as e:
        logger.warning(f"新浪财经失败: {e}")
    return []


def generate_ai_summary(indices: dict, regime: str, mkt_stat: dict) -> str:
    """用AI生成当日市场摘要"""
    from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_PROXY
    if not OPENAI_API_KEY:
        return ""

    import ssl, warnings
    warnings.filterwarnings("ignore")
    from requests.adapters import HTTPAdapter

    class A(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["cert_reqs"] = ssl.CERT_NONE
            kw["assert_hostname"] = False
            return super().init_poolmanager(*a, **kw)
        def proxy_manager_for(self, *a, **kw):
            kw["cert_reqs"] = ssl.CERT_NONE
            kw["assert_hostname"] = False
            return super().proxy_manager_for(*a, **kw)

    s = requests.Session()
    s.trust_env = False
    s.verify = False
    if OPENAI_PROXY:
        s.proxies = {"http": OPENAI_PROXY, "https": OPENAI_PROXY}
    s.mount("https://", A(max_retries=0))

    idx_text = "\n".join(f"- {k}: {v.get('close','')} ({v.get('pct_chg',0):+.2f}%)" for k, v in (indices or {}).items())
    mkt_text = f"上涨{mkt_stat.get('up','?')}家, 下跌{mkt_stat.get('down','?')}家, 涨停{mkt_stat.get('limit_up','?')}家, 共{mkt_stat.get('total','?')}只"
    prompt = f"""你是A股市场分析师。请根据以下数据生成今日收盘市场简报（100字以内），专业简洁、不含投资建议：

指数表现：
{idx_text}

市场环境：{regime}
涨跌统计：{mkt_text}

请输出纯文本，不要JSON。"""

    try:
        resp = s.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": OPENAI_MODEL, "messages": [
                {"role": "system", "content": ""},
                {"role": "user", "content": prompt},
            ], "temperature": 0.3, "max_tokens": 200},
            timeout=20,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"AI摘要失败: {e}")
    return ""


def fetch_daily_news(client=None, date_str: str = None, limit: int = 15) -> list[dict]:
    """聚合财经新闻（多源+缓存）"""
    cached, _ = _load_cache()
    if cached:
        return cached[:limit]

    all_news = []
    for fetcher in [_fetch_10jqka, _fetch_sina_finance, _fetch_163_money]:
        try:
            items = fetcher()
            seen = {n["title"] for n in all_news}
            for n in items:
                if n["title"] not in seen:
                    all_news.append(n)
        except Exception:
            pass

    # 去重
    seen = set()
    unique = []
    for n in all_news:
        if n["title"] not in seen:
            seen.add(n["title"])
            unique.append(n)

    result = unique[:limit]
    _save_cache(result, "")
    return result


def get_market_summary(indices: dict = None, regime: str = "", mkt_stat: dict = None) -> str:
    """获取或生成市场摘要（带缓存）"""
    _, cached_summary = _load_cache()
    if cached_summary:
        return cached_summary

    if indices:
        summary = generate_ai_summary(indices, regime, mkt_stat)
        if summary:
            # Update cache with summary
            existing_news, _ = _load_cache()
            _save_cache(existing_news or [], summary)
            return summary
    return ""


def fetch_stock_news_summary(client, symbols: list[str], date_str: str = None) -> dict[str, list[str]]:
    result = {}
    if client is None or not symbols:
        return result
    for sym in symbols[:5]:
        try:
            code = f"{sym}.SH" if sym.startswith("6") else f"{sym}.SZ"
            df = client.pro.news(ts_code=code, limit=3, fields="title")
            result[sym] = df["title"].tolist()[:3] if df is not None and not df.empty else []
        except Exception:
            result[sym] = []
    return result
