from pathlib import Path
import json,zipfile,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'reports/recent2025-20261020';W=ROOT/'work/recent_window_20261020';D=ROOT/'deliverables'
font=Path('C:/Windows/Fonts/msyh.ttc')
if font.exists():font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
plt.rcParams['axes.unicode_minus']=False
keys=['A60_original','B80_original','C60_fast_reduce','D80_fast_reduce'];labels=['A 牛市60%，原规则','B 牛市80%，原规则','C 牛市60%，快速减仓','D 牛市80%，快速减仓'];colors=['#2563eb','#d97706','#0891b2','#be185d']
rs={k:json.loads((P/f'{k}.json').read_text(encoding='utf8')) for k in keys};ss=json.loads((P/'audit_summary.json').read_text(encoding='utf8'));loss=json.loads((P/'loss_attribution.json').read_text(encoding='utf8'))
fig,(ax,ddax)=plt.subplots(2,1,figsize=(12,8),sharex=True,gridspec_kw={'height_ratios':[2,1]});fig.patch.set_facecolor('white')
for k,label,c in zip(keys,labels,colors):
 n=pd.DataFrame(rs[k]['nav']);dates=pd.to_datetime(n.date);ax.plot(dates,n.nav/10000,label=label,color=c,lw=1.7);ddax.plot(dates,100*(n.nav/n.nav.cummax()-1),color=c,lw=1.4)
