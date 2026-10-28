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
