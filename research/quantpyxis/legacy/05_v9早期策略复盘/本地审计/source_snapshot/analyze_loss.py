# -*- coding: utf-8 -*-
"""One single run of the frozen v9 config + FIFO round-trip loss audit."""
import json, collections
from engine import Market, INIT_CASH, START, END, bench
import variants as V

CFG = dict(V.BASE, trail=0.12, tag='v9-M')
mk = Market()
r = V.run_variant(mk, CFG)

# ---- FIFO round trips
open_lots = collections.defaultdict(list)   # code -> [[shares, price, date, fee_per_share]]
trips = []
for t in sorted(r['trade_log'], key=lambda x: (x['date'], 0 if x['side'] == 'SELL' else 1)):
    c = t['code']
    fps = t['fee'] / t['shares'] if t['shares'] else 0
    if t['side'] == 'BUY':
        open_lots[c].append([t['shares'], t['price'], t['date'], fps])
    else:
        left = t['shares']
        while left > 0 and open_lots[c]:
            lot = open_lots[c][0]
            take = min(left, lot[0])
            gross = (t['price'] - lot[1]) * take
            fees = fps * take + lot[3] * take
            trips.append({
                'code': c, 'name': mk.names.get(c, ''), 'board': mk.boards.get(c, ''),
                'buy_date': lot[2], 'buy_price': lot[1], 'sell_date': t['date'], 'sell_price': t['price'],
                'shares': take, 'gross': round(gross, 2), 'fees': round(fees, 2),
                'net': round(gross - fees, 2), 'ret': round(t['price'] / lot[1] - 1, 6),
                'reason': t['reason'],
                'days': len([d for d in mk.cal if lot[2] < d <= t['date']])})
            lot[0] -= take; left -= take
            if lot[0] == 0:
                open_lots[c].pop(0)

trips.sort(key=lambda x: x['net'])
print('round trips', len(trips))
for t in trips[:8]:
    print('%-8s %-4s %s %.2f -> %s %.2f  %4d股  net %9.2f  %7.2f%%  %2d日  %s' %
          (t['name'], t['board'], t['buy_date'], t['buy_price'], t['sell_date'], t['sell_price'],
           t['shares'], t['net'], t['ret'] * 100, t['days'], t['reason']))

worst = trips[0]
print('\nWORST', worst)

# ---- context of the worst trade
code = worst['code']
bars = mk.bar[code]
path = [b for b in mk.panel[code] if worst['buy_date'] <= b[0] <= worst['sell_date']]
hi = max(b[3] for b in path)
lo = min(b[4] for b in path)
peak_after_buy = max(b[2] for b in path)
print('entry %.2f  max intraday high %.2f  max close-after-buy %.2f  min low %.2f  exit %.2f' %
      (worst['buy_price'], hi, peak_after_buy, lo, worst['sell_price']))
print('MAE from entry %.2f%%   MFE from entry %.2f%%' %
      ((lo / worst['buy_price'] - 1) * 100, (hi / worst['buy_price'] - 1) * 100))
print('exit vs peak close %.2f%%' % ((worst['sell_price'] / peak_after_buy - 1) * 100))

# momentum rank at the signal before entry
for lg in r['rebalance_log']:
    if lg['exec_date'] == worst['buy_date']:
        print('rebalance', lg['exec_date'], 'candidates', lg['candidates'])
        for i, (c2, n2, mom) in enumerate(lg['top8']):
            print('   #%d %-6s %-8s mom %+.2f%%' % (i + 1, c2, n2, mom * 100))
        break

# the stock's own history: 12-1 momentum at signal, and price a year before
sig = [lg['signal_date'] for lg in r['rebalance_log'] if lg['exec_date'] == worst['buy_date']][0]
mc = mk.mclose[code]
m1 = mk.prev_month(sig[:7], 1); m12 = mk.prev_month(sig[:7], 12)
print('signal', sig, 'P(m-1)=%.2f (%s) P(m-12)=%.2f (%s) mom=%+.1f%%' %
      (mc[m1], m1, mc[m12], m12, (mc[m1] / mc[m12] - 1) * 100))
print('signal close %.2f  50d ago %.2f' % (
    mk.bar[code][sig][2], [b for b in mk.panel[code] if b[0] <= sig][-50][2]))

# same-day peer outcomes
print('\ncohort on', worst['buy_date'])
for lg in r['rebalance_log']:
    if lg['exec_date'] == worst['buy_date']:
        for c2 in lg['picks']:
            tt = [x for x in trips if x['code'] == c2 and x['buy_date'] == worst['buy_date']]
            print('   %-8s net %9.2f' % (mk.names.get(c2, ''), sum(x['net'] for x in tt) if tt else 0.0))

json.dump({'trips': trips, 'worst': worst}, open('loss_audit.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('\nfinal %.2f  ret %.4f%%  dd %.4f%%  trades %d' %
      (r['final'], r['return'] * 100, r['max_drawdown'] * 100, r['trades']))
