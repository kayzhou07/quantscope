from pathlib import Path
import re,json,csv,collections,hashlib,ast

ROOT=Path(__file__).resolve().parents[2]
REPORT=ROOT/'reports'
OUT=REPORT/'v9q-platform-audit'
OUT.mkdir(exist_ok=True)
logs=(REPORT/'v9q-platform-logs.txt').read_text(encoding='utf-8')
trades_text=(REPORT/'v9q-platform-trades.txt').read_text(encoding='utf-8')
num=lambda s:float(s.replace(',',''))
rows=[]
for match in re.finditer(r'AUDIT_CLOSE (\d{8}) nav=([\d.eE+-]+) cash=([\d.eE+-]+) market=([\d.eE+-]+) held=(\[[^\n]*\])',logs):
    date,nav,cash,mv,held=match.groups()
    nav,cash,mv=map(float,(nav,cash,mv))
    assert abs(nav-cash-mv)<1e-7
    rows.append(dict(date=date,nav=nav,cash=cash,market_value=mv,exposure=mv/nav,holdings=ast.literal_eval(held)))
rows.sort(key=lambda x:x['date'])
assert len(rows)==125 and len({x['date'] for x in rows})==125
peak=100000.;peak_date='20260331';dd=0.;window=None
for row in rows:
    if row['nav']>peak:peak=row['nav'];peak_date=row['date']
    loss=1-row['nav']/peak
    if loss>dd:dd=loss;window=[peak_date,row['date']]
trades=[]
for line in trades_text.splitlines():
    match=re.search(r'- row "(2026-\d\d-\d\d [^\"]+)"',line)
    if not match:continue
    fields=match.group(1).split()
    if len(fields)!=11:raise ValueError(fields)
    date,time,code,name,side,price,shares,amount,margin,fee,realized=fields
    trades.append(dict(date=date,time=time,code=code,name=name,side=side,price=num(price),shares=int(num(shares)),signed_amount=num(amount),fee=num(fee),display_closed_pnl=None if realized=='--' else num(realized)))
assert len(trades)==16
trades=sorted(trades,key=lambda x:x['date'])
book=collections.defaultdict(lambda:dict(buy=0.,sell=0.,fees=0.,shares=0))
fifo=collections.defaultdict(collections.deque);closed=[]
for t in trades:
    c=t['code'];b=book[c];q=abs(t['shares']);amount=abs(t['signed_amount']);unit=amount/q;fee=t['fee']/q
    b['fees']+=t['fee'];b['name']=t['name']
    if t['side']=='买入':
        b['buy']+=amount;b['shares']+=q;fifo[c].append([q,unit,fee,t['date']])
    else:
        b['sell']+=amount;b['shares']-=q;left=q
        while left:
            lot=fifo[c][0];n=min(left,lot[0])
            pnl=n*(unit-fee-lot[1]-lot[2])
            closed.append(dict(code=c,name=t['name'],buy_date=lot[3],sell_date=t['date'],shares=n,trade_net_pnl=round(pnl,2)))
            left-=n;lot[0]-=n
            if not lot[0]:fifo[c].popleft()
attrs=[]
for c,b in book.items():
    assert b['shares']==0
    attrs.append(dict(code=c,name=b['name'],trade_net_pnl=round(b['sell']-b['buy']-b['fees'],2),fees=round(b['fees'],2)))
attrs.sort(key=lambda x:-x['trade_net_pnl'])
cashflow=-sum(t['signed_amount']+t['fee'] for t in trades)
residual=rows[-1]['nav']-100000-cashflow
july=sum(x['trade_net_pnl'] for x in closed if x['buy_date']=='2026-07-01')
assert abs(dd-.17346456400750998)<1e-10
for name,data in [('equity',rows),('trades',trades),('fifo',closed),('attribution',attrs)]:
    (OUT/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
source=REPORT/'v9-platform-editor-readback.py'
local=json.loads((Path(__file__).resolve().parent/'results/R4-Q.json').read_text(encoding='utf-8'))
summary=dict(strategy_id='6ad62b5522686300b6eeafb5',task_id='6ad62f02f5be1600b606ab6a',url='https://quant.10jqka.com.cn/view/study-index.html#/backtest-result?id=6ad62f02f5be1600b606ab6a',status='completed',window=['2026-04-01','2026-09-30'],initial=100000,final=rows[-1]['nav'],return_=rows[-1]['nav']/100000-1,max_drawdown=dd,dd_window=window,mean_exposure=sum(x['exposure'] for x in rows)/len(rows),trades=len(trades),trade_dates=sorted({x['date'] for x in trades}),fees_displayed=sum(x['fee'] for x in trades),trade_cashflow=cashflow,cash_adjustment_residual=residual,july_new_lots_net=july,attribution=attrs,local_comparison=dict(final=local['final'],nav_delta=rows[-1]['nav']-local['final'],fees=local['fees'],fee_delta=sum(x['fee'] for x in trades)-local['fees']),submitted_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),notes=['Platform cost_basis includes buy fees; local stop cost omitted fees.','Platform displayed sell fee approximately 0.125% or minimum buy commission plus 0.1% sell tax, unlike local 0.076%; report actual differences, do not claim current real broker taxes.','Gross dividends logged: 50 CNY on 20260428 for 100 shares 301377.SZ, 10 CNY on 20260702 for 500 shares 301526.SZ; after adding 60 CNY, displayed trade precision leaves 0.0267105 CNY residual.'])
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=1))
