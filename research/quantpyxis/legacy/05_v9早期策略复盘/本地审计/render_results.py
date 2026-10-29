"""Render backtest result cards from the audited JSON/CSV, with no simulated values."""
from pathlib import Path
import json,csv,sys
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parent
OUT=ROOT.parents[1]/'deliverables'
FONT='C:/Windows/Fonts/msyh.ttc'
def f(n):return ImageFont.truetype(FONT,n)
COLORS=dict(ink='#17263D',muted='#61718A',blue='#2468DF',red='#D9564B',grey='#8A96A7',line='#E4EAF3')

def render(freq):
    r=json.loads((ROOT/'results'/f'R4-{freq}.json').read_text(encoding='utf-8'))
    analysis=json.loads((ROOT/'results'/f'R4-{freq}_analysis.json').read_text(encoding='utf-8'))
    es=r['equity']; ds=[x['date'] for x in es]
    nav=[100000]+[x['total'] for x in es]
    returns=[(v/100000-1)*100 for v in nav]
    bench=[100000]
    with (ROOT/'results'/f'R4-{freq}_sz399006_matched.csv').open(encoding='utf-8-sig') as stream:
        bench += [float(x['matched_nav']) for x in csv.DictReader(stream)]
    benchret=[(v/100000-1)*100 for v in bench]
    peak=100000.; dd=[]
    for v in nav:
        peak=max(peak,v);dd.append((v/peak-1)*100)
    im=Image.new('RGB',(1600,1280),'#F3F6FB');d=ImageDraw.Draw(im)
    def text(x,y,s,size=26,color=None):d.text((x,y),s,font=f(size),fill=color or COLORS['ink'])
    def box(rect,fill='white'):d.rounded_rectangle(rect,radius=18,fill=fill)
    label='季度低频版' if freq=='Q' else '月度对照版'
    text(62,42,label+'  ·  v9 纠错后回测',44)
    text(64,111,'2026.04.01—2026.09.30  ｜  初始资金 100,000 元  ｜  125 个交易日',25,COLORS['muted'])
    d.rounded_rectangle((1210,42,1538,95),radius=14,fill='#E6EDFA')
    text(1240,52,'本地复算 · 非平台截图',22,COLORS['blue'])
    metrics=[('区间收益',f"+{r['return']*100:.2f}%",COLORS['red']),('最大回撤',f"{r['audited_max_drawdown']*100:.2f}%",COLORS['ink']),('成交笔数',str(r['trades'])+' 笔',COLORS['ink']),('平均收盘仓位',f"{r['mean_exposure']*100:.2f}%",COLORS['ink'])]
    for i,(name,value,color) in enumerate(metrics):
        x=62+i*375;box((x,174,x+353,318));text(x+24,194,name,24,COLORS['muted']);text(x+24,240,value,42,color)
    box((62,345,1538,805));text(88,365,'累计收益与同仓位指数对照',29)
    text(92,414,'— 策略（已扣费用）',23,COLORS['blue']);text(436,414,'— 匹配实际仓位的创业板指＋现金',23,COLORS['grey'])
    x0,y0,x1,y1=155,481,1472,733
    low=min(-5,min(returns+benchret));high=max(returns+benchret)*1.10
    low=int(low//10)*10;high=(int(high//10)+1)*10
    def xx(i):return x0+i*(x1-x0)/(len(nav)-1)
    def yy(v):return y1-(v-low)/(high-low)*(y1-y0)
    for j in range(5):
        v=low+(high-low)*j/4;y=yy(v)
        d.line((x0,y,x1,y),fill=COLORS['line'],width=2);text(83,y-14,f'{v:.0f}%',19,COLORS['muted'])
    d.line([(xx(i),yy(v)) for i,v in enumerate(returns)],fill=COLORS['blue'],width=5)
    d.line([(xx(i),yy(v)) for i,v in enumerate(benchret)],fill=COLORS['grey'],width=4)
    text(x1-125,yy(returns[-1])-39,f'+{returns[-1]:.2f}%',23,COLORS['blue'])
    for month in range(4,10):
        i=next(i for i,x in enumerate(ds,1) if x.startswith(f'2026-{month:02d}'))
        text(xx(i)-18,751,f'{month}月',21,COLORS['muted'])
    box((62,828,1538,1070));text(88,846,'从历史最高净值回撤',28)
    a,b,c,e=155,907,1472,1024
    bottom=max(20,(-min(dd)//5+1)*5)
    d.line((a,b,c,b),fill=COLORS['line'],width=2)
    pts=[(xx(i),b+(-v/bottom)*(e-b)) for i,v in enumerate(dd)]
    d.polygon([(a,b)]+pts+[(c,b)],fill='#FCE9E7');d.line(pts,fill=COLORS['red'],width=3)
    text(89,b-12,'0%',19,COLORS['muted']);text(80,e-12,f'−{bottom:.0f}%',19,COLORS['muted'])
    text(92,1100,f"期末资金 {r['final']:,.2f} 元   ｜   有交易的日期 {r['operation_days']} 天   ｜   费用 {r['fees']:.2f} 元",25)
    matched=analysis['benchmarks'][1]['exposure_matched_return']*100
    text(92,1142,f'同仓位创业板指对照 +{matched:.2f}%；对照未扣交易费用、现金利息为零。',23,COLORS['muted'])
    text(92,1190,'原有前复权行情快照；样本内研究，非实盘收益，未恢复历史涨跌停与企业行动。',21,COLORS['muted'])
    name=OUT/f'v9纠错回测_{label}_20261019.png';im.save(name)
    print(name)

if __name__=='__main__':
    render('Q');render('M')
