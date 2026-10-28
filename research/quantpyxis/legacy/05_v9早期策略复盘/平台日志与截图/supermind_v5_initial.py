# 量化一 v5：MOM80/PV20 + VOL20 + volume defense + Michigan cap
# Research only; sentiment OFF. Market-volume rule uses CSI300 proxy.
# 信号以月初前一交易日为截止，不读取当日收盘价/全日量来下单。
# 费用/成交/T+1/企业行动依赖平台实现，未经独立逐项对账。
import math
import pandas as pd


def momentum_score(closes):
    if len(closes) != 253:
        raise ValueError('Need exactly 253 consecutive sessions')
    values = [float(x) for x in closes]
    if any(not math.isfinite(x) or x <= 0 for x in values):
        raise ValueError('Invalid history')
    return values[-22] / values[0] - 1.0


def init(context):
    set_benchmark('000300.SH')
    # 研究假设：佣金2bps；平台另计税费，须在成交明细核实。
    set_commission(PerShare(type='stock', cost=0.0002))
    # 平台PriceSlippage按双边总比例：0.001约为每侧5bps。
    set_slippage(PriceSlippage(0.001))
    set_volume_limit(0.10, 0.10)
    context.entries = {}
    context.ma_targets = {}
    context.halved = {}
    context.rotation_due = False
    context.adv = {}
    context.risk_cap = 1.0
    log.info('V5 open proxies only; NOT Barra replica; fundamental/analyst/industry factors unavailable')
    context.pending = None
    context.signal_date = None
    context.target_vol = 0.20
    context.risk_exposure = 0.0
    context.last_month = None
    context.defensive = False
    context.last_cap = None
    context.normal_targets = []
    context.dividend_etf = '510880.SH'
    context.volume_threshold = 0.50  # Frozen research value; no future-period recalibration
    log.info('V5 threshold frozen={} sentiment OFF; MOM80/PV20'.format(context.volume_threshold))
    log.info('量化一 v5: MOM80/PV20; top3; vol20%; past60d; sentiment OFF')
    log.info('Research only. Platform fees, matching and corporate actions require audit.')


