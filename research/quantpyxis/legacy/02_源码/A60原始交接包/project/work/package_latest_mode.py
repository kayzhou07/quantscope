from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
out = root / 'deliverables'
doc = out / '最新投资模式_20261020.md'
target = out / '最新投资模式_交接包_20261020.zip'
files = {'README.md': doc}
for rel in ['config/risk_preference.json',
            'deliverables/最新投资模式_20261020.md',
            'deliverables/202501至202610_四组回测与亏损复盘_20261020.md',
            'deliverables/本地回测_202501至20261019_四组对照.png',
            'work/prepare_recent_run.py', 'work/run_recent_2x2.py',
            'work/audit_recent_2x2.py', 'work/report_recent_2x2.py',
            'work/package_latest_mode.py']:
    p = root / rel
    assert p.is_file(), p
    files['project/' + rel] = p
for folder in ['reports/recent2025-20261020', 'work/recent_window_20261020/frozen']:
    for p in sorted((root / folder).rglob('*')):
        if p.is_file() and not any(x in p.parts for x in ['__pycache__', '.pytest_cache', 'cache']) and p.suffix != '.pyc':
            files['project/' + p.relative_to(root).as_posix()] = p
for name in ['PROTOCOL.md', 'PROTOCOL.json', 'run.log']:
    p = root / 'work/recent_window_20261020' / name
    if p.is_file():
        files['project/' + p.relative_to(root).as_posix()] = p
manifest = {name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                   'bytes': p.stat().st_size} for name, p in files.items()}
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
    for name, p in files.items():
        z.write(p, name)
    z.writestr('MANIFEST_SHA256.json', json.dumps(manifest, ensure_ascii=False, indent=2))
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
    for name, entry in manifest.items():
        assert hashlib.sha256(z.read(name)).hexdigest() == entry['sha256']
    assert any(name.endswith('regime_v1.csv') for name in z.namelist())
    assert any('/tests/' in name for name in z.namelist())
audit = json.loads((root / 'reports/recent2025-20261020/audit_summary.json').read_text(encoding='utf-8'))
text = doc.read_text(encoding='utf-8')
for row in audit:
    assert f"{row['final']:,.2f}" in text
    assert f"{abs(row['return_'])*100:.2f}%" in text
    assert f"{row['maxdd']*100:.2f}%" in text
assert json.loads((root / 'config/risk_preference.json').read_text(encoding='utf-8'))['confirmed_portfolio_drawdown_limit'] == .3
print(json.dumps({'document': str(doc), 'package': str(target),
                  'files': len(files), 'zip_bytes': target.stat().st_size,
                  'manifest_verified': True, 'results_checked': 4}, ensure_ascii=False))
