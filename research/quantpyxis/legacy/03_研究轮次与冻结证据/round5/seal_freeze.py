# -*- coding: utf-8 -*-
"""P0: seal the frozen strategy A and a read-only snapshot of strategy/engine/regime/data.

Writes round5/SEAL/:
  snapshot/            byte copies of the frozen code + regime CSV + data manifest
  A_CONFIG.json        machine-readable frozen strategy configuration
  FREEZE_MANIFEST.json freeze id/time, data cutoff, versions, per-file sha256, behaviour notes
  SHA256SUMS.txt
  README_FREEZE.md
and marks every snapshot file read-only.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
R5 = WS / "winrate_backtest_20261020" / "round5"
SEAL = R5 / "SEAL"
SNAP = SEAL / "snapshot"
REPO = WS / "qrun_1416"
R4 = WS / "winrate_backtest_20261020" / "round4"
TZ = timezone(timedelta(hours=8))

COPY = [
    (R4 / "variants.py", "strategy/variants.py"),
    (R4 / "run_phaseB.py", "strategy/run_phaseB.py"),
    (R4 / "overlays.py", "strategy/overlays.py"),
    (REPO / "engine" / "__init__.py", "engine/__init__.py"),
    (REPO / "engine" / "api.py", "engine/api.py"),
    (REPO / "engine" / "core.py", "engine/core.py"),
    (REPO / "engine" / "data.py", "engine/data.py"),
    (REPO / "engine" / "metrics.py", "engine/metrics.py"),
    (REPO / "engine" / "regime.py", "engine/regime.py"),
    (REPO / "os" / "regime_v1.py", "regime/regime_v1.py"),
    (REPO / "os" / "out" / "regime_v1.csv", "regime/regime_v1.csv"),
    (REPO / "data" / "meta" / "manifest.json", "data/manifest.json"),
    (REPO / "data" / "meta" / "universe.csv", "data/universe.csv"),
    (REPO / "data" / "meta" / "calendar.csv", "data/calendar.csv"),
    (WS / "winrate_backtest_20261020" / "SuperMind_S0_月度基线_胜率诊断_v03.py",
     "platform_source/SuperMind_S0_月度基线_胜率诊断_v03.py"),
    (WS / "winrate_backtest_20261020" / "SuperMind_S1_整手候选补位_待验证_v03.py",
     "platform_source/SuperMind_S1_整手候选补位_待验证_v03.py"),
]

A_CONFIG = {
    "schema_version": "round5-a-freeze-1",
    "strategy_id": "A",
    "description": "frozen-state monthly momentum, B=100%/N=30%, top15, buffer20, vol cap 0.55, "
                   "truncate-then-drop affordability, explicit full exits",
    "engine": {
        "repo": "qrun_1416/engine v1.0.1",
        "exec_timing": "T close signal -> T+1 open fill",
        "lots": 100,
        "leverage": 0,
        "cash_interest": 0,
        "max_pending_days": 20,
        "dividend_mode": "qfq_reinvest (share multiplier on ex-date)",
        "volume_cap": None,
        "st_filter": "INERT (panel isst always 0) - documented known gap",
    },
    "state": {
        "source_csv": "qrun_1416/os/out/regime_v1.csv",
        "generator": "qrun_1416/os/regime_v1.py compute_regime(start='2012-08-01', end=asof)",
        "label_semantics": "state evaluated at T close, actionable T+1 open",
        "mapping": {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"},
        "missing_state_behaviour": "FAIL with DATA_UNAVAILABLE; MUST NOT fall back to N and MUST NOT reuse stale state",
    },
    "exposure": {"B": 1.00, "N": 0.30, "E": 0.0, "C": 0.0},
    "selection": {
        "universe_prefixes": ["600", "601", "603", "605", "000", "001", "002", "003", "300", "301"],
        "window_bars": 260,
        "min_valid_bars": 230,
        "momentum_12_1": "x[-22]/x[-253]-1",
        "momentum_6_1": "x[-22]/x[-127]-1",
        "vol": "std(pct_change() over the last 61 return observations)*sqrt(252)  [code: iloc[-61:], 61 obs; NOT exactly 60]",
        "vol_cap": 0.55,
        "min_raw_close": 2.0,
        "min_amount_60d_mean": 200000000.0,
        "trend": "close > MA60 > MA120 (qfq, ffill)",
        "score": {"w_ras": 0.5, "w_m6": 0.3, "w_inv_vol": 0.2, "rank": "cross-sectional pct rank"},
        "top_n": 15,
        "retention_buffer_rank": 20,
        "affordability": "truncate to top_n first, then drop names with 100*raw_close > budget*1.02; "
                         "budget = exposure*nav/top_n; NO backfill",
        "weights": "exposure / n_selected",
        "rebalance": "last trading day of month; first frozen date initialises; B<->N changes do NOT "
                     "trigger an extra immediate rebalance; E/C exit target is issued at that close and "
                     "attempted next open",
    },
    "costs": {
        "slippage_bps_per_side": 10.0,
        "commission_bps": 2.5,
        "min_commission_cny": 5.0,
        "stamp_tax_sell": {"before": "2023-08-28: 0.001", "from_2023_08_28": "0.0005"},
        "transfer_fee": 0.00001,
    },
    "window": {"start": "2014-07-01", "end": "2026-10-19", "capital": 100000.0},
    "published_metrics": {"total_return": 1.7456069325168508,
                          "max_drawdown": -0.23979364746919452,
                          "dd_peak": "2017-11-22", "dd_trough": "2020-03-23",
                          "cagr": 0.088883,
                          "avg_exposure": 0.264221,
                          "all_cash_days": 1285,
                          "max_positions_observed": 17},
    "prohibited_changes": ["no circuit breakers", "no fast re-entry", "no sector caps",
                           "no parameter search on N=85/90 or top_n 15/20",
                           "no use of 2024-09+ performance to change rules"],
    "known_behaviour": {
        "missing_input": "FAIL with DATA_UNAVAILABLE, write nothing",
        "empty_candidates": "set empty target book (sell all), recorded as cash reason no_candidate",
        "pending_order_expiry": "20 trading days (engine max_pending_days), then target_book_expired event",
        "blocked_exit": "carried daily, retried at next open; over-target holdings possible up to expiry",
    },
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    SNAP.mkdir(parents=True, exist_ok=True)
    freeze_at = datetime.now(TZ).isoformat(timespec="seconds")
    files = []
    for src, rel in COPY:
        if not src.exists():
            print("MISSING", src)
            continue
        dst = SNAP / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files.append({"rel": rel, "bytes": dst.stat().st_size, "sha256": sha256_file(dst)})
    # config + regimes also as top-level frozen artifacts
    (SEAL / "A_CONFIG.json").write_text(json.dumps(A_CONFIG, indent=1, ensure_ascii=False),
                                        encoding="utf-8")
    files.append({"rel": "A_CONFIG.json", "bytes": (SEAL / "A_CONFIG.json").stat().st_size,
                  "sha256": sha256_file(SEAL / "A_CONFIG.json")})
    versions = {"python": sys.version.split()[0]}
    try:
        import pandas
        import numpy
        versions["pandas"] = pandas.__version__
        versions["numpy"] = numpy.__version__
    except Exception:  # noqa: BLE001
        pass
    manifest = {
        "freeze_id": "R5-A-FREEZE-" + freeze_at.replace(":", "").replace("-", ""),
        "decision_date": "2026-10-20",
        "freeze_at": freeze_at,
        "data_cutoff": "2026-10-19",
        "data_manifest_sha256": sha256_file(REPO / "data" / "meta" / "manifest.json"),
        "regime_csv_sha256": sha256_file(REPO / "os" / "out" / "regime_v1.csv"),
        "strategy": "A",
        "config_file": "A_CONFIG.json",
        "versions": versions,
        "files": files,
        "read_only": True,
        "notes": [
            "Snapshot is byte-identical copy of the code/data inputs used by round-4 candidate A.",
            "regime_v1.csv is a historical state sample, NOT a continuation algorithm; forward runs must "
            "recompute the state from the generator.",
            "The platform reproduction of A is NOT done; platform R2 reproduction (round 3) is FAIL.",
        ],
    }
    (SEAL / "FREEZE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    lines = [f"{f['sha256']}  {f['rel']}" for f in files]
    lines.append(f"{sha256_file(SEAL / 'FREEZE_MANIFEST.json')}  FREEZE_MANIFEST.json")
    (SEAL / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (SEAL / "README_FREEZE.md").write_text(f"""# 候选 A 冻结快照（read-only）

