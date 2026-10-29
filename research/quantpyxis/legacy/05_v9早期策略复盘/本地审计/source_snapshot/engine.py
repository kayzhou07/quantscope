"""v9 backtest engine: main-board + ChiNext momentum rotation, 100k CNY, 2026-04-01..2026-09-30.

Conventions
- Signal: month-end close of month m (last trading day). Momentum uses monthly closes:
  P(m-1)/P(m-12) - 1  (12-1 momentum, skip the most recent complete month).
- Execution: next trading day's OPEN (T+1), never same-day close.
- Prices: 前复权 (qfq) daily bars from Tencent 自选股 (westock CLI).
- Costs: commission 2.5bp (min 5 CNY), transfer fee 0.1bp both sides,
  stamp duty 5bp on sell, slippage 10bp on execution price.
- Lot: 100 shares. No leverage, no short.
"""
import json, math, os, collections
from loader import load_all

START, END = '2026-04-01', '2026-09-30'
INIT_CASH = 100000.0
COMM, MIN_COMM, TRANSFER, STAMP, SLIP = 0.00025, 5.0, 0.00001, 0.0005, 0.001


def month_of(d):
    return d[:7]


class Market:
    def __init__(self):
        self.panel, self.idx = load_all()
        self.names = {x['code']: x['name'] for x in json.load(open('universe.json', encoding='utf-8'))}
        self.boards = {x['code']: x['board'] for x in json.load(open('universe.json', encoding='utf-8'))}
        self.cal = [b[0] for b in self.idx['sh000300']]
        self.cset = set(self.cal)
        self.bar = {}
        self.dates = {}
        for c, rows in self.panel.items():
            self.bar[c] = {r[0]: r for r in rows}
            self.dates[c] = [r[0] for r in rows]
        self.mclose = {}   # code -> {'YYYY-MM': close}
        self.mlast = {}    # code -> {'YYYY-MM': last date}
        for c, rows in self.panel.items():
            mc, ml = {}, {}
            for r in rows:
                m = month_of(r[0])
                mc[m] = r[2]
                ml[m] = r[0]
            self.mclose[c], self.mlast[c] = mc, ml
        # 20d average amount as of each date
        self.amt20 = {}
        for c, rows in self.panel.items():
            acc = 0.0
            q = collections.deque()
            d = {}
            for r in rows:
                q.append(r[6]); acc += r[6]
                if len(q) > 20:
                    acc -= q.popleft()
                d[r[0]] = acc / len(q)
            self.amt20[c] = d
        self.ma = {}
        for c, rows in self.panel.items():
            cl = [r[2] for r in rows]
            acc, q, d = 0.0, collections.deque(), {}
            for i, r in enumerate(rows):
                acc += cl[i]; q.append(cl[i])
                if len(q) > 120:
                    acc -= q.popleft()
                d[r[0]] = acc / len(q) if len(q) >= 120 else None
            self.ma[c] = d
        self.idxclose = {r[0]: r[2] for r in self.idx['sh000300']}
        self.idxma = {}
        rows = self.idx['sh000300']
        acc, q = 0.0, collections.deque()
        for r in rows:
            acc += r[2]; q.append(r[2])
            if len(q) > 120:
                acc -= q.popleft()
            self.idxma[r[0]] = acc / len(q) if len(q) >= 120 else None

    def prev_month(self, m, k):
        y, mm = int(m[:4]), int(m[5:])
        t = y * 12 + (mm - 1) - k
        return '%04d-%02d' % (t // 12, t % 12 + 1)

    def month_ends(self):
        out = {}
        for d in self.cal:
            out[month_of(d)] = d
        return out


def signal_dates(mk):
    """Last trading day of each month 2026-03..2026-08 -> rebalance in Apr..Sep."""
    me = mk.month_ends()
    months = ['2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08']
    return [me[m] for m in months]


def next_trading_day(mk, d):
    for x in mk.cal:
        if x > d:
            return x
    return None


def eligible(mk, c, t, min_amt, trend):
    b = mk.bar[c].get(t)
    if not b:
        return None
    m = month_of(t)
    m1, m12 = mk.prev_month(m, 1), mk.prev_month(m, 12)
    mc = mk.mclose[c]
    if m1 not in mc or m12 not in mc:
        return None
    if mc[m12] <= 0 or mc[m1] <= 0:
        return None
    mom = mc[m1] / mc[m12] - 1.0
    if mk.amt20[c].get(t, 0) < min_amt:
        return None
    if b[2] < 2.0:
        return None
    # must have traded in the last 10 sessions (not suspended)
    ds = [d for d in mk.dates[c] if d <= t]
    if len(ds) < 240 or ds[-1] != t:
        return None
    if len([d for d in ds if d > mk.cal[mk.cal.index(t) - 10]]) < 9:
        return None
    ma = mk.ma[c].get(t)
    if trend and (ma is None or b[2] <= ma):
        return None
    return {'code': c, 'mom': mom, 'close': b[2], 'ma': ma, 'amt': mk.amt20[c].get(t, 0)}


def run(cfg, mk, tag):
    n = cfg['n_pos']
    buf = cfg['buffer']
    min_amt = cfg['min_amt']
    trend = cfg['trend_filter']
    regime = cfg['regime']
    exp_on, exp_off = cfg['exp_on'], cfg['exp_off']

    cash = INIT_CASH
    pos = {}          # code -> shares
    cost = {}         # code -> avg cost
    trades, log = [], []
    equity = []
    sdates = signal_dates(mk)
    pending = None    # (exec_date, targets list)

    # build schedule of (signal_date, exec_date)
    sched = [(s, next_trading_day(mk, s)) for s in sdates]

    days = [d for d in mk.cal if START <= d <= END]
    # map exec date -> target weights
    plan = {}
    for s, e in sched:
        cands = []
        for c in mk.panel:
            r = eligible(mk, c, s, min_amt, trend)
            if r:
                cands.append(r)
        cands.sort(key=lambda x: (-x['mom'], x['code']))
        rank = {x['code']: k for k, x in enumerate(cands)}
        hold = [c for c in pos if c in rank]
        want = [x['code'] for x in cands[:n]]
        # keep existing holdings still inside buffer
        keep = [c for c in hold if rank[c] < n + buf]
        picks = keep[:]
        for c in want:
            if len(picks) >= n:
                break
            if c not in picks:
                picks.append(c)
        for c in hold:
            if len(picks) < n and c not in picks and rank.get(c, 999) < n + buf:
                picks.append(c)
        idx_c = mk.idxclose.get(s)
        ma_c = mk.idxma.get(s)
        on = (idx_c is not None and ma_c is not None and idx_c > ma_c) if regime else True
        target = exp_on if on else exp_off
        npos = len(picks) if picks else 0
        w = target / npos if npos else 0.0
        plan[e] = {'picks': picks, 'w': w, 'signal': s, 'regime_on': on,
                   'n_cand': len(cands), 'target': target,
                   'mom': {x['code']: x['mom'] for x in cands[:n + 30]}}
        log.append({'signal_date': s, 'exec_date': e, 'picks': picks,
                    'weights': {c: round(w, 4) for c in picks},
                    'regime_on': on, 'target_exposure': target,
                    'candidates_screened': len(cands),
                    'top_momentum': [(x['code'], mk.names.get(x['code'], ''), round(x['mom'], 4)) for x in cands[:8]]})

    for d in days:
        if d in plan:
            p = plan[d]
            eq = cash + sum(pos[c] * mk.bar[c][d][2] for c in pos if d in mk.bar[c])
            # mark to market at open for sizing
            eq_open = cash + sum(pos[c] * mk.bar[c][d][1] for c in pos if d in mk.bar[c])
            total_w = p['w'] * len(p['picks'])
            # sells first: names not in target
            for c in list(pos):
                if c not in p['picks']:
                    b = mk.bar[c].get(d)
                    if not b:
                        continue
                    px = b[1] * (1 - SLIP)
                    amt = px * pos[c]
                    fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP
                    cash += amt - fee
                    trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'SELL',
                                   'shares': pos[c], 'price': round(px, 3), 'amount': round(amt, 2),
                                   'fee': round(fee, 2), 'reason': 'drop from target'})
                    del pos[c]
                    cost.pop(c, None)
            for c in p['picks']:
                b = mk.bar[c].get(d)
                if not b:
                    continue
                px = b[1] * (1 + SLIP)
                tgt_val = eq_open * p['w']
                cur_val = pos.get(c, 0) * px
                diff = tgt_val - cur_val
                if diff > 0:
                    shares = int(diff / (px * 100)) * 100
                    while shares > 0 and px * shares + max(px * shares * COMM, MIN_COMM) > cash:
                        shares -= 100
                    if shares >= 100:
                        amt = px * shares
                        fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER
                        cash -= amt + fee
                        old = pos.get(c, 0)
                        cost[c] = (cost.get(c, 0) * old + amt) / (old + shares)
                        pos[c] = old + shares
                        trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'BUY',
                                       'shares': shares, 'price': round(px, 3), 'amount': round(amt, 2),
                                       'fee': round(fee, 2), 'reason': 'enter target'})
                elif diff < 0:
                    shares = int(-diff / (px * 100)) * 100
                    shares = min(shares, pos.get(c, 0))
                    if shares >= 100:
                        amt = px * shares
                        fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP
                        cash += amt - fee
                        pos[c] -= shares
                        trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'SELL',
                                       'shares': shares, 'price': round(px, 3), 'amount': round(amt, 2),
                                       'fee': round(fee, 2), 'reason': 'trim to target'})
                        if pos[c] == 0:
                            del pos[c]
            log[-1]['equity_before'] = round(eq_open, 2)
            log[-1]['cash_after'] = round(cash, 2)
            log[-1]['positions_after'] = {c: pos[c] for c in pos}
        mv = sum(pos[c] * mk.bar[c][d][2] for c in pos if d in mk.bar[c])
        equity.append({'date': d, 'cash': round(cash, 2), 'market_value': round(mv, 2),
                       'total': round(cash + mv, 2),
                       'exposure': round(mv / (cash + mv), 4) if cash + mv > 0 else 0})

    return summarize(equity, trades, log, mk, tag, cfg)


