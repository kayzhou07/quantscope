# S1 v03 CANDIDATE, NOT VALIDATED: R2 + full-ranked affordability scan incl estimated one-lot buy costs.
# CEO 20261020 P-R0: platform bridge, not exact local-engine reproduction.
# Frozen T-close regime inputs; platform PIT pool, dynamic prices and actual fills.
import math
import bisect
import json
import numpy as np
import pandas as pd
VARIANT='R2'
REGIME_END='20261019'
RUN_TEXT='20120803N 20130625C 20130704N 20130708C 20130716N 20130822E 20130904N 20131210B 20131217N 20140115E 20140801N 20140918B 20141226N 20150127B 20150619C 20160713E 20160819N 20160920E 20161024N 20170309B 20170406N 20170822B 20171102N 20180420E 20180514N 20180702E 20180806C 20180813E 20180815C 20180827E 20180906C 20180926E 20181008C 20190215E 20190228N 20190513C 20190610N 20190910B 20191009N 20191017B 20191024N 20191220B 20200203C 20200210N 20200224B 20200320E 20200323C 20200403E 20200420N 20200605B 20200916N 20210531B 20210618N 20220118E 20220315C 20220322E 20220330C 20220419E 20220421C 20220606E 20220930C 20221205E 20230120N 20230313E 20230407N 20230427E 20230803N 20230817E 20240522N 20240603E 20241009N 20241017C 20241024N 20241129B 20241231N 20250226B 20250403N 20250630B 20251009N 20251107B 20251125N 20260112B 20260323N 20260717C 20260724N 20260730C 20260810N 20260916E 20260924N 20261016E'
RUNS=RUN_TEXT.split()
RUN_DATES=[x[:8] for x in RUNS]

def init(context):
    set_benchmark('000300.SH')
    enable_open_bar()
    set_commission(PerShare(type='stock',cost=.00025,min_trade_cost=5.0))
    set_slippage(PriceSlippage(.002))
    set_volume_limit(.10,.10)
    context.book=None
    context.first=True
    context.exited=False
    context.states=[]
    context.peak=100000.
    context.maxdd=0.
    context.win_audit=initial_win_audit()
    log.info('CEO_P_'+VARIANT+' external frozen T-close regime; PIT platform universe/signals/fills; platform tax defaults require reconciliation; research only')

def held(context):
    return {s:p for s,p in context.portfolio.stock_account.positions.items() if p.amount>0}

def price(symbols,day,fields,count=260,fq='pre'):
    result=get_price(symbols,None,day,'1d',fields,skip_paused=False,fq=fq,bar_count=count,is_panel=False)
    if isinstance(result,pd.DataFrame):
        if len(symbols)!=1:raise ValueError('Unexpected multi-symbol frame')
        return {symbols[0]:result}
    return result

def state_at(day):
    if day>REGIME_END:raise ValueError('Frozen regimes end '+REGIME_END)
    i=bisect.bisect_right(RUN_DATES,day)-1
    if i<0:raise ValueError('Missing regime')
    return RUNS[i][-1]

def dual_state(day):
    data=price(['000300.SH','399006.SZ'],day,['close'],120)
    flags=[]
    for s in ['000300.SH','399006.SZ']:
        f=data.get(s)
        if f is None or len(f)<120 or f['close'].isna().any():return 'E'
        x=f['close']
        flags.append(bool(x.iloc[-1]>x.mean() and x.iloc[-20:].mean()>x.iloc[-60:].mean()))
    return 'B' if all(flags) else 'N' if any(flags) else 'E'