- freeze_id: `{manifest['freeze_id']}`
- 决策日期: 2026-10-20
- 实际封存时间: `{freeze_at}` (Asia/Shanghai)
- 数据截止: 2026-10-19（本地面板/状态 CSV 的最后完整交易日；2026-10-20 数据尚未入库）
- 账户: 100,000 元（新现金影子账户；历史 274,560.69 元只作为继承持仓影子账户的参考，不作为新本金）

## 内容
`A_CONFIG.json` 是唯一机器可读配置；`snapshot/` 是策略/引擎/状态生成器/数据 manifest/平台源码的逐字节副本；
`FREEZE_MANIFEST.json` 记录每个文件 sha256 与版本；`SHA256SUMS.txt` 供 `sha256sum -c` 校验。

## 边界（冻结时即承认）
- 平台复现未完成：平台的 R2→S0 复现是 FAIL（第三轮），因此 A 的平台一致性未验证。
- A 的选择参考了 2024-09 以后的第一眼结果（F6），不是纯训练期选出的配置；冻结后只收集前向结果。
- 未建模 ST 过滤、平台 10% 成交量上限；`amount` 是流动性代理。
- 状态 CSV 不能替代续算；前向必须调用 `regime/regime_v1.py compute_regime(end=asof)` 重算，缺失即失败。
- 2026-10-19 恰为 A 的回撤谷附近；冻结不代表未来回撤上限。
""", encoding="utf-8")
    # mark snapshot + key artifacts read-only
    for p in list(SNAP.rglob("*")):
        if p.is_file():
            p.chmod(p.stat().st_mode & ~stat.S_IWRITE)
    for name in ("A_CONFIG.json", "FREEZE_MANIFEST.json", "SHA256SUMS.txt", "README_FREEZE.md"):
        p = SEAL / name
        p.chmod(p.stat().st_mode & ~stat.S_IWRITE)
    print("sealed", manifest["freeze_id"], "files", len(files))
    print("freeze_at", freeze_at)


if __name__ == "__main__":
    main()
