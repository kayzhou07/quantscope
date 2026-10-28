"""
AI市场情绪分析模块 — 仅用于展示，不参与选股决策
"""
import json, logging, ssl, time, warnings
import requests
from requests.adapters import HTTPAdapter
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_PROXY

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

_ai_session = None
_ai_available = None


class _ProxyAdapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        kw["cert_reqs"] = ssl.CERT_NONE
        kw["assert_hostname"] = False
        return super().init_poolmanager(*a, **kw)
    def proxy_manager_for(self, *a, **kw):
        kw["cert_reqs"] = ssl.CERT_NONE
        kw["assert_hostname"] = False
        return super().proxy_manager_for(*a, **kw)


def _build_session():
    s = requests.Session()
    s.trust_env = False
    s.verify = False
    if OPENAI_PROXY:
        s.proxies = {"http": OPENAI_PROXY, "https": OPENAI_PROXY}
    s.mount("https://", _ProxyAdapter(max_retries=0))
    return s


def _check_available():
    global _ai_available, _ai_session
    if _ai_available is not None:
        return _ai_available
    if not OPENAI_API_KEY:
        _ai_available = False
        return False
    s = _build_session()
    try:
        r = s.get(f"{OPENAI_BASE_URL}/models", headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, timeout=10)
        _ai_available = r.status_code == 200
        if _ai_available:
            _ai_session = s
            logger.info("AI情绪分析已连接")
    except Exception:
        _ai_available = False
    return _ai_available


PROMPT = """你是一个A股短线分析师。以下是今日通过"缩量连板"策略筛选出的股票，请简要分析市场整体情绪：

{stocks_text}

请输出JSON（不要任何额外内容）：
{{"overall_sentiment": "乐观/中性/谨慎", "market_note": "一句话总结当前连板股的整体特征和市场氛围(30字以内)", "stocks": [{{"symbol": "代码", "note": "一句话点评(15字以内)"}}]}}"""


def analyze_sentiment(stocks: list[dict]) -> dict:
    """对选出的股票做AI情绪分析，失败时返回空结果"""
    if not _check_available() or not stocks:
        return {"overall_sentiment": "", "market_note": "", "stocks": []}

    stocks_text = "\n".join(f"- {s['symbol_name']}({s['symbol']}) 买入{s['volume']}股" for s in stocks)
    prompt = PROMPT.format(stocks_text=stocks_text)

    try:
        resp = _ai_session.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": OPENAI_MODEL, "messages": [
                {"role": "system", "content": ""},
                {"role": "user", "content": prompt},
            ], "temperature": 0.3, "max_tokens": 300},
            timeout=25,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(content)
    except Exception as e:
        logger.warning(f"AI情绪分析失败: {e}")

    return {"overall_sentiment": "", "market_note": "", "stocks": []}
