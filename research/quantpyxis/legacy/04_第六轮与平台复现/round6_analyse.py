from pathlib import Path
import pandas as pd
import json
out=Path(__file__).resolve().parents[1]/'outputs/round6_20261021'
periods=[('bull_2014_2015','2014-07-01','2015-06-30'),('bull_2019_2021','2019-01-01','2021-02-28'),
 ('latest_since_202409','2024-09-01','2026-10-19'),('crash_2015_2016','2015-06-01','2016-02-29'),
 ('covid_2020','2020-01-01','2020-04-30'),('year_2018','2018-01-01','2018-12-31'),
 ('april_2025','2025-04-01','2025-04-30'),('year_2026','2026-01-01','2026-10-19')]
rows=[];roll=[]
for tag in ['A','A80']:
    df=pd.read_csv(out/(tag+'_nav.csv'),index_col=0,parse_dates=True)
    n=df.nav.astype(float); peak=n.cummax().clip(lower=100000)
    for label,start,end in periods:
        sub=n.loc[start:end];before=n.loc[n.index<start];base=float(before.iloc[-1]) if len(before) else 100000.
        local_peak=sub.cummax().clip(lower=base)
        rows.append(dict(strategy=tag,period=label,start=str(sub.index[0].date()),end=str(sub.index[-1].date()),
            total_return=float(sub.iloc[-1]/base-1),window_dd=float((sub/local_peak-1).min()),
            inherited_dd=float((sub/peak.loc[sub.index]-1).min())))
    padded=pd.concat([pd.Series([100000.]),n.reset_index(drop=True)],ignore_index=True)
    for w in [252,756]:
        r=(padded/padded.shift(w)-1).dropna()
        roll.append(dict(strategy=tag,sessions=w,min=float(r.min()),median=float(r.median()),max=float(r.max()),positive_ratio=float((r>0).mean())))
pd.DataFrame(rows).to_csv(out/'segments.csv',index=False)
(out/'rolling.json').write_text(json.dumps(roll,indent=2),encoding='utf8')
print(pd.DataFrame(rows).round(4).to_string(index=False))
print(json.dumps(roll))
