# -*- coding: utf-8 -*-
"""Launcher for baostock adjust-factor fetch (independent processes, file-redirected)."""
import os
import subprocess
import sys
import time

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
PY = sys.executable
WORKER = os.path.join(ROOT, "data", "scripts", "factor_worker.py")
LOGS = os.path.join(ROOT, "data", "tmp", "logs")


def main():
    n_shards = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    os.makedirs(LOGS, exist_ok=True)
    procs = []
    t0 = time.time()
    for i in range(n_shards):
        log = os.path.join(LOGS, "factor_worker_%02d.log" % i)
        f = open(log, "w", encoding="utf-8")
        p = subprocess.Popen([PY, WORKER, str(i), str(n_shards)],
                             stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
        procs.append((i, p, f, log))
        print("started factor shard %d pid=%d log=%s" % (i, p.pid, log), flush=True)
        time.sleep(1.0)
    while any(p.poll() is None for _, p, _, _ in procs):
        time.sleep(15)
        done = sum(1 for _, p, _, _ in procs if p.poll() is not None)
        print("factor poll t=%.0fs done=%d/%d" % (time.time() - t0, done, n_shards), flush=True)
    for i, p, f, log in procs:
        f.close()
        print("factor shard %d rc=%s log=%s" % (i, p.returncode, log), flush=True)
    print("FACTOR ALL DONE t=%.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