def selection(context,day,state):
    exposure=.60 if state=='B' else .25 if state=='N' else 0.
    if not exposure:return {}
    cal=pd.DatetimeIndex(get_trade_days(end_date=day,count=260)).normalize()
    if len(cal)<253:raise ValueError('Missing warmup')
    info=get_all_securities('stock',day)
    symbols=sorted([str(s) for s in info.index if (str(s).endswith('.SH') and str(s).startswith(('600','601','603','605'))) or (str(s).endswith('.SZ') and str(s).startswith(('000','001','002','003','300','301')))])
    rows={};returns={};count={'universe':len(symbols),'history':0,'eligible':0}
    for i in range(0,len(symbols),100):
        batch=symbols[i:i+100]
        adj=price(batch,day,['close'],260,'pre')
        raw=price(batch,day,['close','turnover','is_st','is_paused'],60,None)
        for s in batch:
            f=adj.get(s);g=raw.get(s)
            if f is None or g is None or f.empty or g.empty:continue
            f=f.copy();g=g.copy()
            f.index=pd.DatetimeIndex(f.index).normalize();g.index=pd.DatetimeIndex(g.index).normalize()
            f=f.loc[f.index<=cal[-1]];g=g.loc[g.index<=cal[-1]]
            if f.empty or g.empty or g.index[-1]!=cal[-1]:continue
            x=f['close'].reindex(cal).where(lambda z:z>0)
            if x.notna().sum()<230:continue
            x=x.ffill()
            if pd.isna(x.iloc[-253]) or pd.isna(x.iloc[-127]):continue
            count['history']+=1
            if not np.isfinite(g['is_st'].iloc[-1]) or not np.isfinite(g['is_paused'].iloc[-1]):raise ValueError('Missing ST/pause '+s)
            if g['is_st'].iloc[-1]!=0 or g['is_paused'].iloc[-1]!=0:continue
            r=x.pct_change(fill_method=None)
            vol=float(r.iloc[-61:].std()*math.sqrt(252))
            if not np.isfinite(vol) or vol<=0:continue
            rawclose=float(g['close'].iloc[-1])
            liquid=float(g['turnover'].reindex(cal[-60:]).mean())>=2e8
            trend=bool(x.iloc[-1]>x.iloc[-60:].mean()>x.iloc[-120:].mean())
            valid=rawclose>=2 and liquid and trend and (VARIANT=='R4' or vol<=.55)
            count['eligible']+=int(valid)
            rows[s]=(float(x.iloc[-22]/x.iloc[-253]-1)/vol,float(x.iloc[-22]/x.iloc[-127]-1),1/vol,rawclose,valid,float(x.iloc[-6]/x.iloc[-64]-1),float(x.iloc[-6]/x.iloc[-127]-1))
            returns[s]=r.iloc[-60:]
    if not rows:
        log.info('NO_FEATURES '+day+' '+str(count));return {}
    df=pd.DataFrame.from_dict(rows,orient='index',columns=['ras','m6','inv','raw','ok','new3','new6'])
    if VARIANT=='R5':
        base=df[df.ok]
        score=.5*base.new3.rank(pct=True)+.3*base.new6.rank(pct=True)+.2*base.inv.rank(pct=True)
    else:
        score=.5*df.ras.rank(pct=True)+.3*df.m6.rank(pct=True)+.2*df.inv.rank(pct=True)
        score=score[df.ok]
    candidates=sorted(score.dropna().index,key=lambda s:(-float(score[s]),s))
    nav=float(context.portfolio.stock_account.total_value)
    selected=pick_affordable_ranked(candidates,list(held(context)),nav,exposure,df['raw'].to_dict(),10)
    if VARIANT=='R7' and selected:
        hist=pd.DataFrame({s:returns[s] for s in selected}).dropna()
        if len(hist)<50:return {}
        sigma=float(hist.mean(axis=1).std()*math.sqrt(252))
        if not np.isfinite(sigma) or sigma<=0:return {}
        exposure*=min(1.,.14/sigma)
    weights={s:exposure/len(selected) for s in selected}
    log.info('SELECT '+day+' '+state+' '+str(count)+' '+str(weights))
    return weights

def target(context,weights,day,reason):
    context.book={'weights':weights,'qty':None,'age':0,'signal':day,'reason':reason}
    log.info('TARGET '+day+' '+reason+' '+str(weights))


