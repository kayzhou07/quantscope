"""v9 variants: main-board + ChiNext momentum rotation, 100k CNY, 2026-04-01..2026-09-30.

Sizing logic: the risk budget is expressed as (number of positions) x (weight per position),
so that 100-share lot granularity does not silently inflate the intended exposure.
- risk-on : 5 positions x 19% = 95%
- risk-off: 3 positions x 19% = 57%
- a name whose one lot exceeds 25% of equity cannot be sized and is skipped.
"""
import json
import engine
from engine import Market, INIT_CASH, START, END, COMM, MIN_COMM, TRANSFER, STAMP, SLIP, max_dd, bench, eligible


CAND_CACHE = {}


def next_td(mk, d):
    for x in mk.cal:
        if x > d:
            return x
    return None


def run_variant(mk, cfg):
    w_each = cfg['w_each']
    buf = cfg['buffer']
    min_amt = cfg['min_amt']
    trend = cfg['trend_filter']
    regime = cfg['regime']
    stop = cfg.get('stop')
    trail = cfg.get('trail')
    max_single = cfg.get('max_single', 0.25)
    months = cfg['rebal_months']

    me = mk.month_ends()
    sched = [(me[m], next_td(mk, me[m])) for m in months]

    cash = INIT_CASH
    pos, cost, peak_px = {}, {}, {}
    trades, log, equity = [], [], []
    plan = {}
    for s, e in sched:
        key = (s, min_amt, trend)
        if key not in CAND_CACHE:
            cands = []
            for c in mk.panel:
                r = eligible(mk, c, s, min_amt, trend)
                if r:
                    cands.append(r)
            cands.sort(key=lambda x: (-x['mom'], x['code']))
            CAND_CACHE[key] = cands
        cands = list(CAND_CACHE[key])
        eq_ref = cash + sum(pos[c] * mk.bar[c][s][2] for c in pos if s in mk.bar[c])
        lot_cap = eq_ref * max_single
        n_before = len(cands)
        cands = [x for x in cands if x['close'] * 100 <= lot_cap]
        rank = {x['code']: k for k, x in enumerate(cands)}

        on = True
        if regime:
            ic, im = mk.idxclose.get(s), mk.idxma.get(s)
            on = bool(ic is not None and im is not None and ic > im)
        n_target = cfg['n_on'] if on else cfg['n_off']

        keep = [c for c in pos if c in rank and rank[c] < n_target + buf]
        picks = keep[:n_target]
        for x in cands[:n_target]:
            if len(picks) >= n_target:
                break
            if x['code'] not in picks:
                picks.append(x['code'])
        plan[e] = {'picks': picks, 'signal': s, 'regime_on': on, 'lot_cap': lot_cap}
        log.append({'signal_date': s, 'exec_date': e, 'picks': picks,
                    'names': [mk.names.get(c, '') for c in picks],
                    'target_weight_each': round(w_each, 4),
                    'regime_on': on,
                    'target_exposure': round(w_each * len(picks), 3),
                    'n_positions': len(picks),
                    'candidates': len(cands),
                    'candidates_before_lot_filter': n_before,
                    'one_lot_cap': round(lot_cap, 2),
                    'top8': [(c, mk.names.get(c, ''), round(y['mom'], 4))
                             for c, y in [(y['code'], y) for y in cands[:8]]]})

    days = [d for d in mk.cal if START <= d <= END]
    for d in days:
        if d in plan:
            p = plan[d]
            eq_open = cash + sum(pos[c] * mk.bar[c][d][1] for c in pos if d in mk.bar[c])
            for c in list(pos):
                if c not in p['picks']:
                    b = mk.bar[c].get(d)
                    if not b:
                        continue
                    px = b[1] * (1 - SLIP); amt = px * pos[c]
                    fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP
                    cash += amt - fee
                    trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'SELL',
                                   'shares': pos[c], 'price': round(px, 3), 'amount': round(amt, 2),
                                   'fee': round(fee, 2), 'reason': 'leave target'})
                    del pos[c]; cost.pop(c, None); peak_px.pop(c, None)
            for c in p['picks']:
                b = mk.bar[c].get(d)
                if not b:
                    continue
                px = b[1] * (1 + SLIP)
                tgt = eq_open * w_each
                cur = pos.get(c, 0) * px
                diff = tgt - cur
                if diff > 0:
                    sh = int(diff / (px * 100)) * 100
                    if sh == 0 and cur == 0 and px * 100 <= p['lot_cap']:
                        sh = 100
                    while sh > 0 and px * sh + max(px * sh * COMM, MIN_COMM) + px * sh * TRANSFER > cash:
                        sh -= 100
                    if sh >= 100:
                        amt = px * sh
                        fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER
                        cash -= amt + fee
                        old = pos.get(c, 0)
                        cost[c] = (cost.get(c, 0) * old + amt) / (old + sh)
                        pos[c] = old + sh
                        peak_px[c] = max(peak_px.get(c, 0), b[2])
                        trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'BUY',
                                       'shares': sh, 'price': round(px, 3), 'amount': round(amt, 2),
                                       'fee': round(fee, 2), 'reason': 'enter target'})
                elif diff < 0:
                    sh = min(int(-diff / (px * 100)) * 100, pos.get(c, 0))
                    if sh >= 100:
                        amt = px * sh
                        fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP
                        cash += amt - fee; pos[c] -= sh
                        trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'SELL',
                                       'shares': sh, 'price': round(px, 3), 'amount': round(amt, 2),
                                       'fee': round(fee, 2), 'reason': 'trim to target'})
                        if pos[c] == 0:
                            del pos[c]; cost.pop(c, None); peak_px.pop(c, None)
            log[-1]['cash_after'] = round(cash, 2)
        if stop or trail:
            for c in list(pos):
                b = mk.bar[c].get(d)
                if not b:
                    continue
                peak_px[c] = max(peak_px.get(c, 0), b[2])
                hit = None
                if stop and cost.get(c) and b[2] / cost[c] - 1 <= -stop:
                    hit = 'stop -%.0f%% from cost' % (stop * 100)
                elif trail and peak_px[c] and b[2] / peak_px[c] - 1 <= -trail:
                    hit = 'trail -%.0f%% from peak' % (trail * 100)
                if hit:
                    nd = next_td(mk, d)
                    if not nd:
                        continue
                    if nd in plan and c in plan[nd]['picks']:
                        continue
                    nb = mk.bar[c].get(nd)
                    if not nb:
                        continue
                    px = nb[1] * (1 - SLIP); amt = px * pos[c]
                    fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP
                    cash += amt - fee
                    trades.append({'date': nd, 'code': c, 'name': mk.names.get(c, ''), 'side': 'SELL',
                                   'shares': pos[c], 'price': round(px, 3), 'amount': round(amt, 2),
                                   'fee': round(fee, 2), 'reason': hit})
                    del pos[c]; cost.pop(c, None); peak_px.pop(c, None)
        mv = sum(pos[c] * mk.bar[c][d][2] for c in pos if d in mk.bar[c])
        equity.append({'date': d, 'cash': round(cash, 2), 'market_value': round(mv, 2),
                       'total': round(cash + mv, 2),
                       'exposure': round(mv / (cash + mv), 4) if cash + mv > 0 else 0,
                       'n_positions': len(pos)})

    series = [(e['date'], e['total']) for e in equity]
    final = series[-1][1]; ret = final / INIT_CASH - 1
    dd, ddw = max_dd(series)
    rets = [series[i][1] / series[i-1][1] - 1 for i in range(1, len(series))]
    mu = sum(rets) / len(rets); sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    return {'tag': cfg['tag'], 'config': cfg, 'final': round(final, 2), 'return': round(ret, 6),
            'max_drawdown': round(dd, 6), 'dd_window': ddw,
            'ann_vol': round(sd * 252 ** 0.5, 6),
            'sharpe': round(mu / sd * 252 ** 0.5, 3) if sd else 0,
            'calmar': round(ret / dd, 3) if dd else None,
            'trades': len(trades), 'fees': round(sum(t['fee'] for t in trades), 2),
            'mean_exposure': round(sum(e['exposure'] for e in equity) / len(equity), 4),
            'equity': equity, 'trade_log': trades, 'rebalance_log': log,
            'trade_dates': sorted({t['date'] for t in trades})}