def before_trading(context):
    context.pending = None
    today = get_datetime().strftime('%Y%m%d')
    days = get_trade_days(end_date=today, count=2)
    if len(days) < 2:
        return
    previous = pd.Timestamp(days[-2])
    # 仅每月第一个交易日调仓；从月中开始的回测也不临时入场。
    monthly = previous.strftime('%Y%m') != today[:6] and context.last_month != today[:6]
    end = previous.strftime('%Y%m%d')
    refresh_ma(context, today, end)
    market = get_price(['000300.SH'], None, end, '1d', ['close', 'volume'],
                       skip_paused=False, fq='pre', bar_count=253, is_panel=False)['000300.SH']
    if len(market) != 253 or pd.Timestamp(market.index[-1]).strftime('%Y%m%d') != end:
        raise ValueError('Market history incomplete')
    trigger, volume_drop = volume_signal(market['volume'].tolist(), context.volume_threshold)
    was_defensive = context.defensive
    # Enter on any session; exit only at next monthly rebalance with trigger absent.
    context.defensive = trigger or (context.defensive and not monthly)
    inflation, release = inflation_asof(today)
    cap = 1.0 if inflation < 4.0 else 0.70
    cap_changed = context.last_cap != cap
    context.last_cap = cap
    if context.defensive:
        if monthly or not was_defensive or cap_changed:
            context.pending = [context.dividend_etf]
            context.risk_exposure = 0.50
            context.last_month = today[:6]
            log.info('DEFENSE {} drop={:.6f} threshold={} ETF50 CASH50 inflation={} release={}'.format(end, volume_drop, context.volume_threshold, inflation, release))
        return
    if not monthly:
        if cap_changed and context.normal_targets:
            context.pending = list(context.normal_targets)
            context.risk_exposure = min(context.risk_exposure, cap)
        return
    expected = pd.DatetimeIndex(get_trade_days(end_date=end, count=253)).normalize()
    if len(expected) != 253:
        raise ValueError('Insufficient trading calendar')
    universe = [s for s in sorted(get_index_stocks('000300.SH', date=end)) if not s.startswith(('688', '689'))]
    if not universe:
        raise ValueError('Historical CSI300 membership unavailable')
    prices = get_price(universe, None, end, '1d', ['close', 'volume'],
                       skip_paused=False, fq='pre', bar_count=253, is_panel=False)
    ranked = []
    excluded = 0
    for symbol in universe:
        frame = prices.get(symbol)
        if frame is None or len(frame) != 253:
            excluded += 1
            continue
        frame = frame.copy()
        frame.index = pd.DatetimeIndex(frame.index).normalize()
        if not frame.index.equals(expected):
            excluded += 1
            continue
        try:
            score = momentum_score(frame['close'].tolist())
            volumes = [float(x) for x in frame['volume']]
            if any(not math.isfinite(x) or x <= 0 for x in volumes):
                raise ValueError('Missing or paused history')
        except (ValueError, TypeError):
            excluded += 1
            continue
        pv = price_volume_score(frame['close'].tolist(), volumes)
        ranked.append((symbol, score, pv))
    factors = pd.DataFrame(ranked, columns=['symbol', 'mom', 'pv']).set_index('symbol')
    factor_ranks = factors.rank(method='average', pct=True)
    mixed = 0.80 * factor_ranks['mom'] + 0.20 * factor_ranks['pv']
    ranked = [(symbol, float(mixed[symbol])) for symbol in mixed.index]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    context.pending = [symbol for symbol, score in ranked[:3]]
    # 等权候选组合的历史收益波动代理；仅使用截止日及更早行情。
    # 不用已经减仓账户的波动来决定下一次仓位，避免反馈。
    context.risk_exposure = 0.0
    estimated_vol = None
    if context.pending:
        close_matrix = pd.concat([prices[s]['close'].rename(s) for s in context.pending], axis=1).iloc[-61:]
        returns = close_matrix.pct_change(fill_method=None).dropna()
        if len(returns) == 60:
            estimated_vol = float(returns.mean(axis=1).std(ddof=1)) * math.sqrt(252.0)
            context.risk_exposure = volatility_exposure(estimated_vol, context.target_vol)
    betas = []
    residual_caps = []
    tail_caps = []
    for symbol in context.pending:
        frame = prices[symbol]
        metrics = risk_metrics(frame['close'].iloc[-61:].tolist(), market['close'].iloc[-61:].tolist())
        context.adv[symbol] = float(frame['volume'].iloc[-20:].mean())
        betas.append(max(0.0, metrics['beta']))
        # Per-name residual volatility contribution target <=6% annual proxy.
        residual_caps.append(0.06 / max(metrics['residual_vol'], 0.000001))
        # Per-name historical tail loss contribution <=0.5% NAV/day proxy.
        tail_caps.append(0.005 / max(metrics['tail_loss'], 0.000001))
        log.info('RISK_FACTORS {} {} {}'.format(end, symbol, metrics))
    beta_cap = 1.0 / max(1.0, sum(betas)/len(betas)) if betas else 0.0
    n = len(context.pending)
    context.risk_cap = min(1.0, 0.15*n, beta_cap,
                           min(residual_caps)*n if n else 0.0,
                           min(tail_caps)*n if n else 0.0)
    context.risk_exposure = min(context.risk_exposure, context.risk_cap)
    log.info('RISK_CAP {} beta={} cap={} exposure={}'.format(end, beta_cap, context.risk_cap, context.risk_exposure))
    log.info('RISK {} estimated_vol={} exposure={:.6f} cash_target={:.6f}'.format(end, estimated_vol, context.risk_exposure, 1.0-context.risk_exposure))
    context.risk_exposure = min(context.risk_exposure, cap)
    context.normal_targets = list(context.pending)
    log.info('NORMAL inflation={} release={} cap={} exposure={:.6f}'.format(inflation, release, cap, context.risk_exposure))
    context.signal_date = end
    context.last_month = today[:6]
    log.info('Signal={} eligible={} excluded={} top={}'.format(end, len(ranked), excluded, ranked[:3]))