n=pd.DataFrame(rs[keys[0]]['nav']);dates=pd.to_datetime(n.date)
ax.plot(dates,n.bench_sh000300/10000,label='沪深300价格指数（未扣费）',color='#64748b',lw=1.5,ls='--');ax.plot(dates,n.bench_sz399006/10000,label='创业板价格指数（未扣费）',color='#94a3b8',lw=1.2,ls=':');ax.axhline(10,color='#334155',lw=1,ls='--',label='全现金10万元（零利息）')
ax.set_ylabel('账户 / 指数归一值（万元）');ddax.set_ylabel('策略回撤（%）');ddax.axhline(-30,color='#b91c1c',lw=1,ls='--');ddax.text(dates.iloc[5],-29,'用户容忍上限30%：不是损失保证',fontsize=9,color='#991b1b');ddax.set_ylim(-32,2)
for a in [ax,ddax]:a.grid(alpha=.17);a.spines[['top','right']].set_visible(False)
ax.legend(ncol=2,fontsize=9,loc='upper right');fig.suptitle('本地回测：2025-01-02 至 2026-10-19｜初始10万元',fontsize=15,x=.08,ha='left',y=.985)
fig.text(.08,.938,'请求截止10月20日；底层行情与状态仅到10月19日。四组均亏损，未新增更快再入场规则。',fontsize=10,color='#475569')
fig.text(.08,.025,'固定同一执行模型；现金不计息。非SuperMind平台结果；分红采用隐含复权再投资近似。',fontsize=9,color='#475569');fig.tight_layout(rect=[.035,.05,.99,.93]);fig.savefig(D/'本地回测_202501至20261019_四组对照.png',dpi=150);plt.close(fig)
benchmark={x:rs[keys[0]]['nav'][-1]['bench_'+x]/100000-1 for x in ['sh000300','sz399006']}
lines=['# 2025年初至2026年10月：四组对照回测与亏损复盘','',
'实际本地运行日期：2026-10-20。用户要求2025-01-01至2026-10-20，暂停已解除。本轮不是SuperMind平台新任务；没有修改线上策略或操作实际资金。', '',
'**请求末日尚未覆盖：数据、指数与状态文件截至2026-10-19。本轮实际净值区间2025-01-02—2026-10-19，共432个交易日；没有补造10月20日行情。**用户确认的30%最大期间回撤作为本轮风险审查约束，未在代码中临时添加30%保险丝，不承诺未来不会突破。', '',
'## 结果','',
'四组都亏损。80%原规则在四组中亏得较少，但风险高于60%原规则；快速减仓没有形成有效升级。本轮不晋级任何候选，不按较少亏损宣称策略已能赚钱。全现金是合法对照。', '',
'|版本|牛市/中性目标|快速减仓|期末资产|累计收益|最大回撤|平均股票仓位|买卖笔数/日期数|费用|','|---|---|---|---:|---:|---:|---:|---:|---:|']
for i,s in enumerate(ss):lines.append(f"|{labels[i]}|{60 if i in [0,2] else 80}%/25%|{'开' if i>=2 else '关'}|{s['final']:,.2f}元|{s['return_']:+.4%}|{s['maxdd']:.4%}|{s['mean_exposure']:.2%}|{s['trades']}/{s['trade_dates']}|{s['fees']:.2f}元|")
lines += ['',f"同区间首尾收盘归一的沪深300价格指数{benchmark['sh000300']:+.4%}、创业板指{benchmark['sz399006']:+.4%}；未扣成本、不含现金分红，不是可买卖指数本身或本轮ETF订单。全现金收益0%、股票价格回撤0%，本区间在收益和回撤两项上优于四组。", '',
'## 时点、初始化与四组差别','',
'从2025年初10万元现金新起步，未继承2018连续账户的仓位/利润。预热400会话，实际数据载入2023-05-12起；状态标签使用此前完整历史计算文件。保持原季度末收盘生成信号、下一交易日开盘执行。原策略初始化没有补做前一季度的调仓，因此四组首次成交均为2025-04-01，2025年一季度现金等待；没有为了改善结果追加年初立即建仓。', '',
'评分及筛选沿用冻结V3/V4.6代码：风险调整12−1动量排序50%、6−1动量30%、低波动排序20%，成交额、趋势、波动、整手预算等保持相同。80%组中性仓位仍25%，与历史80/30的s80不同；不能把本次差额当旧s80重现。预算影响一手可负担股票，选中组合也可能改变，不是简单按比例放大原持仓。', '',
'快速减仓使用已有V4.6实现：状态为NEUTRAL且实际股票仓位高于25%+3个百分点时，按比例下调目标至25%，同一中性段只触发一次，下一开盘撮合。季度选股和风险关闭退出保留。本轮没有定义或实现更快再入场，不将结果称为“快进快出组合”。', '',
'## 分年与逐月','',
'|版本|2025全年|2026截至10月19日|','|---|---:|---:|']
for name,s in zip(labels,ss):lines.append(f"|{name}|{s['2025_return']:+.4%}|{s['2026_return']:+.4%}|")
lines += ['', '|月份|A60原规则|B80原规则|C60快速减仓|D80快速减仓|','|---|---:|---:|---:|---:|']
mm=json.loads((P/'monthly_audit.json').read_text(encoding='utf8'));md={(r['variant'],r['month']):r['return_'] for r in mm}
for month in sorted({r['month'] for r in mm}):lines.append('|'+month+'|'+'|'.join(f'{md[k,month]:+.2%}' for k in keys)+'|')
lines += ['', '月收益复利衔接，不能直接相加；2026年10月不是完整自然月。', '',
'## 亏损归因与对照','']
for name,s in zip(labels,ss):
 worst=s['worst_day'];lines += [f"### {name}", '',f"最大回撤{s['maxdd']:.4%}，{s['dd_window'][0]}至{s['dd_window'][1]}；最差日{worst['date']}收益{worst['ret']:+.4%}、减少{-worst['pnl']:,.2f}元。", '', '|累计最差标的代码|含双边费用净交易现金流|','|---|---:|']
 for row in loss[s['variant']]['stock_cashflow'][:5]:lines.append(f"|{row['code']}|{row['net_trade_cashflow']:,.2f}元|")
 lines += ['', '期末无持股，上表是按证券全区间汇总的买卖现金流，已含最终卖出时实现的复权再投资效果；不是每次买卖回合，也不是未复权真实股息账本。最差五日逐仓价值变化和当日买卖现金流的加总已核，明细见loss_attribution.json。','']