BASE = dict(w_each=0.19, n_on=5, n_off=3, buffer=1, min_amt=1e8, trend_filter=False,
            regime=True, max_single=0.25, stop=None, trail=None,
            rebal_months=['2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08'])

VARIANTS = [
    dict(BASE, tag='A 主策略：月度动量+大盘状态'),
    dict(BASE, tag='B 取消大盘状态 常满仓', regime=False),
    dict(BASE, tag='C 加个股趋势过滤', trend_filter=True),
    dict(BASE, tag='D 主策略+移动止损12%', trail=0.12),
    dict(BASE, tag='E 季度调仓+趋势+移动止损', rebal_months=['2026-03', '2026-06'], trend_filter=True, trail=0.12),
]

if __name__ == '__main__':
    mk = Market()
    print('trading days', len([d for d in mk.cal if START <= d <= END]))
    print('CSI300 %.4f  ChiNext %.4f  SSE %.4f' % (bench(mk, 'sh000300'), bench(mk, 'sz399006'), bench(mk, 'sh000001')))
    res = []
    for cfg in VARIANTS:
        r = run_variant(mk, dict(cfg))
        res.append(r)
        print('%-30s ret %7.3f%%  dd %6.2f%%  vol %6.2f%%  sharpe %5.2f  expo %5.1f%%  trades %2d  fees %7.2f  final %10.2f'
              % (r['tag'], r['return'] * 100, r['max_drawdown'] * 100, r['ann_vol'] * 100,
                 r['sharpe'], r['mean_exposure'] * 100, r['trades'], r['fees'], r['final']))
    json.dump(res, open('results_v9.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