def _can_trade(bar, side):
    if bar.is_paused or (side == 'buy' and bar.is_st):
        return False
    opening = float(bar.open)
    if not math.isfinite(opening) or opening <= 0:
        return False
    # 保守阻止开盘封板方向；不以日内最高/最低/收盘预测成交。
    limit = float(bar.high_limit if side == 'buy' else bar.low_limit)
    if math.isfinite(limit) and limit > 0:
        if side == 'buy' and opening >= limit:
            return False
        if side == 'sell' and opening <= limit:
            return False
    return True


def handle_bar(context, bar_dict):
    account = context.portfolio.stock_account
    holdings = account.positions
    opening_equity = account.available_cash + account.frozen_cash
    for symbol in holdings:
        opening_equity += holdings[symbol].amount * float(bar_dict[symbol].open)
    # Daily opening check includes all stock/ETF holdings. Intraday gaps and
    # blocked sells cannot guarantee a hard cash floor; retry next session.
    invested = opening_equity - account.available_cash - account.frozen_cash
    limit = 0.50 if context.defensive else (context.last_cap if context.last_cap is not None else 1.0)
    if not context.defensive:
        limit = min(limit, context.risk_cap)
    foreign = context.defensive and any(s != context.dividend_etf and holdings[s].amount > 0 for s in holdings)
    actual_weights = [holdings[x].amount * float(bar_dict[x].open) / opening_equity for x in holdings] if opening_equity > 0 else []
    if context.pending is not None:
        total_weight = sum(actual_weights)
        hhi = sum((x/total_weight)**2 for x in actual_weights) if total_weight > 0 else 0.0
        log.info('ACTUAL_CONCENTRATION weights={} risky_sleeve_HHI={}'.format(actual_weights, hhi))
    risk_override = context.defensive or invested > limit * opening_equity + 0.01 or (not context.defensive and any(x > 0.15 + 0.000001 for x in actual_weights))
    if context.pending is None and (context.rotation_due or any(symbol in holdings and holdings[symbol].amount > context.ma_targets[symbol] for symbol in context.ma_targets)):
        context.pending = list(context.normal_targets) if not context.defensive else [context.dividend_etf]
    if context.pending is None:
        if invested > limit * opening_equity + 0.01 or foreign or (not context.defensive and any(x > 0.15 + 0.000001 for x in actual_weights)):
            context.pending = [context.dividend_etf] if context.defensive else list(context.normal_targets)
            context.risk_exposure = min(context.risk_exposure, limit)
            log.info('CASH_GUARD invested={} equity={} limit={} retry=True'.format(invested, opening_equity, limit))
        elif context.defensive and not any(s == context.dividend_etf and holdings[s].amount > 0 for s in holdings):
            context.pending = [context.dividend_etf]
            context.risk_exposure = 0.50
            log.info('DEFENSE ETF entry retry')
        else:
            return
    targets = context.pending
    context.pending = None
    weight = context.risk_exposure / len(targets) if targets else 0.0
    context.rotation_due = False
    order_plan = []
    for symbol in sorted(set(holdings) | set(targets)):
        target = weight if symbol in targets else 0.0
        position = holdings[symbol] if symbol in holdings else None
        current_value = position.amount * float(bar_dict[symbol].open) if position else 0.0
        if symbol in context.ma_targets:
            target = min(target, context.ma_targets[symbol] * float(bar_dict[symbol].open) / opening_equity)
        if target > 0 and not context.defensive:
            # No redistribution of inaccessible allocation. Limit each buy trade
            # to 1% of previous20-session mean volume; price uses today's open.
            liquidity_budget = context.adv.get(symbol, 0.0) * 0.01 * float(bar_dict[symbol].open)
            target = min(target, 0.15, (current_value + liquidity_budget)/opening_equity)
            if current_value == 0 and target * opening_equity < 100 * float(bar_dict[symbol].open) + 5.0:
                log.info('LOT_REJECT_KEEP_CASH {}'.format(symbol))
                continue
        delta = target * opening_equity - current_value
        if abs(delta) < 0.01:
            continue
        side = 'sell' if delta < 0 else 'buy'
        if side == 'sell' and symbol not in context.ma_targets and not risk_override:
            age = len(get_trade_days(start_date=context.entries.get(symbol, get_datetime().strftime('%Y%m%d')), end_date=get_datetime().strftime('%Y%m%d'))) - 1
            if age < 7:
                context.rotation_due = True
                log.info('MIN_HOLD_BLOCK {} age={}'.format(symbol, age))
                continue
        if side == 'buy' and symbol in context.ma_targets:
            log.info('MA_NO_REBUY {}'.format(symbol))
            continue
        if side == 'sell' and position.available_amount <= 0:
            log.info('BLOCKED T+1 {}'.format(symbol))
            continue
        if not _can_trade(bar_dict[symbol], side):
            log.info('BLOCKED {} {}'.format(side, symbol))
            continue
        order_plan.append((side, symbol, target))
    # 先减仓再加仓；平台再约束整手、可卖数量与现金，可能拒单或部分成交。
    for side, symbol, target in sorted(order_plan, key=lambda x: (x[0] != 'sell', x[1])):
        result = order_target_percent(symbol, target)
        log.info('TARGET {} {} weight={:.6f} order={}'.format(side, symbol, target, result))
        if side == 'buy' and result is not None:
            # Reset entire holding clock on add-on buys, conservatively.
            context.entries[symbol] = get_datetime().strftime('%Y%m%d')


