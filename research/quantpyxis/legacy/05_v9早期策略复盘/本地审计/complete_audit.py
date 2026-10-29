"""Post-run data hygiene and diagnostic uncertainty, without parameter search."""
from replay_audit import *


def valid(r):
    _,o,c,h,l,v,a=r
    return 0<l<=min(o,c)<=max(o,c)<=h and v>=0 and a>=0


def main():
    original=engine.load_all
    def clean():
        panel,idx=original()
        return {c:[r for r in rows if valid(r)] for c,rows in panel.items()},idx
    engine.load_all=clean
    mk=engine.Market()
    hygiene={}
    for freq in ('M','Q'):
        ns,run=patched_runner(4)
        cfg=dict(variants.BASE,tag='R5-'+freq,trail=.12,rebal_months=(variants.BASE['rebal_months'] if freq=='M' else ['2026-03','2026-06']))
        r=metrics(run(mk,cfg)); ref=json.loads((OUT/('R4-'+freq+'.json')).read_text(encoding='utf-8'))
        hygiene[freq]=dict(final=r['final'],identical_trade_log=r['trade_log']==ref['trade_log'],identical_equity=r['equity']==ref['equity'])
        assert r['equity']==ref['equity'] and r['trade_log']==ref['trade_log'], 'Data cleaning changed results; report separately'
    save('clean_data_verification.json',dict(removed_pseudo_bars=4,missing_symbol='sh601026',verification=hygiene,rule='Drop nonpositive/invalid OHLC bars before features; no fabricated observations'))
    uncertainty={}; diagnostics={}
    for freq in ('M','Q'):
        key='R4-'+freq; r=json.loads((OUT/(key+'.json')).read_text(encoding='utf-8'))
        vals=[100000]+[x['total'] for x in r['equity']]
        ret=[y/x-1 for x,y in zip(vals,vals[1:])]
        rng=random.Random(20261019); n=len(ret); stats=[]
        for _ in range(2000):
            sampled=[]
            while len(sampled)<n:
                start=rng.randrange(n)
                sampled.extend(ret[(start+j)%n] for j in range(10))
            sampled=sampled[:n]; sd=statistics.stdev(sampled)
            if sd:stats.append(statistics.mean(sampled)/sd*252**.5)
        stats.sort()
        uncertainty[key]=dict(point=r['audited_sharpe'],conditional_percentile_95=[stats[int(len(stats)*.025)],stats[int(len(stats)*.975)]],method='circular moving-block bootstrap; block=10 sessions; 2000 samples; seed=20261019; zero cash yield',warning='Conditional descriptive interval only; not selection-adjusted, not out-of-sample significance or prospective return interval')
        analysis=json.loads((OUT/(key+'_analysis.json')).read_text(encoding='utf-8'))
        fifo=analysis['fifo']; attr=analysis['attribution']
        month=[]
        prior=100000
        for m in ['2026-04','2026-05','2026-06','2026-07','2026-08','2026-09']:
            rows=[e for e in r['equity'] if e['date'].startswith(m)]
            end=rows[-1]['total']; month.append(dict(month=m,start_nav=prior,end_nav=end,return_=end/prior-1,pnl=end-prior,mean_exposure=sum(e['exposure'] for e in rows)/len(rows)));prior=end
        peak_date,trough_date=r['dd_window']; a=next(x for x in r['equity'] if x['date']==peak_date);b=next(x for x in r['equity'] if x['date']==trough_date)
        july=[x for x in fifo if x['buy_date']=='2026-07-01']
        diagnostics[key]=dict(monthly=month,top3_net_contribution=sum(x['net_pnl'] for x in attr[:3]),net_profit=r['final']-100000,top3_share=sum(x['net_pnl'] for x in attr[:3])/(r['final']-100000),july_new_lots_pnl=sum(x['net_pnl'] for x in july),july_new_lots=july,max_dd_peak_nav=a['total'],max_dd_trough_nav=b['total'],drawdown_cash_loss=a['total']-b['total'],flat_cash_from=next((e['date'] for i,e in enumerate(r['equity']) if e['n_positions']==0 and all(y['n_positions']==0 for y in r['equity'][i:])),None))
        table(key+'_monthly.csv',month)
    save('bootstrap_diagnostic.json',uncertainty);save('loss_diagnostics.json',diagnostics)
    # Diagnostic ablation: same old engine, same pool; which individual trades change with max floor?
    diffs={}
    for freq in ('M','Q'):
        old=json.loads((OUT/('R0-'+freq+'.json')).read_text(encoding='utf-8'))
        new=json.loads((OUT/('R1-'+freq+'.json')).read_text(encoding='utf-8'))
        _,a=fifo_audit(old,mk);_,b=fifo_audit(new,mk)
        aa={x['code']:x for x in a};bb={x['code']:x for x in b}
        diffs[freq]=sorted([dict(code=c,name=mk.names.get(c,''),old_net=aa.get(c,{}).get('net_pnl',0),new_net=bb.get(c,{}).get('net_pnl',0),change=round(bb.get(c,{}).get('net_pnl',0)-aa.get(c,{}).get('net_pnl',0),2)) for c in set(aa)|set(bb)],key=lambda x:x['change'])
    save('floor_only_attribution_delta.json',diffs)
    print(json.dumps(dict(hygiene=hygiene,uncertainty=uncertainty,diagnostics=diagnostics,floor_only_delta=diffs),ensure_ascii=False,indent=1))


if __name__=='__main__':main()
