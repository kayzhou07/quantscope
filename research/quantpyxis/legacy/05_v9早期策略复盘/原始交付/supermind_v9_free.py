# -*- coding: utf-8 -*-
"""SuperMind 平台策略代码 v9 FREE（自由版）

用途：把本地自由版规则搬回同花顺 SuperMind 编辑器，供平台上复跑与保存。
规则：
  * 股票池：主板 + 创业板（600/601/603/605/000/001/002/003/300/301），排除科创板 688/689
    与北交所；ST/停牌/当日涨停开盘不可买。
  * 评分：完整月线 12-1 动量，P(m-1)/P(m-12)-1，权重 100%，只用信号时点之前的数据。
  * 调仓：每年 1/4/7/10 月首个交易日开盘；两次季度复核之间不下单。
  * 仓位：目标 100% 股票；按排名整手买入，最多 4 只；若等权预算买不到一手但现金够，
    允许买一手（"一手底线"），因此个别标的可能超过等权权重。
  * 删除：旧的 200 股×4% 资产门槛、Beta/残差/尾部风险上限、宏观与缩量防守全部不再使用。
  * 费用/成交/停牌/T+1/送转分红依赖平台实现；本文件不声称已经平台编译或回测。
"""

import math

import pandas as pd

MAX_NAMES = 4


def init(context):
    set_benchmark('000300.SH')
    set_commission(PerShare(type='stock', cost=0.0002))
    set_slippage(PriceSlippage(0.001))
    set_volume_limit(0.10, 0.10)
    context.pending = None
    context.audit = {}
    context.last_signal = None
    log.info('v9 FREE: main+ChiNext, monthly 12-1 MOM100, quarterly rebalance, 100% target, '
             'no old funding gate / risk-cap stack / macro defense')


def is_quarter_review(today, previous):
    return previous.strftime('%Y%m') != today[:6] and int(today[4:6]) in [1, 4, 7, 10]


def valid_board(symbol):
    code = symbol.split('.')[0]
    if code.startswith(('688', '689', '8', '4', '9')):
        return False
    return code.startswith(('600', '601', '603', '605', '000', '001', '002', '003', '300', '301'))


def build_universe(end):
    """主板+创业板股票池。优先用全市场证券列表，失败时回退到三个指数成分的并集。"""
    pool = set()
    try:
        secs = get_all_securities(types=['stock'], date=end)
        pool |= set(secs.index)
    except Exception as exc:  # noqa: BLE001
        log.info('get_all_securities unavailable: {}'.format(exc))
    for index in ['000300.SH', '000905.SH', '399006.SZ']:
        try:
            pool |= set(get_index_stocks(index, date=end))
        except Exception as exc:  # noqa: BLE001
            log.info('index {} unavailable: {}'.format(index, exc))
    return [s for s in sorted(pool) if valid_board(s)]


def completed_months(frame, execution_date):
    work = frame.copy()
    work.index = pd.DatetimeIndex(work.index)
    current = pd.Timestamp(execution_date).to_period('M')
    work = work[work.index.to_period('M') < current]
    months = work.groupby(work.index.to_period('M')).agg({'close': 'last'})
    if len(months) < 13:
        raise ValueError('Need13 complete monthly bars')
    expected = pd.period_range(current - 13, current - 1, freq='M')
    if not months.iloc[-13:].index.equals(expected):
        raise ValueError('Missing monthly bar')
    return months


def monthly_momentum(months):
    c = [float(x) for x in months['close']]
    if any(not math.isfinite(x) or x <= 0 for x in c[-13:]):
        raise ValueError('Invalid monthly history')
    return c[-2] / c[-13] - 1.0


def before_trading(context):
    context.pending = None
    today = get_datetime().strftime('%Y%m%d')
    days = get_trade_days(end_date=today, count=2)
    if len(days) < 2:
        return
    previous = pd.Timestamp(days[-2])
    if not is_quarter_review(today, previous):
        return
    end = previous.strftime('%Y%m%d')
    universe = build_universe(end)
    if len(universe) < 100:
        raise ValueError('Universe too small: {}'.format(len(universe)))
    history_start = (previous.to_period('M') - 26).start_time.strftime('%Y%m%d')
    prices = get_price(universe, history_start, end, '1d', ['close'],
                       skip_paused=False, fq='pre', is_panel=False)
    ranked = []
    excluded = 0
    for symbol in universe:
        frame = prices.get(symbol)
        if frame is None or frame.empty:
            excluded += 1
            continue
        try:
            months = completed_months(frame, today)
            ranked.append((symbol, monthly_momentum(months)))
        except (ValueError, TypeError):
            excluded += 1
    ranked.sort(key=lambda item: (-item[1], item[0]))
    # 排除 ST 与非正常交易状态（平台字段）
    context.pending = []
    for symbol, score in ranked:
        if len(context.pending) >= MAX_NAMES:
            break
        try:
            sec = get_security_info(symbol)
            if 'ST' in str(sec.display_name) or '退' in str(sec.display_name):
                continue
        except Exception:  # noqa: BLE001
            pass
        context.pending.append(symbol)
    context.last_signal = end
    log.info('V9_SIGNAL {} eligible={} excluded={} selected={}'.format(end, len(ranked), excluded, context.pending))
    log.info('V9_TOP {}'.format(ranked[:8]))


def handle_bar(context, bar_dict):
    if context.pending is None:
        return
    targets = list(context.pending)
    context.pending = None
    account = context.portfolio.stock_account
    holdings = account.positions
    equity = account.available_cash + account.frozen_cash
    for symbol in holdings:
        equity += holdings[symbol].amount * float(bar_dict[symbol].open)
    # 先卖不在目标里的持仓
    for symbol in list(holdings):
        if holdings[symbol].amount > 0 and symbol not in targets:
            order_target_percent(symbol, 0.0)
            context.audit['sell'] = context.audit.get('sell', 0) + 1
    # 再按排名分配：剩余子弹 / 剩余名额
    bought = 0
    for symbol in targets:
        if symbol not in bar_dict:
            continue
        px = float(bar_dict[symbol].open)
        if not math.isfinite(px) or px <= 0:
            continue
        if bar_dict[symbol].is_paused or bar_dict[symbol].is_st:
            continue
        try:
            if px >= float(bar_dict[symbol].high_limit):
                continue
        except Exception:  # noqa: BLE001
            pass
        remaining = len(targets) - bought
        cash = float(account.available_cash)
        budget = cash / remaining if remaining else 0.0
        weight = budget / equity if equity > 0 else 0.0
        if weight * equity < 100 * px and cash >= 100 * px:
            weight = 100 * px / equity  # 一手底线
        if weight <= 0:
            continue
        order_target_percent(symbol, weight)
        bought += 1
        context.audit['buy'] = context.audit.get('buy', 0) + 1
        log.info('V9_ORDER {} weight={:.6f} price={}'.format(symbol, weight, px))