def volatility_exposure(estimated_vol, target_vol):
    if not math.isfinite(estimated_vol) or estimated_vol <= 0:
        return 0.0
    if not math.isfinite(target_vol) or target_vol <= 0:
        raise ValueError('Invalid target volatility')
    return min(1.0, target_vol / estimated_vol)


# value in percent units (4.7 means 4.7%); earliest usable China calendar date.
# Every source is recorded in reports/V3_DESIGN.md. September final unavailable
# in retrieved archive: retain September preliminary; do not invent a release.
INFLATION_RELEASES = [
    ('20250927', 4.7, 'Sep26-2025-final'),
    ('20251025', 4.6, 'Oct24-2025-final'),
    ('20251122', 4.5, 'Nov21-2025-final'),
    ('20251220', 4.2, 'Dec19-2025-final'),
    ('20260124', 4.0, 'Jan23-2026-final'),
    ('20260221', 3.4, 'Feb20-2026-final'),
    ('20260328', 3.8, 'Mar27-2026-final'),
    ('20260425', 4.7, 'Apr24-final'),
    ('20260509', 4.5, 'May08-prelim'),
    ('20260523', 4.8, 'May22-final'),
    ('20260613', 4.6, 'Jun12-prelim'),
    ('20260627', 4.6, 'Jun26-final'),
    ('20260718', 4.2, 'Jul17-prelim'),
    ('20260801', 4.2, 'Jul31-final'),
    ('20260815', 4.3, 'Aug14-prelim'),
    ('20260829', 4.0, 'Aug28-final'),
    ('20260912', 4.6, 'Sep11-prelim'),
    ('20261010', 4.7, 'Oct09-prelim'),
]


def inflation_asof(today):
    rows = [row for row in INFLATION_RELEASES if row[0] <= today]
    if not rows:
        raise ValueError('No point-in-time macro observation; supported run May-Oct2026')
    date, value, label = rows[-1]
    if (pd.Timestamp(today) - pd.Timestamp(date)).days > 45:
        raise ValueError('Macro input stale')
    return value, label


