from pathlib import Path
import sys,json,importlib.util,time,hashlib,traceback
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];W=ROOT/'work/recent_window_20261020'
sys.dont_write_bytecode=True
sys.path.insert(0,str(W/'frozen/run2/engine_fix'))
import engine
from engine.api import run_backtest,result_to_json
spec=importlib.util.spec_from_file_location('recent_v46',W/'frozen/run2/os/strategy_v46.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
EXT=Path('C:<LOCAL_HOME>/OneDrive/文档/deepseek-harness/default-workspace')
OUT=ROOT/'reports/recent2025-20261020';OUT.mkdir(exist_ok=False)
protocol=json.loads((W/'PROTOCOL.json').read_text(encoding='utf8'))
(OUT/'PROTOCOL.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf8')
df=pd.read_csv(W/'frozen/qrun_1416/os/out/regime_v1.csv',dtype={'date':str})
labels=dict(zip(df.date,df.state));summary=[]
print('ENGINE',engine.ENGINE_VERSION,engine.__file__,flush=True)
for bull,trim,key in [(.6,False,'A60_original'),(.8,False,'B80_original'),(.6,True,'C60_fast_reduce'),(.8,True,'D80_fast_reduce')]:
 t0=time.time()
 try:
  strategy=mod.BullBearOSv46(exposure={'BULL':bull,'NEUTRAL':.25,'BEAR':0.,'CRASH':0.},neutral_trim=trim,trim_weak_only=False,name=key)
  r=run_backtest(strategy,start='2025-01-01',end='2026-10-19',capital=100000,data_dir=EXT/'qrun_1416/data',rebalance='Q',regime_labels=labels,slippage_bps=10,commission_bps=2.5,min_commission=5,cash_rate=0,warmup_days=400,benchmark=['sh000300','sz399006'],cache_dir=W/'cache',name=key)
  (OUT/f'{key}.json').write_text(json.dumps(result_to_json(r,include_nav=True,include_trades=True,include_positions=True),ensure_ascii=False,allow_nan=False),encoding='utf8')
  for tab in ['nav','trades','orders','events','positions']:
   if hasattr(r.get(tab),'to_csv'):r[tab].to_csv(OUT/f'{key}_{tab}.csv',index=False,encoding='utf-8-sig')
  row=dict(variant=key,bull_budget=bull,neutral_budget=.25,fast_reduce=trim,**r['summary']);summary.append(row)
  pd.DataFrame(summary).to_csv(OUT/'summary.csv',index=False,encoding='utf-8-sig')
  (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,default=str,indent=2),encoding='utf8')
  print(json.dumps(dict(variant=key,seconds=round(time.time()-t0,1),return_=r['summary']['total_return'],maxdd=r['summary']['max_drawdown'],final=r['summary']['final_nav'],trades=r['summary']['trade_count'],actual_window=[r['start'],r['end']],warnings=r['warnings']),ensure_ascii=False),flush=True)
 except Exception:
  (OUT/f'{key}_FAILED.txt').write_text(traceback.format_exc(),encoding='utf8');traceback.print_exc();raise
print('COMPLETED',OUT,flush=True)
