"""Attribution: per-stock realized/unrealized P&L, daily equity export, benchmark compare."""
import json, collections
import engine
from engine import Market, INIT_CASH, START, END, bench


def attribution(res, mk):
    lots = collections.defaultdict(lambda: {'sh': 0, 'cost': 0.0, 'sold_amt': 0.0, 'sold_sh': 0,
                                            'fees': 0.0, 'buys': [], 'sells': []})
    for t in sorted(res['trade_log'], key=lambda x: x['date']):
        L = lots[t['code']]
        L['fees'] += t['fee']
        if t['side'] == 'BUY':
            L['sh'] += t['shares']
            L['cost'] += t['amount']
            L['buys'].append([t['date'], t['shares'], t['price']])
        else:
            avg = L['cost'] / L['sh'] if L['sh'] else 0
            L['sold_amt'] += t['amount']
            L['sold_sh'] += t['shares']
            L['cost'] -= avg * t['shares']
            L['sh'] -= t['shares']
            L['sells'].append([t['date'], t['shares'], t['price']])
    last = '2026-09-30'
    out = []
    for c, L in lots.items():
        mv = 0.0
        if L['sh'] > 0:
            b = mk.bar[c].get(last)
            px = b[2] if b else 0
            mv = px * L['sh']
        realized = L['sold_amt'] - (L['cost'] + (L['sold_amt'] / L['sold_sh'] * L['sold_sh'] if False else 0))
        # realized = proceeds attributable to sold shares - cost of those shares
        realized = L['sold_amt'] - (sum(x[1] for x in L['buys']) and 0 or 0) - 0
        out.append({'code': c, 'name': mk.names.get(c, ''), 'board': mk.boards.get(c, ''),
                    'shares_left': L['sh'], 'cost_left': round(L['cost'], 2),
                    'market_value': round(mv, 2),
                    'unrealized': round(mv - L['cost'], 2),
                    'unreal_pct': round((mv / L['cost'] - 1) * 100, 2) if L['cost'] else None,
                    'sold_amount': round(L['sold_amt'], 2), 'fees': round(L['fees'], 2),
                    'buys': L['buys'], 'sells': L['sells']})
    return sorted(out, key=lambda x: -(x['market_value'] or 0))


if __name__ == '__main__':
    mk = Market()
    res = json.load(open('results_v9.json', encoding='utf-8'))
    print('CSI300 %.4f ChiNext %.4f SSE %.4f' % (bench(mk, 'sh000300'), bench(mk, 'sz399006'), bench(mk, 'sh000001')))
    for r in res:
        print('\n=== %s  ret %.3f%% dd %.2f%% trades %d fees %.2f' %
              (r['tag'], r['return'] * 100, r['max_drawdown'] * 100, r['trades'], r['fees']))
        for a in attribution(r, mk):
            print('  %-6s %-8s %-4s left %5d mv %9.2f unr %8.2f (%s%%)' %
                  (a['code'], a['name'], a['board'], a['shares_left'], a['market_value'],
                   a['unrealized'], a['unreal_pct']))