a,b,c,d=ss
lines += [
f"对照差额：60%组快速减仓令期末资产比原规则少{a['final']-c['final']:,.2f}元，多{c['trades']-a['trades']}笔交易，多费用{c['fees']-a['fees']:.2f}元；80%组少{b['final']-d['final']:,.2f}元，多{d['trades']-b['trades']}笔交易，多费用{d['fees']-b['fees']:.2f}元。费用差只能解释小部分损失差，不能把失败全部归因手续费。",
'', '在原规则下提高牛市预算，本窗口少亏约1699.38元，但回撤由12.34%扩大到15.55%；快速减仓下提高预算反而再少约1615.14元。这个结果否定“只要更快减仓就能配合加仓提高收益”的本次实现；不证明所有更快退出/再入场方法都无效。日内与隔夜归因、行业与选择风险还不能从单个汇总收益数建立因果结论。','',
'## 执行与账本核验','',
'本轮执行层冻结副本为1.0.3，现有94项测试全部通过。四组共1728条每日净值、377笔买卖；每日NAV=现金+持仓市值，收益和回撤独立复算与展示统计一致。现金按逐笔买卖及费用复算，最大差小于0.02元，保留为展示精度差；期末全部现金。各组9条adjust不计买卖笔数。费用和滑点已经体现在账户路径中，不再二次扣除。原始数据及冻结源码运行前后SHA256一致。','',
'数据模型限制没有因测试通过而消失：历史ST字段全0实际为未知；换手字段缺失；退市覆盖不完整；日线无法恢复开盘排队与真实冲击。公司行为沿用qfq_reinvest隐含再投资及infer_missing_actions近似，不是真实分红现金/股数事件表；无利息现金保持0%。订单待处理默认20会话期限，目标簿路径的单独orders表为空，信号事件和实际成交已保存，但不能称订单生命周期已全核。','',
'股票历史计数筛选代码使用230个有效观察/约260行窗口，与文本“上市250日”的表述并非完全同义；本轮固定原代码作同口径比较，没有悄悄修规则。报告只能称本地模型研究结果，不称无偏全市场或平台可执行收益。','',
'## 当前决策','',
'本窗口不支持将80%+快速减仓替换60%原规则。四组观察到的回撤虽低于用户30%容忍度，但亏损且均落后全现金；“回撤没到30%”不是加仓或晋级理由。这是已见2025—2026行情的样本内检查，不是全新样本外。','',
'下一步先完成10月20日行情及状态补齐、核实初始建仓约定与执行近似；如继续研究更快再入场，应另立规则和试验号，在不改选股/费用的对照下验证，不在本轮看到亏损后回头挑参数。2015压力测试和2018长期结果不能由本段结果替代。','',
'## 文件与复现','',
'- reports/recent2025-20261020/：每组完整JSON、逐日净值、成交、持仓、事件、独立审计、月度、亏损归因及协议。','- work/recent_window_20261020/：冻结代码、协议、运行日志、缓存；没有覆盖外部DeepSeek目录。','- work/run_recent_2x2.py、audit_recent_2x2.py、report_recent_2x2.py：本轮运行、审计与报告代码。复跑需本机相同原始行情路径及协议所列数据指纹；公开交付包不含原始海量行情和缓存。','- 本图是本地运行结果图，明确不是SuperMind界面截图。', '',
'运行时间约34秒（第一组包括面板缓存建立）；不含阅读、94项测试、审计与报告时间。请求末日2026-10-20尚缺数据，不把10月19日结果标成20日完成。']
(D/'202501至202610_四组回测与亏损复盘_20261020.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
items=list(P.glob('*'))+[D/'202501至202610_四组回测与亏损复盘_20261020.md',D/'本地回测_202501至20261019_四组对照.png',ROOT/'work/run_recent_2x2.py',ROOT/'work/audit_recent_2x2.py',ROOT/'work/report_recent_2x2.py',W/'PROTOCOL.md',W/'run.log']
items += [x for x in (W/'frozen').rglob('*.py') if '__pycache__' not in x.parts]
manifest=[dict(path=str(x.relative_to(ROOT)).replace('\\','/'),bytes=x.stat().st_size,sha256=hashlib.sha256(x.read_bytes()).hexdigest()) for x in items if x.is_file()]
with zipfile.ZipFile(D/'202501至202610_四组回测数据与代码_20261020.zip','w',zipfile.ZIP_DEFLATED) as z:
 for x in items:
  if x.is_file():z.write(x,str(x.relative_to(ROOT)))
 z.writestr('BUNDLE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
print('report, figure, and evidence bundle written')
