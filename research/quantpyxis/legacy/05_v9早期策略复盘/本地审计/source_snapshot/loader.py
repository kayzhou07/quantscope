"""Load raw westock kline tables into a panel. No look-ahead beyond window end."""
import glob, json, os, collections

def parse_rows(path, has_code):
    out = []
    for line in open(path, encoding='utf-8', errors='ignore'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if has_code:
            if not (cells[0].startswith('sh') or cells[0].startswith('sz')):
                continue
            code, date = cells[0], cells[1]
            rest = cells[2:]
        else:
            date = cells[0]
            code = None
            rest = cells[1:]
        if len(rest) < 6:
            continue
        try:
            o, c, h, l, v, a = (float(rest[0]), float(rest[1]), float(rest[2]),
                                float(rest[3]), float(rest[4]), float(rest[5]))
        except ValueError:
            continue
        out.append((code, date, o, c, h, l, v, a))
    return out

def load_all():
    panel = collections.defaultdict(list)   # code -> [(date,o,c,h,l,v,a)]
    for p in sorted(glob.glob('raw/bars/b*.txt')) + sorted(glob.glob('raw/bars2/r*.txt')):
        for code, date, o, c, h, l, v, a in parse_rows(p, True):
            panel[code].append((date, o, c, h, l, v, a))
    for c in panel:
        panel[c].sort(key=lambda x: x[0])
        # dedupe identical dates (retry files may overlap)
        seen, uniq = set(), []
        for r in panel[c]:
            if r[0] in seen:
                continue
            seen.add(r[0]); uniq.append(r)
        panel[c] = uniq
    idx = collections.defaultdict(list)
    for code, date, o, c, h, l, v, a in parse_rows('raw/index_bars.txt', True):
        idx[code].append((date, o, c, h, l, v, a))
    for c in idx:
        idx[c].sort(key=lambda x: x[0])
    return panel, idx

if __name__ == '__main__':
    panel, idx = load_all()
    print('stocks', len(panel))
    n = [len(v) for v in panel.values()]
    print('bars per stock min/median/max', min(n), sorted(n)[len(n)//2], max(n))
    print('index codes', {k: len(v) for k, v in idx.items()})
    print('sample first/last', panel['sh600012'][0][0], panel['sh600012'][-1][0])