def max_dd(series):
    peak, dd, when = series[0][1], 0.0, (None, None)
    pk_d = series[0][0]
    for d, v in series:
        if v > peak:
            peak, pk_d = v, d
        x = (peak - v) / peak
        if x > dd:
            dd, when = x, (pk_d, d)
    return dd, when


def summarize(equity, trades, log, mk, tag, cfg):
    series = [(e['date'], e['total']) for e in equity]
    final = series[-1][1]
    ret = final / INIT_CASH - 1
    dd, ddw = max_dd([(d, v) for d, v in series])
    rets = [(series[i][1] / series[i-1][1] - 1) for i in range(1, len(series))]
    mu = sum(rets) / len(rets) if rets else 0
    sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 1 else 0
    sharpe = (mu / sd * (252 ** 0.5)) if sd else 0
    fees = sum(t['fee'] for t in trades)
    expo = sum(e['exposure'] for e in equity) / len(equity)
    # per-trade realized pnl for sells
    buys = collections.defaultdict(list)
    for t in trades:
        if t['side'] == 'BUY':
            buys[t['code']].append(t)
    return {'tag': tag, 'config': cfg, 'initial': INIT_CASH, 'final': round(final, 2),
            'return': round(ret, 6), 'max_drawdown': round(dd, 6), 'dd_window': ddw,
            'ann_vol': round(sd * (252 ** 0.5), 6), 'sharpe': round(sharpe, 3),
            'trades': len(trades), 'buy_count': sum(1 for t in trades if t['side'] == 'BUY'),
            'sell_count': sum(1 for t in trades if t['side'] == 'SELL'),
            'fees': round(fees, 2), 'mean_exposure': round(expo, 4),
            'equity': equity, 'trade_log': trades, 'rebalance_log': log,
            'final_positions': None}


def bench(mk, code):
    cl = {r[0]: r[2] for r in mk.idx.get(code, [])}
    s = cl.get('2026-03-31'); e = cl.get('2026-09-30')
    if not s or not e:
        return None
    return round(e / s - 1, 6)


if __name__ == '__main__':
    mk = Market()
    print('calendar days in window', len([d for d in mk.cal if START <= d <= END]))
    print('CSI300', bench(mk, 'sh000300'), 'ChiNext', bench(mk, 'sz399006'), 'SSE', bench(mk, 'sh000001'))
