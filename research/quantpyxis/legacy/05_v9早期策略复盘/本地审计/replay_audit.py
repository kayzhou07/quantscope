"""Frozen-window forensic replay. Standard library only; never alters source snapshot."""
from pathlib import Path
import os, sys, json, hashlib, csv, math, statistics, random, collections

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'source_snapshot'
OUT = ROOT / 'results'
OUT.mkdir(exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(SRC))
os.chdir(SRC)
import engine, variants, loader


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def table(name, rows):
    if not rows:
        return
    with (OUT / name).open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


def patched_runner(stage):
    code = (SRC / 'variants.py').read_text(encoding='utf-8')
    if stage in (1, 3, 4):
        old = "elif trail and peak_px[c] and b[2] / peak_px[c] - 1 <= -trail:"
        assert old in code
        code = code.replace(old, "elif trail and peak_px[c] and b[2] / max(cost[c], peak_px[c]) - 1 <= -trail:")
    if stage >= 2:
        code = code.replace('cash = INIT_CASH', 'cash = INIT_CASH\n    pending = {}\n    stopped_today = set()')
        start = code.index('                    nd = next_td(mk, d)\n', code.index('if hit:'))
        end = code.index('        mv = sum(', start)
        old = code[start:end]
        # Preserve original next-rebalance override through R3; R4 gives exits priority.
        skip = "                    if nd in plan and c in plan[nd]['picks']:\n                        continue\n" if stage < 4 else ''
        replacement = (
            '                    nd = next_td(mk, d)\n'
            '                    if not nd or nd > END:\n                        continue\n'
            + skip +
            '                    if c not in pending:\n'
            "                        pending[c] = {'signal_date': d, 'exec_date': nd, 'reason': hit, 'wait': cfg.get('exit_delay', 0)}\n"
        )
        code = code[:start] + replacement + code[end:]
        morning = '''        stopped_today = set()
        for c, order in list(pending.items()):
            if d < order['exec_date']:
                continue
            if order['wait'] > 0:
                order['wait'] -= 1
                continue
            b = mk.bar[c].get(d)
            if not b or b[5] <= 0:
                continue
            if c not in pos:
                del pending[c]
                continue
            px = b[1] * (1 - SLIP); amt = px * pos[c]
            fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP
            cash += amt - fee
            trades.append({'date': d, 'code': c, 'name': mk.names.get(c, ''), 'side': 'SELL',
                           'shares': pos[c], 'price': round(px, 3), 'amount': round(amt, 2),
                           'fee': round(fee, 2), 'reason': order['reason'] + ' signal=' + order['signal_date']})
            stopped_today.add(c)
            del pos[c]; cost.pop(c, None); peak_px.pop(c, None); del pending[c]
'''
        code = code.replace('    for d in days:\n', '    for d in days:\n' + morning)
    if stage == 4:
        # Plans are made only at the signal close, using the portfolio then actually held.
        a = code.index('    for s, e in sched:\n')
        b = code.index('    days = [d for d in mk.cal', a)
        block = code[a:b]
        body = block[len('    for s, e in sched:\n'):]
        new = '    def make_plan(s, e):\n' + body
        new += "    signal_exec = dict(sched)\n    for s, e in sched:\n        if s < START:\n            make_plan(s, e)\n\n"
        code = code[:a] + new + code[b:]
        # Closing NAV is not permitted to use a future execution price.
        code = code.replace("        mv = sum(pos[c] * mk.bar[c][d][2] for c in pos if d in mk.bar[c])", "        mv = sum(pos[c] * mark(c, d, 2) for c in pos)")
        code = code.replace("pos[c] * mk.bar[c][s][2] for c in pos if s in mk.bar[c]", "pos[c] * mark(c, s, 2) for c in pos")
        code = code.replace("pos[c] * mk.bar[c][d][1] for c in pos if d in mk.bar[c]", "pos[c] * mark(c, d, 1) for c in pos")
        helper = '''    def mark(c, d, field):
        if d in mk.bar[c]:
            return mk.bar[c][d][field]
        previous = [x for x in mk.dates[c] if x < d]
        if not previous:
            raise ValueError('No historical mark for held asset ' + c)
        return mk.bar[c][previous[-1]][2]
'''
        code = code.replace('    def make_plan(s, e):', helper + '    def make_plan(s, e):')
        code = code.replace('            for c in list(pos):\n                if c not in p[\'picks\']:', "            for c in list(pos):\n                if c in pending:\n                    continue\n                if c not in p['picks']:")
        code = code.replace("            for c in p['picks']:\n", "            for c in p['picks']:\n                if c in stopped_today or c in pending:\n                    continue\n")
        code = code.replace('                        amt = px * sh\n                        fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP', '                        px = b[1] * (1 - SLIP)\n                        amt = px * sh\n                        fee = max(amt * COMM, MIN_COMM) + amt * TRANSFER + amt * STAMP')
        code = code.replace("peak_px[c] = max(peak_px.get(c, 0), b[2])\n                        trades.append", "peak_px[c] = peak_px.get(c, 0)\n                        trades.append")
        code = code.replace("            log[-1]['cash_after'] = round(cash, 2)", "            next(x for x in log if x['exec_date'] == d)['cash_after'] = round(cash, 2)")
        code = code.replace("    series = [(e['date'], e['total']) for e in equity]", "        if d in signal_exec and signal_exec[d] and signal_exec[d] <= END:\n            make_plan(d, signal_exec[d])\n        if cash < -1e-6:\n            raise AssertionError('negative cash')\n\n    series = [(e['date'], e['total']) for e in equity]")
    ns = {'__name__': 'audit_patched'}
    exec(compile(code, 'variants_R%d.py' % stage, 'exec'), ns)
    (OUT / ('variants_R%d.py' % stage)).write_text(code, encoding='utf-8')
    return ns, ns['run_variant']