def observe_account_day(stats, day, nav, has_positions):
    # Account-close P&L includes actual platform charges and cash adjustments.
    # A fully cash, unchanged day is flat; an exit day remains a risk day.
    if not math.isfinite(nav) or nav <= 0:
        raise ValueError('Invalid NAV in diagnostics')
    if stats.get('last_day') == day:
        raise ValueError('Duplicate account-close observation')
    pnl = nav - stats['previous_nav']
    risk_day = bool(has_positions or stats['previous_positions'])
    stats['days'] += 1
    positive = pnl > 0.000001
    negative = pnl < -0.000001
    stats['positive_days'] += int(positive)
    stats['negative_days'] += int(negative)
    stats['flat_days'] += int(not positive and not negative)
    stats['risk_days'] += int(risk_day)
    stats['risk_positive_days'] += int(risk_day and positive)
    stats['flat_cash_days'] += int(not risk_day and not positive and not negative)
    if positive: stats['win_sum'] += pnl
    if negative: stats['loss_sum'] += pnl
    stats['previous_nav'] = nav
    stats['previous_positions'] = bool(has_positions)
    stats['last_day'] = day
    return stats

def initial_win_audit():
    return dict(previous_nav=100000.0, previous_positions=False,
                days=0, positive_days=0, negative_days=0, flat_days=0,
                risk_days=0, risk_positive_days=0, flat_cash_days=0,
                win_sum=0.0, loss_sum=0.0, last_day=None)

def win_audit_summary(stats):
    result = dict(stats)
    result['winrate_all'] = stats['positive_days']/stats['days'] if stats['days'] else None
    result['winrate_risk'] = stats['risk_positive_days']/stats['risk_days'] if stats['risk_days'] else None
    result['mean_win_day'] = stats['win_sum']/stats['positive_days'] if stats['positive_days'] else None
    result['mean_loss_day'] = stats['loss_sum']/stats['negative_days'] if stats['negative_days'] else None
    result['daily_profit_factor'] = stats['win_sum']/-stats['loss_sum'] if stats['loss_sum'] < 0 else None
    return result


def pick_affordable_ranked(candidates, held_symbols, nav, exposure, raw_prices, top_n=10):
    # Screen the entire ranked list, then choose up to top_n feasible names.
    # Preserve the ORIGINAL candidate ranks for the retention buffer.
    if nav <= 0 or exposure <= 0 or top_n <= 0:
        return []
    budget = nav*exposure/top_n
    feasible = set()
    for symbol in candidates:
        price = float(raw_prices.get(symbol, float('nan')))
        if not math.isfinite(price) or price <= 0:
            continue
        value = price*100*1.001
        estimated_cost = value + max(5.0, value*0.00025) + value*0.00001
        if estimated_cost <= budget:
            feasible.add(symbol)
    rank = {symbol:i for i,symbol in enumerate(candidates)}
    selected = sorted([s for s in held_symbols if s in feasible and rank.get(s,10**9)<2*top_n],
                      key=lambda s:rank[s])[:top_n]
    for symbol in candidates:
        if len(selected) >= top_n:
            break
        if symbol in feasible and symbol not in selected:
            selected.append(symbol)
    return selected

def before_trading(context):
    pass

