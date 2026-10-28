import json, subprocess, time, os, sys

W = 'C:<LOCAL_HOME>/.local/bin/westock.exe'
u = json.load(open('universe.json', encoding='utf-8'))
codes = [x['code'] for x in u]
B = 20
os.makedirs('raw/bars', exist_ok=True)
total = (len(codes) + B - 1) // B
fails = []
t0 = time.time()
for i in range(total):
    out = 'raw/bars/b%03d.txt' % i
    if os.path.exists(out) and os.path.getsize(out) > 2000:
        continue
    batch = codes[i*B:(i+1)*B]
    r = subprocess.run([W, 'kline', ','.join(batch), '--start', '2025-01-01',
                        '--end', '2026-09-30', '--fq', 'qfq'],
                       capture_output=True, text=True, encoding='utf-8', errors='ignore')
    open(out, 'w', encoding='utf-8').write(r.stdout)
    if '成功: %d' % len(batch) not in r.stdout:
        fails.append((i, r.stdout[:200]))
    print('batch %d/%d %.0fs' % (i+1, total, time.time()-t0), flush=True)
print('done, fails:', len(fails))
for f in fails[:5]:
    print(f)