def metrics(r):
    values = [engine.INIT_CASH] + [e['total'] for e in r['equity']]
    rets = [b / a - 1 for a, b in zip(values, values[1:])]
    peak = values[0]; dd = 0
    for v in values:
        peak = max(peak, v); dd = max(dd, 1-v/peak)
    sd = statistics.stdev(rets)
    r['audited_max_drawdown'] = dd
    r['audited_sharpe'] = statistics.mean(rets)/sd*math.sqrt(252) if sd else None
    r['operation_days'] = len(r['trade_dates'])
    r['buy_count'] = sum(t['side']=='BUY' for t in r['trade_log'])
    r['sell_count'] = r['trades'] - r['buy_count']
    return r


def fifo_audit(r, mk):
    lots = collections.defaultdict(collections.deque)
    closed = []; ledger = collections.defaultdict(lambda: dict(buy=0.,sell=0.,fees=0.,shares=0))
    for t in r['trade_log']:
        c=t['code']; q=t['shares']; price=t['amount']/q; fee=t['fee']/q
        book=ledger[c]; book['fees']+=t['fee']
        if t['side']=='BUY':
            lots[c].append([q,price,fee,t['date']]); book['buy']+=t['amount']; book['shares']+=q
        else:
            book['sell']+=t['amount']; book['shares']-=q
            left=q
            while left:
                lot=lots[c][0]; n=min(left,lot[0]); net=n*(price-fee-lot[1]-lot[2])
                closed.append(dict(code=c,name=t['name'],buy_date=lot[3],sell_date=t['date'],shares=n,net_pnl=round(net,2),net_return=(price-fee)/(lot[1]+lot[2])-1,reason=t['reason']))
                lot[0]-=n; left-=n
                if lot[0]==0: lots[c].popleft()
    attrs=[]
    for c,b in ledger.items():
        ds=[d for d in mk.dates[c] if d<=engine.END]
        mark=mk.bar[c][ds[-1]][2]
        mv=b['shares']*mark
        attrs.append(dict(code=c,name=mk.names.get(c,''),net_pnl=round(b['sell']+mv-b['buy']-b['fees'],2),fees=round(b['fees'],2),end_shares=b['shares']))
    attrs.sort(key=lambda x:-x['net_pnl'])
    assert abs(sum(x['net_pnl'] for x in attrs) - (r['final']-100000))<1, 'PnL attribution failed'
    return closed,attrs


def matched_bench(r,mk,code):
    """Daily OPEN-to-OPEN risk exposure match, split overnight and intraday returns.
    Holdings/overnight quantity known at previous close; post-trade exposure at open.
    Index is price return; benchmark has zero fees and zero cash interest.
    """
    bars={b[0]:b for b in mk.idx[code]}; dates=sorted(bars)
    trades=collections.defaultdict(list)
    for t in r['trade_log']: trades[t['date']].append(t)
    pos=collections.defaultdict(int); cash=100000.; bench=100000.; rows=[]
    for e in r['equity']:
        d=e['date']; prev=dates[dates.index(d)-1]
        prev_mv=sum(q*mk.bar[c][max(x for x in mk.dates[c] if x<=prev)][2] for c,q in pos.items() if q)
        prev_exp=prev_mv/(cash+prev_mv)
        bench*=1+prev_exp*(bars[d][1]/bars[prev][2]-1)
        for t in trades[d]:
            sign=1 if t['side']=='BUY' else -1
            pos[t['code']]+=sign*t['shares']; cash-=sign*t['amount']+t['fee']
        open_mv=sum(q*(mk.bar[c][d][1] if d in mk.bar[c] else mk.bar[c][max(x for x in mk.dates[c] if x<d)][2]) for c,q in pos.items() if q)
        exp=open_mv/(cash+open_mv)
        bench*=1+exp*(bars[d][2]/bars[d][1]-1)
        rows.append(dict(date=d,index=code,overnight_exposure=prev_exp,intraday_exposure=exp,matched_nav=bench))
    return dict(index=code,full_index_return=bars[engine.END][2]/bars['2026-03-31'][2]-1,exposure_matched_return=bench/100000-1,method='overnight/intraday split, actual lagged holdings, gross price index; no benchmark fees'),rows