def price_volume_score(closes, volumes):
    # Volume-weighted daily return minus simple daily return, last 20 sessions.
    # Positive means relatively more volume accompanied rising prices.
    c = [float(x) for x in closes[-21:]]
    v = [float(x) for x in volumes[-20:]]
    if len(c) != 21 or len(v) != 20 or any(not math.isfinite(x) or x <= 0 for x in c + v):
        raise ValueError('Invalid price-volume history')
    r = [c[i + 1] / c[i] - 1.0 for i in range(20)]
    return sum(a * b for a, b in zip(r, v)) / sum(v) - sum(r) / 20.0


def volume_signal(volumes, threshold):
    v = [float(x) for x in volumes[-11:]]
    if len(v) != 11 or any(not math.isfinite(x) or x <= 0 for x in v):
        raise ValueError('Invalid market volumes')
    drop = 1.0 - v[-1] / v[0]
    falling = all(v[i] < v[i-1] for i in range(8, 11))
    return falling and drop >= threshold, drop


def proxy_metrics(market, etf, threshold, start, end):
    # Simplified daily-rebalanced 70% index vs 50% dividend ETF proxy.
    # Signal t close -> weights at t+1 open -> t+2 open return.
    # Not the stock-selection strategy, not an independently audited total return.
    dates = pd.DatetimeIndex(market.index)
    values, triggers = [], 0
    previous_weights = (0.0, 0.0)
    for i in range(10, len(market)-2):
        entry, exit_date = dates[i+1], dates[i+2]
        if entry < pd.Timestamp(start) or exit_date > pd.Timestamp(end):
            continue
        defensive, _ = volume_signal(market['volume'].iloc[i-10:i+1].tolist(), threshold)
        weights = (0.0, 0.5) if defensive else (0.7, 0.0)
        ri = float(market['open'].iloc[i+2] / market['open'].iloc[i+1] - 1.0)
        re = float(etf['open'].iloc[i+2] / etf['open'].iloc[i+1] - 1.0)
        # Research cost assumption 20bps per one-way traded notional.
        turnover = sum(abs(a-b) for a, b in zip(weights, previous_weights))
        values.append(weights[0]*ri + weights[1]*re - 0.002*turnover)
        previous_weights = weights
        triggers += int(defensive)
    if len(values) < 100:
        raise ValueError('Insufficient calibration sample')
    r = pd.Series(values)
    utility = float(r.mean()*252.0 - 1.5*r.var(ddof=1)*252.0)
    nav = (1.0 + r).cumprod()
    dd = float((nav / nav.cummax() - 1.0).min())
    return utility, float(nav.iloc[-1]-1.0), dd, triggers, len(values)


def calibrate_volume_threshold():
    data = get_price(['000300.SH', '510880.SH'], '20191201', '20260430', '1d',
                     ['open', 'close', 'volume'], skip_paused=False, fq='pre', is_panel=False)
    market, etf = data['000300.SH'], data['510880.SH']
    market, etf = market.align(etf, join='inner', axis=0)
    for frame in (market, etf):
        if frame[['open', 'close', 'volume']].isnull().any().any() or (frame[['open', 'close', 'volume']] <= 0).any().any():
            raise ValueError('Invalid calibration history')
    candidates = []
    for threshold in [0.10, 0.20, 0.30, 0.40, 0.50]:
        train = proxy_metrics(market, etf, threshold, '20200101', '20241231')
        valid = proxy_metrics(market, etf, threshold, '20250101', '20260430')
        log.info('CAL threshold={} train={} validation={}'.format(threshold, train, valid))
        candidates.append((train[0], threshold))
    # Training utility only. Validation never reselects the threshold.
    selected = sorted(candidates, key=lambda x: (-x[0], -x[1]))[0][1]
    benchmark = proxy_metrics(market, etf, 2.0, '20250101', '20260430')
    log.info('CAL selected={} validation_no_defense={}'.format(selected, benchmark))
    return selected



