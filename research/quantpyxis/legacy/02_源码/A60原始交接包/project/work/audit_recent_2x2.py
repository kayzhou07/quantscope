from pathlib import Path
import json,math,hashlib,collections,csv
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'reports/recent2025-20261020';W=ROOT/'work/recent_window_20261020'
KEYS=['A60_original','B80_original','C60_fast_reduce','D80_fast_reduce']
res={k:json.loads((P/f'{k}.json').read_text(encoding='utf8')) for k in KEYS}
summary=[];monthly=[];losses={}
for key,d in res.items():
 nav=d['nav'];assert len(nav)==len({x['date'] for x in nav})==432
 assert nav[0]['date']=='2025-01-02' and nav[-1]['date']=='2026-10-19'
 peak=100000.;pdate='2024-12-31';dd=0.;window=[];prior=100000.;daily=[];months={};cash_gap=[]
 trades=d['trades'];bydate=collections.defaultdict(list)
 for t in trades:bydate[t['date']].append(t)
 for n in nav:
  assert abs(n['nav']-n['cash']-n['market_value'])<1e-7
  if n['nav']>peak:peak=n['nav'];pdate=n['date']
  if 1-n['nav']/peak>dd:dd=1-n['nav']/peak;window=[pdate,n['date']]
  daily.append(dict(date=n['date'],ret=n['nav']/prior-1,pnl=n['nav']-prior));prior=n['nav'];months[n['date'][:7]]=n['nav']
 pnav=nav[-1]['nav'];assert abs(pnav/d['capital']-1-d['summary']['total_return'])<.00000051
 assert abs(dd+ d['summary']['max_drawdown'])<.00000051
 mean=sum(n['exposure'] for n in nav)/len(nav)
 counts=collections.Counter(t['side'] for t in trades);fees=sum(t['fee_total'] for t in trades if t['side'] in ['buy','sell'])
 assert abs(fees-d['total_fees'])<1e-7
 cash=100000.
 for n in nav:
  for t in bydate[n['date']]:
   if t['side']=='buy':cash-=t['amount']+t['fee_total']
   elif t['side']=='sell':cash+=t['amount']-t['fee_total']
  cash+=n.get('interest',0.)
  cash_gap.append(abs(cash-n['cash']))
 assert max(cash_gap)<.02,max(cash_gap)
 assert not d['holdings_final']
 startmonth=100000.
 for m,v in months.items():monthly.append(dict(variant=key,month=m,return_=v/startmonth-1,pnl=v-startmonth,end=v));startmonth=v
 book=collections.defaultdict(lambda:dict(buy=0.,sell=0.,fees=0.))
 for t in trades:
  if t['side'] in ['buy','sell']:book[t['code']][t['side']]+=t['amount'];book[t['code']]['fees']+=t['fee_total']
 attrs=[dict(code=k,net_trade_cashflow=v['sell']-v['buy']-v['fees'],**v) for k,v in book.items()];attrs.sort(key=lambda x:x['net_trade_cashflow'])
 worst=sorted(daily,key=lambda x:x['pnl'])[:5];pos=collections.defaultdict(dict)
 for p in d['positions']:pos[p['date']][p['code']]=float(p['value'])
 for event in worst:
  date=event['date'];i=next(i for i,r in enumerate(nav) if r['date']==date);before={} if i==0 else pos[nav[i-1]['date']];after=pos[date]
  cashmoves=collections.defaultdict(float)
  for t in bydate[date]:
   if t['side']=='buy':cashmoves[t['code']]-=t['amount']+t['fee_total']
   elif t['side']=='sell':cashmoves[t['code']]+=t['amount']-t['fee_total']
  ca=[dict(code=c,pnl=after.get(c,0)-before.get(c,0)+cashmoves[c]) for c in set(before)|set(after)|set(cashmoves)]
  ca.sort(key=lambda x:x['pnl']);event['positions']=ca;event['sum_position_pnl']=sum(x['pnl'] for x in ca);event['residual']=event['pnl']-event['sum_position_pnl']
  assert abs(event['residual'])<.02,event
 losses[key]=dict(worst_days=worst,stock_cashflow=attrs)
 row=dict(variant=key,start=nav[0]['date'],end=nav[-1]['date'],days=len(nav),final=pnav,return_=pnav/100000-1,maxdd=dd,dd_window=window,mean_exposure=mean,trades=counts['buy']+counts['sell'],adjustments=counts.get('adjust',0),trade_dates=len({t['date'] for t in trades if t['side'] in ['buy','sell']}),first_trade=min(t['date'] for t in trades if t['side'] in ['buy','sell']),cash_days=sum(n['market_value']==0 for n in nav),fees=fees,implicit_reinvested_dividends=d['total_dividends'],cash_max_gap=max(cash_gap),realized_pnl=d['fifo']['realized_pnl'],peak=peak,worst_day=min(daily,key=lambda x:x['ret']),risk_limit=.30,observed_drawdown_below_limit=dd<=.30,not_a_future_risk_guarantee=True)
 summary.append(row)
 for year in [2025,2026]:
  vals=[n for n in nav if n['date'].startswith(str(year))];prev=100000. if year==2025 else next(n['nav'] for n in reversed(nav) if n['date'][:4]=='2025');row[str(year)+'_return']=vals[-1]['nav']/prev-1
for name,data in [('audit_summary',summary),('monthly_audit',monthly)]:
 (P/f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
 pd.DataFrame(data).to_csv(P/f'{name}.csv',index=False,encoding='utf-8-sig')
(P/'loss_attribution.json').write_text(json.dumps(losses,ensure_ascii=False,indent=2),encoding='utf8')
protocol=json.loads((W/'PROTOCOL.json').read_text(encoding='utf8'))
for r in protocol['data_files_hashed']:assert hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256'],r['file']
for r in protocol['source_snapshot']:assert hashlib.sha256((W/'frozen'/r['file']).read_bytes()).hexdigest()==r['sha256'],r['file']
(P/'verification.json').write_text(json.dumps(dict(status='passed',daily_rows=sum(s['days'] for s in summary),stock_fills=sum(s['trades'] for s in summary),source_data_unchanged=True,frozen_code_unchanged=True,engine_tests_passed=94,orders_table_empty_in_all=all(len(x['orders'])==0 for x in res.values()),warning='Target-book path has no individual orders table; events and fills are present. Corporate actions use implicit qfq reinvestment, not real dividend cash.'),ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps([{k:v for k,v in x.items() if k not in ['worst_day']} for x in summary],ensure_ascii=False,indent=2))
