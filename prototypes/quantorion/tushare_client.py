import time
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

# 绕过系统代理（tushare 国内直连，走代理反而失败）
for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
    os.environ.pop(key, None)
os.environ["NO_PROXY"] = "api.tushare.pro,api.waditu.com"

import pandas as pd
import tushare as ts

from config import (
    TUSHARE_TOKEN, CACHE_TTL_SECONDS, MIN_LIST_DAYS,
    PREFERRED_INDUSTRIES, MAX_MARKET_CAP, DEFAULT_LIMIT_UP_PCT,
    REGIME_MA_FAST, REGIME_MA_SLOW, REGIME_CONFIRM_DAYS,
    MAX_STOCKS,
)

logger = logging.getLogger(__name__)


class TushareClient:
    def __init__(self):
        ts.set_token(TUSHARE_TOKEN)
        self.pro = ts.pro_api()
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def _cached(self, key: str, fetcher, ttl: int = CACHE_TTL_SECONDS) -> pd.DataFrame:
        now = time.time()
        if key in self._cache:
            ts_val, df = self._cache[key]
            if now - ts_val < ttl:
                return df.copy()
        try:
            df = fetcher()
        except Exception as e:
            logger.warning(f"Tushare API调用失败 (key={key}): {e}")
            # 不缓存失败结果，返回空DataFrame让调用方降级处理
            return pd.DataFrame()
        if df is not None and not df.empty:
            self._cache[key] = (now, df.copy())
        else:
            df = pd.DataFrame()
        return df

    # ── trading calendar ────────────────────────────────────────────

    def is_trading_day(self, date_str: str) -> bool:
        """检查是否为交易日（日历缺失时降级为周一至周五）"""
        try:
            df = self._cached(
                f"trade_cal_{date_str}",
                lambda: self.pro.trade_cal(
                    exchange="SSE", start_date=date_str, end_date=date_str
                ),
            )
            if df.empty:
                # 交易日历无数据，降级：周一至周五视为交易日
                dt = datetime.strptime(date_str, "%Y%m%d")
                return dt.weekday() < 5
            return df.iloc[0]["is_open"] == 1
        except Exception as e:
            logger.warning(f"交易日检查失败({date_str}): {e}")
            return True  # 网络异常时假定为交易日

    def get_recent_trading_days(self, base_date: str, count: int) -> list[str]:
        """获取 base_date 及之前的 count 个交易日（日历缺失时降级为周一至周五）"""
        start = (datetime.strptime(base_date, "%Y%m%d") - timedelta(days=max(count * 3, 30))
                 ).strftime("%Y%m%d")
        try:
            df = self._cached(
                f"trade_cal_range_{start}_{base_date}",
                lambda: self.pro.trade_cal(
                    exchange="SSE", start_date=start, end_date=base_date
                ),
            )
            if df.empty:
                # 降级：按周一到周五生成交易日列表
                end_dt = datetime.strptime(base_date, "%Y%m%d")
                days = []
                cur = end_dt
                while len(days) < count:
                    if cur.weekday() < 5:
                        days.append(cur.strftime("%Y%m%d"))
                    cur -= timedelta(days=1)
                return days
            open_days = df[df["is_open"] == 1]["cal_date"].sort_values(ascending=False)
            return open_days.head(count).tolist()
        except Exception as e:
            logger.warning(f"获取交易日失败: {e}")
            return []

    # ── stock list ───────────────────────────────────────────────────

    def get_all_stocks(self) -> pd.DataFrame:
        """获取全A股列表（排除北交所、ST）"""
        def fetcher():
            df = self.pro.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,symbol,name,area,industry,list_date",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            # 排除北交所
            df = df[~df["ts_code"].str.endswith(".BJ")]
            # 排除 ST
            df = df[~df["name"].str.contains("ST", na=False)]
            # 上市满 MIN_LIST_DAYS 天
            df["list_date"] = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce")
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=MIN_LIST_DAYS)
            df = df[df["list_date"] <= cutoff]
            return df

        return self._cached("all_stocks", fetcher, ttl=CACHE_TTL_SECONDS)

    # ── daily data ───────────────────────────────────────────────────

    def get_daily_batch(self, trade_date: str) -> pd.DataFrame:
        """获取某个交易日全市场日线数据"""
        def fetcher():
            df = self.pro.daily(trade_date=trade_date)
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        return self._cached(f"daily_{trade_date}", fetcher)

    def get_daily_for_stocks(self, ts_codes: list[str], start_date: str,
                             end_date: str) -> pd.DataFrame:
        """批量获取多只股票的区间日线"""
        all_frames = []
        batch_size = 500
        for i in range(0, len(ts_codes), batch_size):
            batch = ts_codes[i:i + batch_size]
            try:
                df = self.pro.daily(
                    ts_code=",".join(batch),
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is not None and not df.empty:
                    all_frames.append(df)
            except Exception as e:
                logger.warning(f"批量获取日线失败 (batch {i}): {e}")
        if not all_frames:
            return pd.DataFrame()
        return pd.concat(all_frames, ignore_index=True)

    def get_multi_day_daily(self, trade_dates: list[str]) -> dict[str, pd.DataFrame]:
        """获取多个交易日的日线"""
        result = {}
        for d in trade_dates:
            df = self.get_daily_batch(d)
            if not df.empty:
                result[d] = df
        return result

    # ── daily_basic（市值、换手率等）─────────────────────────────────

    def get_daily_basic(self, trade_date: str) -> pd.DataFrame:
        def fetcher():
            df = self.pro.daily_basic(trade_date=trade_date)
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        return self._cached(f"daily_basic_{trade_date}", fetcher)

    # ── 北向资金持股 ─────────────────────────────────────────────────

    def get_hk_hold(self, trade_date: str) -> pd.DataFrame:
        """获取沪深港通持股明细（北向资金）"""
        def fetcher():
            try:
                df = self.pro.hk_hold(trade_date=trade_date)
                if df is None or df.empty:
                    return pd.DataFrame()
                return df
            except Exception as e:
                logger.warning(f"北向资金数据获取失败({trade_date}): {e}")
                return pd.DataFrame()
        return self._cached(f"hk_hold_{trade_date}", fetcher)

    def get_north_flow_change(self, ts_codes: list[str], recent_days: list[str]
                              ) -> pd.DataFrame:
        """
        计算北向资金近N日净流入变化
        返回 DataFrame: ts_code, north_flow_score
        """
        if len(recent_days) < 2:
            return pd.DataFrame()
        frames = []
        for d in recent_days[:2]:
            df = self.get_hk_hold(d)
            if df is not None and not df.empty:
                df = df[["ts_code", "vol"]].copy()
                df["trade_date"] = d
                frames.append(df)
        if len(frames) < 2:
            return pd.DataFrame()

        newest, oldest = frames[0], frames[-1]
        merged = newest.merge(oldest, on="ts_code", suffixes=("_new", "_old"), how="inner")
        # vol 单位为万股，变化 > 0 即为净流入
        merged["north_flow_change"] = merged["vol_new"] - merged["vol_old"]
        merged = merged[merged["ts_code"].isin(ts_codes)]
        merged["north_positive"] = merged["north_flow_change"] > 0
        # 归一化：流入变化 / 旧持仓 * 100
        merged["north_flow_pct"] = (merged["north_flow_change"] / merged["vol_old"].replace(0, 1)) * 100
        return merged[["ts_code", "north_flow_change", "north_positive", "north_flow_pct"]]

    # ── 涨跌停价 ─────────────────────────────────────────────────────

    def get_stk_limit(self, trade_date: str) -> pd.DataFrame:
        def fetcher():
            try:
                df = self.pro.stk_limit(trade_date=trade_date)
                if df is None or df.empty:
                    return pd.DataFrame()
                return df
            except Exception as e:
                logger.warning(f"涨跌停数据获取失败({trade_date}): {e}")
                return pd.DataFrame()
        return self._cached(f"stk_limit_{trade_date}", fetcher)

    # ── 新闻 ─────────────────────────────────────────────────────────

    def get_stock_news(self, ts_code: str, limit: int = 3) -> list[str]:
        """获取个股新闻标题（降级容错）"""
        try:
            df = self.pro.news(ts_code=ts_code, limit=limit, fields="title,datetime")
            if df is not None and not df.empty:
                return df["title"].tolist()[:limit]
        except Exception:
            pass
        try:
            df = self.pro.major_news(ts_code=ts_code, limit=limit, fields="title")
            if df is not None and not df.empty:
                return df["title"].tolist()[:limit]
        except Exception:
            pass
        return []

    # ── 工具方法 ─────────────────────────────────────────────────────

    @staticmethod
    def get_limit_up_pct(ts_code: str) -> float:
        """根据股票代码返回涨停幅度"""
        for prefix, pct in DEFAULT_LIMIT_UP_PCT.items():
            if ts_code.startswith(prefix):
                return pct
        return 0.10

    @staticmethod
    def is_preferred_industry(industry: str) -> bool:
        if not isinstance(industry, str):
            return False
        return any(kw in industry for kw in PREFERRED_INDUSTRIES)

    def filter_by_market_cap(self, stocks: pd.DataFrame, trade_date: str
                             ) -> pd.DataFrame:
        """按市值 < MAX_MARKET_CAP 过滤"""
        basic = self.get_daily_basic(trade_date)
        if basic.empty or "total_mv" not in basic.columns:
            logger.warning("无法获取市值数据，跳过市值过滤")
            return stocks
        # total_mv 单位为万元，MAX_MARKET_CAP 为元
        cap_limit_wan = MAX_MARKET_CAP / 10000
        basic = basic[basic["total_mv"] < cap_limit_wan]
        valid_codes = set(basic["ts_code"].unique())
        return stocks[stocks["ts_code"].isin(valid_codes)]

    # ── 市场环境判断（MA10/MA20 + 确认期）────────────────────────────

    def get_market_regime(self, trade_date: str) -> dict:
        """
        基于上证指数 MA10/MA20 判断市场环境，含确认期防假信号。

        返回: {
            'regime': 'normal'|'weak'|'oscillation',
            'max_stocks': int,        # 最大持仓数
            'position_target': float,  # 目标仓位比例
        }
        """
        def fetcher():
            end_dt = datetime.strptime(trade_date, "%Y%m%d")
            start_dt = end_dt - timedelta(days=max(REGIME_MA_SLOW * 2 + 10, 60))
            df = self.pro.index_daily(
                ts_code="000001.SH",
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=trade_date,
                fields="trade_date,close",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.sort_values("trade_date")
            df["ma10"] = df["close"].rolling(window=REGIME_MA_FAST).mean()
            df["ma20"] = df["close"].rolling(window=REGIME_MA_SLOW).mean()
            return df

        cache_key = f"market_regime_{trade_date}"
        df = self._cached(cache_key, fetcher, ttl=CACHE_TTL_SECONDS)

        if df.empty or len(df) < REGIME_MA_SLOW:
            return {"regime": "normal", "max_stocks": MAX_STOCKS,
                    "position_target": 0.90}

        df = df.dropna(subset=["ma10", "ma20"])
        if len(df) < REGIME_CONFIRM_DAYS:
            return {"regime": "normal", "max_stocks": MAX_STOCKS,
                    "position_target": 0.90}

        # 原始日信号
        latest = df.iloc[-1]
        close = float(latest["close"])
        ma10 = float(latest["ma10"])
        ma20 = float(latest["ma20"])

        # 判断最近 N 天的一致性
        recent = df.tail(REGIME_CONFIRM_DAYS)
        below_ma20_count = int((recent["close"] < recent["ma20"]).sum())
        above_ma10_count = int((recent["close"] > recent["ma10"]).sum())

        if below_ma20_count >= REGIME_CONFIRM_DAYS:
            regime = "weak"
            max_s = 1
            target = 0.20
        elif above_ma10_count >= REGIME_CONFIRM_DAYS:
            regime = "normal"
            max_s = MAX_STOCKS
            target = 0.90
        else:
            regime = "oscillation"
            max_s = max(1, MAX_STOCKS - 1)
            target = 0.50

        return {"regime": regime, "max_stocks": max_s, "position_target": target}