def handle_bar(context,bar_dict):
    book=context.book
    if book is None:return
    book['age']+=1
    if book['age']>20:
        log.info('EXPIRED '+str(book));context.book=None;return
    if book['qty'] is None:
        a=context.portfolio.stock_account
        nav=float(a.available_cash+a.frozen_cash)
        for s,p in held(context).items():
            b=bar_dict[s]
            nav+=float(p.market_value) if b.is_paused else p.amount*float(b.open)
        qty={s:0 for s in held(context)}
        for s,w in book['weights'].items():
            opening=float(bar_dict[s].open)
            if not np.isfinite(opening) or opening<=0:raise ValueError('Missing open '+s)
            qty[s]=max(0,int((nav*w-5)/(opening*1.001*100))*100)
        book['qty']=qty
    for side in ['sell','buy']:
        for s,desired in sorted(book['qty'].items()):
            p=context.portfolio.stock_account.positions.get(s)
            current=int(p.amount) if p is not None else 0
            delta=desired-current
            if not delta or (delta>0)!=(side=='buy'):continue
            b=bar_dict[s];opening=float(b.open)
            limit=float(b.high_limit if side=='buy' else b.low_limit)
            blocked=b.is_paused or not np.isfinite(opening) or opening<=0 or (np.isfinite(limit) and limit>0 and (opening>=limit if side=='buy' else opening<=limit))
            if side=='buy' and b.is_st:blocked=True
            if side=='sell' and (p is None or p.available_amount<min(current-desired,100)):blocked=True
            if blocked:
                log.info('BLOCKED '+s+' '+side);continue
            attempt=desired
            if side=='buy':
                cash=float(context.portfolio.stock_account.available_cash)
                while attempt>current:
                    value=(attempt-current)*opening*1.001
                    if value+max(5.,value*.00025)+value*.00001<=cash:break
                    attempt-=100
                if attempt<=current:
                    log.info('CASH_BLOCK '+s);continue
            oid=order_target(s,int(attempt))
            log.info('ORDER '+get_datetime().strftime('%Y%m%d')+' '+s+' qty='+str(attempt)+' id='+str(oid)+' signal='+book['signal'])
    complete=True
    for s,desired in book['qty'].items():
        p=context.portfolio.stock_account.positions.get(s)
        if (int(p.amount) if p is not None else 0)!=desired:complete=False
    if complete:context.book=None

def after_trading(context):
    day=get_datetime().strftime('%Y%m%d')
    a=context.portfolio.stock_account
    nav=float(a.total_value);cash=float(a.available_cash+a.frozen_cash)
    context.peak=max(context.peak,nav);context.maxdd=max(context.maxdd,1-nav/context.peak)
    log.info('NAV '+day+' nav='+str(nav)+' cash='+str(cash)+' maxdd='+str(context.maxdd)+' held='+str({s:p.amount for s,p in held(context).items()}))
    if abs(nav-context.win_audit['previous_nav']) <= 0.000001 and (held(context) or context.win_audit['previous_positions']):
        log.info('RISK_FLAT_DAY '+json.dumps(dict(day=day,nav=nav,cash=cash,
            holdings={s:dict(qty=int(p.amount),market_value=float(p.market_value)) for s,p in held(context).items()},
            pending_book=context.book),sort_keys=True))
    observe_account_day(context.win_audit, day, nav, bool(held(context)))
    if day in ['20231229','20261019']:
        log.info('WIN_AUDIT '+json.dumps(win_audit_summary(context.win_audit),sort_keys=True))
    state=dual_state(day) if VARIANT=='R6' else state_at(day)
    context.states.append(state)
    days=pd.DatetimeIndex(get_trade_days(start_date=day,end_date=(pd.Timestamp(day)+pd.Timedelta(days=16)).strftime('%Y%m%d')))
    if len(days)<2:raise ValueError('Next session calendar unavailable')
    now=pd.Timestamp(day);nxt=days[1]
    month_end=(now.month!=nxt.month or now.year!=nxt.year)
    scheduled=month_end and (VARIANT not in ['R0','R1'] or now.month in [3,6,9,12])
    initial=context.first and VARIANT!='R0'
    weekly=now.isocalendar()[:2]!=nxt.isocalendar()[:2]
    reentry=(VARIANT in ['R3','R4','R5','R6','R7'] and weekly and context.exited and not held(context) and context.book is None and len(context.states)>=2 and all(x in ['B','N'] for x in context.states[-2:]))
    if state in ['E','C']:
        if held(context) or context.book is not None:
            context.exited=True
            target(context,{},day,'daily_exit')
    elif scheduled or initial or reentry:
        target(context,selection(context,day,state),day,'scheduled' if scheduled else 'initial' if initial else 'reentry')
        if reentry:context.exited=False
    context.first=False
    if day==REGIME_END:log.info('FINAL '+VARIANT+' nav='+str(nav)+' maxdd='+str(context.maxdd))
