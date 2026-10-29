from pathlib import Path
import sys,json,hashlib,time
import numpy as np
import pandas as pd
BASE=Path(r'C:<LOCAL_HOME>/OneDrive/文档/deepseek-harness/default-workspace')
sys.path.insert(0,str(BASE/'winrate_backtest_20261020/round5/SEAL/snapshot/strategy'))
sys.path.insert(0,str(BASE/'qrun_1416'))
import variants as V
from engine.api import run_backtest
OUT=Path(__file__).resolve().parents[1]/'outputs/round6_20261021'
OUT.mkdir(exist_ok=True)
labels=pd.read_csv(BASE/'qrun_1416/os/out/regime_v1.csv',dtype={'date':str})
mapping={'BULL':'B','NEUTRAL':'N','BEAR':'E','CRASH':'C'}
labels=dict(zip(labels.date,labels.state.map(mapping)))
specs={'A':1.,'A80':.8}
protocol=dict(date='2026-10-21',start='2014-07-01',end='2026-10-19',capital=100000,
    candidates=specs,change='Only BULL target budget 1.0 -> .8; N=.30; top15 buffer20; existing seen history',
    source_sha256=hashlib.sha256(Path(V.__file__).read_bytes()).hexdigest(),
    acceptance='Do not promote on winrate; require reproducible A, lower maxDD/worst day, assess return cost and full-year results; no further parameter search')
(OUT/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf8')
rows=[]
for tag,bull in specs.items():
    kw=dict(state_mode='frozen',exposure={'B':bull,'N':.3,'E':0,'C':0},top_n=15,buffer=20,max_vol=.55,score_mode='base',afford='drop')
    for stress in [False,True]:
        name=tag+('_cost2x' if stress else '')
        t=time.time()
        result=run_backtest(V.Variant(name=name,**kw),start='2014-07-01',end='2026-10-19',capital=100000,
            data_dir=BASE/'qrun_1416/data',warmup_days=340,rebalance='M',benchmark=['sh000300','sz399006'],
            slippage_bps=20 if stress else 10,commission_bps=5 if stress else 2.5,min_commission=5,
            regime_labels=labels,cache_dir=BASE/'qrun_1416/tmp/engine-dev/cache')
        nav=result['nav']; n=nav.nav.astype(float); returns=n.pct_change().fillna(0)
        peak=n.cummax().clip(lower=100000)
        row=dict(name=name,total_return=float(n.iloc[-1]/100000-1),max_dd=float((n/peak-1).min()),
            worst_day=float(returns.min()),worst_day_date=str(returns.idxmin()),avg_exposure=float(nav.exposure.mean()),
            sharpe=float(returns.mean()/returns.std()*np.sqrt(252)),final_nav=float(n.iloc[-1]),
            fees=result['metrics'].get('total_fees'),elapsed=time.time()-t,days=len(n))
        nav.to_csv(OUT/(name+'_nav.csv'))
        result['trades'].to_csv(OUT/(name+'_trades.csv'),index=False)
        row['yearly']={}
        prev=100000.
        for year,part in n.groupby(n.index.astype(str).str[:4]):
            row['yearly'][year]=float(part.iloc[-1]/prev-1);prev=float(part.iloc[-1])
        rows.append(row)
        (OUT/'local_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
assert abs(rows[0]['total_return']-1.7456069325168508)<1e-9
assert abs(rows[0]['max_dd']+.23979364746919452)<1e-9
print('A_REPRODUCTION_PASS',flush=True)