def main():
    manifest=[]
    for p in sorted(SRC.rglob('*')):
        if p.is_file():
            manifest.append(dict(path=p.relative_to(SRC).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    save('manifest.json',dict(source=str(SRC),snapshot_files=manifest,window=[engine.START,engine.END],note='Existing WorkBuddy snapshot; no new download'))
    mk=engine.Market()
    print('loaded',len(mk.panel),'stocks',len([d for d in mk.cal if engine.START<=d<=engine.END]),'sessions',flush=True)
    results={}; summaries=[]
    runners={0:({},variants.run_variant)}
    for stage in (1,2,3,4): runners[stage]=patched_runner(stage)
    for freq in ('M','Q'):
        cfg=dict(variants.BASE,tag='',trail=.12,rebal_months=(variants.BASE['rebal_months'] if freq=='M' else ['2026-03','2026-06']))
        for stage in range(5):
            key=f'R{stage}-{freq}'; cfg1=dict(cfg,tag=key)
            r=metrics(runners[stage][1](mk,cfg1)); results[key]=r
            save(key+'.json',r)
            print(key,r['final'],r['audited_max_drawdown'],r['trades'],flush=True)
        ref=147740.32 if freq=='M' else 132926.81
        assert results['R0-'+freq]['final']==ref, 'Original reproduction failed'
        assert results['R0-'+freq]['trades']==(41 if freq=='M' else 16)
        for suffix,extra in [('delay1',dict(exit_delay=1)),('delay2',dict(exit_delay=2)),('slip30bp',{})]:
            ns,runner=patched_runner(4)
            if suffix=='slip30bp': ns['SLIP']=.003
            key='R4-'+freq+'-'+suffix
            r=metrics(runner(mk,dict(cfg,tag=key,**extra))); results[key]=r; save(key+'.json',r)
    for key,r in results.items():
        summaries.append({k:v for k,v in r.items() if k not in ['config','equity','trade_log','rebalance_log','trade_dates']})
        table(key+'_equity.csv',r['equity']); table(key+'_trades.csv',r['trade_log'])
        if key.startswith('R4-'):
            closed,attr=fifo_audit(r,mk); table(key+'_fifo.csv',closed); table(key+'_attribution.csv',attr)
            r['attribution']=attr; r['fifo']=closed
            bb=[]
            for idx in ('sh000300','sz399006'):
                if idx in mk.idx:
                    b,rows=matched_bench(r,mk,idx);bb.append(b);table(key+'_'+idx+'_matched.csv',rows)
            save(key+'_analysis.json',dict(attribution=attr,fifo=closed,benchmarks=bb))
    save('summary.json',summaries);table('summary.csv',summaries)
    # Audit all raw duplicate observations before the original loader silently deduplicates.
    seen={}; conflicts=[]; duplicates=0; malformed=0
    for p in sorted((SRC/'raw'/'bars').glob('b*.txt'))+sorted((SRC/'raw'/'bars2').glob('r*.txt')):
        for row in loader.parse_rows(p,True):
            c,d,*vals=row;key=(c,d)
            if key in seen:
                duplicates+=1
                if seen[key]!=tuple(vals): conflicts.append(dict(code=c,date=d,first=seen[key],retry=vals,file=p.name))
            else: seen[key]=tuple(vals)
            o,cl,h,l,v,a=vals
            if not (0<l<=min(o,cl)<=max(o,cl)<=h and v>=0 and a>=0): malformed+=1
    held_missing=[]
    for key in ('R4-M','R4-Q'):
        quantities=collections.defaultdict(int);byday=collections.defaultdict(list)
        for t in results[key]['trade_log']:byday[t['date']].append(t)
        for e in results[key]['equity']:
            for t in byday[e['date']]: quantities[t['code']]+=t['shares']*(1 if t['side']=='BUY' else -1)
            for c,q in quantities.items():
                if q and e['date'] not in mk.bar[c]:held_missing.append([key,e['date'],c])
    save('data_audit.json',dict(stock_count=len(mk.panel),stock_bars=sum(map(len,mk.panel.values())),indexes={k:len(v) for k,v in mk.idx.items()},duplicates=duplicates,conflicting_duplicates=conflicts,invalid_ohlc_rows=malformed,missing_held_bars=held_missing,stock_data_min=min(v[0][0] for v in mk.panel.values()),stock_data_max=max(v[-1][0] for v in mk.panel.values())))
    print('DONE',str(OUT),flush=True)


if __name__=='__main__': main()
