# -*- coding: utf-8 -*-
"""Launcher: spawn independent python processes for Tencent raw bar fetch (sharded).

Usage: python launch_tx.py [n_shards] [n_threads]
Sandbox blocks multiprocessing/named pipes; uses subprocess.Popen with stdout redirected to files.
"""
import datetime as dt
import os
import subprocess
import sys
import time

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
PY = sys.executable
WORKER = os.path.join(ROOT, "data", "scripts", "tx_worker.py")
LOGS = os.path.join(ROOT, "data", "tmp", "logs")


def main():
    n_shards = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    n_threads = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    os.makedirs(LOGS, exist_ok=True)
    procs = []
    t0 = time.time()
    for i in range(n_shards):
        log = os.path.join(LOGS, "tx_worker_%02d.log" % i)
        f = open(log, "w", encoding="utf-8")
        p = subprocess.Popen([PY, WORKER, str(i), str(n_shards), str(n_threads)],
                             stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
        procs.append((i, p, f, log))
        print("started shard %d pid=%d log=%s" % (i, p.pid, log), flush=True)
        time.sleep(0.4)
    print("all %d shards started; waiting..." % n_shards, flush=True)
    rc = []
    while any(p.poll() is None for _, p, _, _ in procs):
        time.sleep(10)
        done = sum(1 for _, p, _, _ in procs if p.poll() is not None)
        print("poll t=%.0fs done_shards=%d/%d" % (time.time() - t0, done, n_shards), flush=True)
    for i, p, f, log in procs:
        f.close()
        rc.append(p.returncode)
        print("shard %d exited rc=%s log=%s" % (i, p.returncode, log), flush=True)
    print("ALL DONE t=%.0fs rcs=%s" % (time.time() - t0, rc), flush=True)


if __name__ == "__main__":
    main()
