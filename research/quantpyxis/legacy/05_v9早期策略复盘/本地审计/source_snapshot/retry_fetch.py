import json, subprocess, time, os, glob, re

W = 'C:<LOCAL_HOME>/.local/bin/westock.exe'
u = json.load(open('universe.json', encoding='utf-8'))
codes = [x['code'] for x in u]

have = set()
for p in glob.glob('raw/bars/b*.txt'):
    for line in open(p, encoding='utf-8', errors='ignore'):
        m = re.match(r'\|\s*(sh|sz)\d{6}\s*\|', line)
        if m:
            have.add(line.split('|')[1].strip())

missing = [c for c in codes if c not in have]
print('have %d / %d, missing %d' % (len(have), len(codes), len(missing)))

os.makedirs('raw/bars2', exist_ok=True)
t0 = time.time()
for i in range(0, len(missing), 8):
    batch = missing[i:i+8]
    out = 'raw/bars2/r%03d.txt' % (i // 8)
    if os.path.exists(out) and os.path.getsize(out) > 1500:
        continue
    for attempt in range(3):
        r = subprocess.run([W, 'kline', ','.join(batch), '--start', '2025-01-01',
                            '--end', '2026-09-30', '--fq', 'qfq'],
                           capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if 'RATE_LIMITED' not in r.stdout:
            break
        time.sleep(2.0)
    open(out, 'w', encoding='utf-8').write(r.stdout)
    print('%d/%d %.0fs status=%s' % (i + len(batch), len(missing), time.time() - t0,
          'ok' if 'RATE' not in r.stdout else 'limited'), flush=True)
    time.sleep(0.6)
print('done')
