import os
from dotenv import load_dotenv

load_dotenv(override=False)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")
OPENAI_PROXY = os.getenv("OPENAI_PROXY", "")

INITIAL_CAPITAL = 500_000

# 缩量连板参数
LIMIT_UP_MIN_DAYS = 2          # 连板最少天数
VOLUME_DECLINE_RATIO = 1.0     # 缩量比例（<1=缩量）

# 涨停限制比例
DEFAULT_LIMIT_UP_PCT = {
    "60": 0.10, "00": 0.10, "30": 0.20, "68": 0.20,
    "83": 0.30, "87": 0.30, "43": 0.30, "40": 0.30, "8": 0.30,
}

# tushare_client 需要的参数
MIN_LIST_DAYS = 60
MAX_MARKET_CAP = 40_000_000_000
PREFERRED_INDUSTRIES = ["科技", "软件", "半导体", "芯片", "通信", "互联网", "电子",
                         "新能源", "电池", "光伏", "风电", "储能", "锂电", "充电桩",
                         "医药", "生物", "医疗", "器械", "制药", "疫苗", "中药"]
# tushare_client 内部方法需要的参数（缩量连板不直接使用）
MAX_STOCKS = 3
REGIME_MA_FAST = 10
REGIME_MA_SLOW = 20
REGIME_CONFIRM_DAYS = 3
CACHE_TTL_SECONDS = 3600