def risk_metrics(closes, market_closes):
    if len(closes) != 61 or len(market_closes) != 61:
        raise ValueError('Need61 aligned daily closes')
    if any(not math.isfinite(float(x)) or float(x) <= 0 for x in list(closes)+list(market_closes)):
        raise ValueError('Invalid risk history')
    r = pd.Series([closes[i+1]/closes[i]-1.0 for i in range(60)])
    m = pd.Series([market_closes[i+1]/market_closes[i]-1.0 for i in range(60)])
    variance = float(m.var(ddof=1))
    if variance <= 0:
        raise ValueError('Constant market history')
    beta = float(r.cov(m)) / variance
    residual = r - (float(r.mean()) - beta*float(m.mean())) - beta*m
    downside = math.sqrt(sum(min(float(x),0.0)**2 for x in r)/60.0)*math.sqrt(252.0)
    tail_loss = max(0.0, -sum(sorted(r.tolist())[:3])/3.0)
    return {'beta':beta, 'residual_vol':float(residual.std(ddof=1))*math.sqrt(252.0),
            'downside_vol':downside, 'tail_loss':tail_loss}


def ma_action(closes, half_already):
    if len(closes) != 30 or any(not math.isfinite(float(x)) or x <= 0 for x in closes):
        raise ValueError('Need30 valid completed closes')
    last = float(closes[-1])
    ma5, ma13, ma30 = [sum(closes[-n:])/n for n in [5,13,30]]
    if last < ma13 or last < ma30:
        return 'EXIT', ma5, ma13, ma30
    if last < ma5 and not half_already:
        return 'HALF', ma5, ma13, ma30
    return 'HOLD', ma5, ma13, ma30


def half_remaining(amount):
    # Sell whole lots only; 100 shares cannot be halved. Full exits may clear odd lots.
    sell = int(float(amount) / 2.0 / 100.0) * 100
    return float(amount) - sell


def refresh_ma(context, today, end):
    positions = context.portfolio.stock_account.positions
    held = [s for s in positions if positions[s].amount > 0]
    for symbol in list(context.entries):
        if symbol not in held:
            context.entries.pop(symbol, None)
    # Keep flat MA bans until next monthly selection, preventing immediate re-entry.
    watch = sorted(set(held) | set(context.ma_targets))
    if not watch:
        return
    history = get_price(watch, None, end, '1d', ['close'], skip_paused=False,
                        fq='pre', bar_count=30, is_panel=False)
    for symbol in watch:
        frame = history.get(symbol)
        if frame is None or len(frame) != 30 or pd.Timestamp(frame.index[-1]).strftime('%Y%m%d') != end:
            raise ValueError('Incomplete MA history')
        closes = [float(x) for x in frame['close']]
        action, m5, m13, m30 = ma_action(closes, context.halved.get(symbol, False))
        if closes[-1] >= max(m5,m13,m30):
            context.halved[symbol] = False
            context.ma_targets.pop(symbol, None)
        if symbol not in held:
            continue
        if symbol not in context.entries:
            context.entries[symbol] = today  # Conservative unknown-entry clock.
        amount = float(positions[symbol].amount)
        if action == 'EXIT':
            context.ma_targets[symbol] = 0.0
        elif action == 'HALF':
            target = half_remaining(amount)
            if target >= amount:
                log.info('MA_HALF_LOT_BLOCK {} amount={}'.format(symbol, amount))
            else:
                context.ma_targets[symbol] = min(context.ma_targets.get(symbol, amount), target)
                context.halved[symbol] = True
        if action != 'HOLD':
            log.info('MA_SIGNAL {} {} close={} ma5={} ma13={} ma30={} target_shares={}'.format(end, symbol, closes[-1],m5,m13,m30,context.ma_targets.get(symbol)))
