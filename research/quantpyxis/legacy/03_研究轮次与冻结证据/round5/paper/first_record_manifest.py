# -*- coding: utf-8 -*-
"""Build the run manifest for the first forward records (2026-10-19 state check, 2026-10-20 pending)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

WS = Path(__file__).resolve().parents[3]
R5 = WS / "winrate_backtest_20261020" / "round5"
RUNS = R5 / "paper" / "runs"
TZ = timezone(timedelta(hours=8))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    files = []
    for pat in ("2026-10-19/*.json", "2026-10-20/**/*.json", "accounts/*.json",
                "validation/*.json", "freeze_check.json"):
        for p in sorted(RUNS.glob(pat)):
            files.append({"path": str(p.relative_to(RUNS)), "bytes": p.stat().st_size,
                          "sha256": sha(p)})
    manifest = {
        "schema": "round5-first-record-1",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "freeze_id": json.loads((R5 / "SEAL" / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))["freeze_id"],
        "data_cutoff": "2026-10-19",
        "primary_account": "cash_100k_new",
        "shadow_account": "inherit_history_274560.69",
        "first_records": {
            "2026-10-19_state_check": "all pass: 4 cutoffs prefix match + direct-vs-truncated 0 mismatch",
            "2026-10-20_monthly_signal": "PENDING_DATA (2026-10-20 close not ingested); no signal, no fill",
            "2026-10-20_daily_audit": "PENDING_DATA",
        },
        "files": files,
        "note": "No paper fill exists yet. The first signal can only be generated after 2026-10-20 (or later "
                "trading day) close data is ingested; generation time must be after freeze_at and before the "
                "next open; otherwise PENDING_DATA and defer.",
    }
    out = RUNS / "FIRST_RECORD_MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", out, "files", len(files))


if __name__ == "__main__":
    main()
