from pathlib import Path
import shutil,hashlib,json
ROOT=Path(__file__).resolve().parents[1]
EXT=Path('C:<LOCAL_HOME>/OneDrive/文档/deepseek-harness/default-workspace')
OUT=ROOT/'work/recent_window_20261020';OUT.mkdir(exist_ok=False)
F=OUT/'frozen';S=EXT/'run2/engine_fix/engine'
shutil.copytree(S,F/'run2/engine_fix/engine',ignore=shutil.ignore_patterns('__pycache__','_tmp','cache','outputs','out'))
for rel in ['run2/os/strategy_v46.py','qrun_1416/os/strategy_final.py','qrun_1416/os/strategy_v1.py','qrun_1416/os/out/regime_v1.csv']:
 p=F/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(EXT/rel,p)
src=[]
for p in sorted(F.rglob('*')):
 if p.is_file():src.append(dict(file=str(p.relative_to(F)),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size))
data=EXT/'qrun_1416/data';files=[data/'meta'/n for n in ['manifest.json','calendar.csv','universe.csv','codes.csv']]
files += [data/'panel'/f'{stem}_{y}.csv.gz' for y in [2023,2024,2025,2026] for stem in ['bars','bars_qfq']]
files += [data/'panel/index_daily.csv.gz']
df=[]
for p in files:
 if p.is_file():df.append(dict(file=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size))
config=dict(experiment='RECENT-20250101-20261020-2X2',requested_window=['2025-01-01','2026-10-20'],data_end='2026-10-19',actual_end='2026-10-19',initial_cash=100000,initialize='fresh cash, no inherited holdings',warmup_days=400,cash_rate=0,rebalance='Q',bull_budgets=[.6,.8],neutral_budget=.25,neutral_trim=[False,True],trim_band=.03,fast_reentry=False,slippage_bps=10,commission_bps=2.5,min_commission=5,max_tolerated_drawdown=.30,drawdown_definition='1 - NAV / prior running peak; initial capital included',purpose='predeclared comparison; no parameter search',new_platform_run=False,source_snapshot=src,data_files_hashed=df)
(OUT/'PROTOCOL.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf8')
(OUT/'PROTOCOL.md').write_text('''# 本轮运行前协议

用户恢复并要求2025-01至2026-10-20回测，覆盖此前暂停/仅设计分工。本轮自行运行本地冻结副本，不覆盖DeepSeek项目，不冒充SuperMind平台。
现有原始行情及状态截止2026-10-19，不制造10月20日行情；请求日与实际日分别记录。
10万元从现金新起步，不截取2018连续账户。400会话预热，状态文件沿用此前完整历史计算。
四组为60/25和80/25，各开/关现有中性快速减仓，评分、选股、费用、季度复核一致；不加现金利息，不发明更快再入场，也不混用历史80/30结果。快速减仓段内一次、超目标3个百分点才触发，下一开盘执行。
30%是用户确认容忍上限，作为风险审查约束；不在看到结果后修改参数或将其改造成保证30%止损。本轮规则未新增账户保险丝。
代码指纹、数据文件指纹和参数见PROTOCOL.json；测试、所有失败、净值、交易、亏损归因都保存。
''',encoding='utf8')
print(OUT)
