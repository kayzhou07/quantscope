"""Finalise the current version (v9-Q) and export CSV / JSON artefacts."""
import json, csv, collections
from engine import Market, INIT_CASH, START, END, bench
import variants as V

CFG = dict(V.BASE, trail=0.12, tag='v9-M')

mk = Market()
r = V.run_variant(mk, CFG)

# --- benchmark: equal weight, buy every eligible name at 2026-04-01 open, hold to 2026-09-30
tr = None
acc = []
for c in mk.panel:
    b0 = mk.bar[c].get('2026-04-01')
    b1 = mk.bar[c].get('2026-09-30')
    if b0 and b1:
        acc.append(b1[2] / b0[1] - 1)
ew = sum(acc) / len(acc)

# --- attribution
lots = collections.defaultdict(lambda: {'sh': 0, 'cost': 0.0, 'sold': 0.0, 'sh_sold': 0, 'fee': 0.0})
for t in sorted(r['trade_log'], key=lambda x: x['date']):
    L = lots[t['code']]; L['fee'] += t['fee']
    if t['side'] == 'BUY':
        L['sh'] += t['shares']; L['cost'] += t['amount']
    else:
        avg = L['cost'] / L['sh'] if L['sh'] else 0
        L['cost'] -= avg * t['shares']; L['sold'] += t['amount']
        L['sh'] -= t['shares']; L['sh_sold'] += t['shares']

attr = []
for c, L in lots.items():
    px_end = mk.bar[c]['2026-09-30'][2] if '2026-09-30' in mk.bar[c] else None
    mv = px_end * L['sh'] if (px_end and L['sh']) else 0.0
    attr.append({'code': c, 'name': mk.names.get(c, ''), 'board': mk.boards.get(c, ''),
                 'sell_proceeds': round(L['sold'], 2), 'shares_left': L['sh'],
                 'cost_left': round(L['cost'], 2), 'market_value': round(mv, 2),
                 'unrealized': round(mv - L['cost'], 2), 'fees': round(L['fee'], 2),
                 'bought': round(sum(t['amount'] for t in r['trade_log']
                                     if t['code'] == c and t['side'] == 'BUY'), 2),
                 'total_pnl': round(L['sold'] + mv - sum(
                     t['amount'] for t in r['trade_log'] if t['code'] == c and t['side'] == 'BUY'), 2)})

summary = {
    'version': 'v9-M', 'window': [START, END], 'initial': INIT_CASH,
    'final': r['final'], 'return': r['return'], 'max_drawdown': r['max_drawdown'],
    'dd_window': r['dd_window'], 'ann_vol': r['ann_vol'], 'sharpe': r['sharpe'],
    'calmar': r['calmar'], 'trades': r['trades'], 'fees': r['fees'],
    'mean_exposure': r['mean_exposure'], 'trade_dates': r['trade_dates'],
    'bench': {'CSI300': bench(mk, 'sh000300'), 'ChiNext': bench(mk, 'sz399006'),
              'SSE': bench(mk, 'sh000001'), 'equal_weight_universe': round(ew, 6),
              'equal_weight_n': len(acc)},
    'config': {k: v for k, v in CFG.items() if k != 'tag'},
    'attribution': attr,
}

json.dump(summary, open('results_current.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(r, open('full_current.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

with open('equity_current.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f); w.writerow(['date', 'total', 'cash', 'market_value', 'exposure', 'n_positions'])
    for e in r['equity']:
        w.writerow([e['date'], e['total'], e['cash'], e['market_value'], e['exposure'], e['n_positions']])

with open('trades_current.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f); w.writerow(['date', 'code', 'name', 'side', 'shares', 'price', 'amount', 'fee', 'reason'])
    for t in r['trade_log']:
        w.writerow([t['date'], t['code'], t['name'], t['side'], t['shares'], t['price'], t['amount'], t['fee'], t['reason']])

print('final %.2f ret %.4f%% dd %.4f%% vol %.4f%% sharpe %.2f trades %d fees %.2f expo %.2f%%' %
      (r['final'], r['return']*100, r['max_drawdown']*100, r['ann_vol']*100, r['sharpe'], r['trades'], r['fees'], r['mean_exposure']*100))
print('bench', summary['bench'])
print('--- rebalances')
for l in r['rebalance_log']:
    print(l['exec_date'], l['names'], 'on' if l['regime_on'] else 'off', l['n_positions'], 'cand', l['candidates'])
print('--- trades')
for t in r['trade_log']:
    print(t['date'], t['side'], t['name'], t['shares'], t['price'], t['amount'], t['fee'], t['reason'])
print('--- attribution')
for a in sorted(attr, key=lambda x: -x['total_pnl']):
    print(a)
print('sum pnl %.2f + fees %.2f = %.2f (target %.2f)' % (
    sum(a['total_pnl'] for a in attr), r['fees'],
    sum(a['total_pnl'] for a in attr) + r['fees'], r['final'] - INIT_CASH))
print('--- monthly close')
last = {}
for e in r['equity']:
    last[e['date'][:7]] = e['total']
print(last)
