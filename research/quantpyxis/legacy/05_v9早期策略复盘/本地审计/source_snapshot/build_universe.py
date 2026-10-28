import re, json, glob, os

def parse_constituent(path):
    out = []
    for line in open(path, encoding='utf-8', errors='ignore'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) < 2:
            continue
        code = cells[0]
        if not re.fullmatch(r'(sh|sz)\d{6}', code):
            continue
        out.append((code, cells[1]))
    return out

rows = []
for p in sorted(glob.glob('raw/idx_*.txt')):
    idx = os.path.basename(p).replace('idx_', '').replace('.txt', '')
    for code, name in parse_constituent(p):
        rows.append((code, name, idx))

uniq = {}
for code, name, idx in rows:
    if code not in uniq:
        uniq[code] = {'code': code, 'name': name, 'indices': []}
    uniq[code]['indices'].append(idx)

def board(code):
    n = code[2:]
    if code.startswith('sh'):
        if n.startswith('600') or n.startswith('601') or n.startswith('603') or n.startswith('605'):
            return '主板'
        if n.startswith('688') or n.startswith('689'):
            return '科创板'
        return '其他沪'
    else:
        if n.startswith('000') or n.startswith('001') or n.startswith('002') or n.startswith('003'):
            return '主板'
        if n.startswith('300') or n.startswith('301'):
            return '创业板'
        return '其他深'

keep = []
drop = {}
for c in uniq.values():
    b = board(c['code'])
    c['board'] = b
    if b in ('主板', '创业板'):
        keep.append(c)
    else:
        drop[b] = drop.get(b, 0) + 1

keep.sort(key=lambda x: x['code'])
json.dump(keep, open('universe.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('unique from indices:', len(uniq))
print('kept 主板+创业板:', len(keep))
print('dropped:', drop)
from collections import Counter
print(Counter([k['board'] for k in keep]))
