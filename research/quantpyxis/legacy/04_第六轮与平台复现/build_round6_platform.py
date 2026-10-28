from pathlib import Path
import ast,json,hashlib
root=Path(__file__).resolve().parents[1]
out=root/'outputs/round6_20261021'
s=(root/'outputs/SuperMind_S0_月度基线_胜率诊断_v03.py').read_text(encoding='utf8')
changes={"VARIANT='R2'":"VARIANT='A'\nBULL_BUDGET=1.0\nTOP_N=15",
"exposure=.60 if state=='B' else .25 if state=='N' else 0.":"exposure=BULL_BUDGET if state=='B' else .30 if state=='N' else 0.",
"key=lambda s:rank[s])[:10]":"key=lambda s:rank[s])[:TOP_N]",
"if len(selected)>=10:break":"if len(selected)>=TOP_N:break",
"exposure*nav/10*1.02":"exposure*nav/TOP_N*1.02"}
for old,new in changes.items():
    assert s.count(old)==1,old
    s=s.replace(old,new)
s='# 20261021 A platform-native bridge; different data/execution from local A; research only.\n'+s
hashes={}
for tag,source in [('A',s),('A80',s.replace("VARIANT='A'","VARIANT='A80'").replace('BULL_BUDGET=1.0','BULL_BUDGET=.8'))]:
    ast.parse(source,feature_version=(3,8))
    p=out/(tag+'_platform.py');p.write_text(source,encoding='utf8')
    hashes[tag]=hashlib.sha256(p.read_bytes()).hexdigest()
assert s.count('rank.get(s,10**9)<20')==1
(out/'platform_sources.json').write_text(json.dumps(hashes,indent=2),encoding='utf8')
print('PASS: Python3.8 syntax, original buffer20; A/A80 differ only tag and BULL budget')
