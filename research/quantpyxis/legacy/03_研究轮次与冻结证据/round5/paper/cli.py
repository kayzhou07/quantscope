# -*- coding: utf-8 -*-
"""Round-5 paper-trading CLI.

Commands
--------
  freeze-check                     verify the SEAL SHA256SUMS (read-only snapshot)
  state-check --asof D --cutoffs ...   recompute regime at cutoffs vs published prefix
  monthly-signal --asof D [--initial]  month-end (or initial) signal from frozen rules A
  daily-audit --asof D             daily close audit of the paper account
  paper-advance --to D             process pending orders / mark NAV day by day
  monthly-report --month YYYY-MM   aggregate the paper ledger
  validate                         run the P1 validation suite

Every command writes a JSON artifact under round5/paper/runs/<asof>/ with generated_at,
freeze_id, data cutoff and code/data hashes.  No command ever back-fills a signal or a fill for
a date whose data was not available at generation time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, ledger, panel, state, strategy, util, validate


def _run_dir(asof: str) -> Path:
    p = config.RUNS / asof
    p.mkdir(parents=True, exist_ok=True)
    return p


def _emit(path: Path, obj: dict) -> dict:
    obj = dict(obj)
    obj.setdefault("schema_generated_at", util.now_iso())
    obj["freeze_id"] = config.FREEZE_ID
    obj["freeze_at"] = config.FREEZE_AT
    obj["data_cutoff"] = config.DATA_CUTOFF
    digest = util.atomic_write_json(path, obj)
    out = dict(obj)
    out["_written"] = str(path)
    out["_sha256"] = digest
    return out


def _account_file(account: str) -> Path:
    d = config.RUNS / "accounts"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{account}.json"


def _existing_holdings(account: str) -> dict:
    p = _account_file(account)
    if p.exists():
        d = util.read_json(p)
        return {c: pos["shares"] for c, pos in d.get("positions", {}).items()}
    return {}


def _existing_nav(account: str, default: float) -> float:
    p = _account_file(account)
    if p.exists():
        d = util.read_json(p)
        if d.get("nav_history"):
            return float(d["nav_history"][-1]["nav"])
        return float(d.get("cash", default))
    return float(default)


# ------------------------------------------------------------------ commands
def cmd_freeze_check(args) -> int:
    seal = config.SEAL
    sums = seal / "SHA256SUMS.txt"
    bad, checked = [], 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        p = seal / rel
        checked += 1
        if not p.exists() or util.sha256_file(p) != digest:
            bad.append(rel)
    out = {"command": "freeze-check", "checked": checked, "mismatch": bad,
           "pass": len(bad) == 0, "freeze_id": config.FREEZE_ID}
    print(json.dumps(_emit(config.RUNS / "freeze_check.json", out), indent=1, ensure_ascii=False))
    return 0 if not bad else 1


def cmd_state_check(args) -> int:
    cutoffs = [c.strip() for c in args.cutoffs.split(",") if c.strip()]
    res = state.check(cutoffs)
    res.update({"command": "state-check", "asof": args.asof})
    out = _emit(_run_dir(args.asof) / "state_check.json", res)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if res.get("all_pass") else 2


def cmd_monthly_signal(args) -> int:
    asof = args.asof
    last = panel.last_available_date()
    days = panel.trading_days()
    rdir = _run_dir(asof)
    sdir = rdir / "signals"
    sdir.mkdir(parents=True, exist_ok=True)
    base = {"command": "monthly-signal", "asof": asof, "kind": args.kind,
            "account": args.account, "last_available_bar": last,
            "is_trading_day": asof in days,
            "is_month_end": (asof in days and strategy.is_month_end(asof))}
    if asof not in days:
        base.update(status="NOT_TRADING_DAY", no_signal_created=True,
                    reason=f"{asof} is not a trading day in the frozen calendar")
        print(json.dumps(_emit(sdir / f"PENDING_{asof}.json", base), indent=1, ensure_ascii=False))
        return 0
    if asof > last or not panel.has_bar(asof):
        base.update(status="PENDING_DATA", no_signal_created=True,
                    reason=f"close data for {asof} not ingested (panel last bar {last}); "
                           f"no signal is created and no fill is back-filled")
        print(json.dumps(_emit(sdir / f"PENDING_{asof}.json", base), indent=1, ensure_ascii=False))
        return 0
    if args.kind != "initial" and not strategy.is_month_end(asof):
        base.update(status="NOT_MONTH_END", no_signal_created=True,
                    reason="frozen rules only rebalance on the last trading day of the month")
        print(json.dumps(_emit(sdir / f"SKIP_{asof}.json", base), indent=1, ensure_ascii=False))
        return 0
    nav = args.nav if args.nav is not None else _existing_nav(args.account, 100000.0)
    holdings = _existing_holdings(args.account)
    try:
        sig = strategy.build_signal(asof, args.kind, nav, holdings, args.account)
    except panel.DataUnavailable as exc:
        base.update(status="DATA_UNAVAILABLE", no_signal_created=True, reason=str(exc))
        print(json.dumps(_emit(sdir / f"PENDING_{asof}.json", base), indent=1, ensure_ascii=False))
        return 2
    path = sdir / f"signal_{asof}.json"
    if path.exists():
        old = util.read_json(path)
        if old.get("signal_id") == sig["signal_id"]:
            base.update(status="ALREADY_EXISTS_IDENTICAL", signal_id=sig["signal_id"],
                        no_signal_created=True)
            print(json.dumps(_emit(sdir / f"IDEMPOTENT_{asof}.json", base),
                             indent=1, ensure_ascii=False))
            return 0
        base.update(status="CONFLICT_EXISTING_SIGNAL_DIFFERS", signal_id=sig["signal_id"],
                    existing_signal_id=old.get("signal_id"), no_signal_created=True)
        print(json.dumps(_emit(sdir / f"CONFLICT_{asof}.json", base),
                         indent=1, ensure_ascii=False))
        return 3
    out = _emit(path, sig)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def cmd_daily_audit(args) -> int:
    asof = args.asof
    rdir = _run_dir(asof)
    last = panel.last_available_date()
    if asof > last or not panel.has_bar(asof):
        out = {"command": "daily-audit", "asof": asof, "status": "PENDING_DATA",
               "reason": f"close data for {asof} not ingested (panel last bar {last})",
               "account": args.account}
        print(json.dumps(_emit(rdir / "daily_audit.json", out), indent=1, ensure_ascii=False))
        return 0
    p = _account_file(args.account)
    if not p.exists():
        out = {"command": "daily-audit", "asof": asof, "status": "NO_ACCOUNT",
               "reason": "paper account not initialised; run monthly-signal --initial first",
               "account": args.account}
        print(json.dumps(_emit(rdir / "daily_audit.json", out), indent=1, ensure_ascii=False))
        return 0
    led = ledger.PaperLedger.load(p)
    if not led.d["nav_history"] or led.d["nav_history"][-1]["date"] < asof:
        try:
            led.advance(asof, ledger.load_signals(config.RUNS))
        except Exception as exc:  # noqa: BLE001
            out = {"command": "daily-audit", "asof": asof, "status": "ADVANCE_FAILED",
                   "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(_emit(rdir / "daily_audit.json", out), indent=1, ensure_ascii=False))
            return 2
    info = state.recompute(asof)
    audit = led.daily_audit(asof, info["letter"], config.EXPOSURE.get(info["letter"], 0.0))
    out = {"command": "daily-audit", "asof": asof, "status": "OK", "audit": audit,
           "state": info}
    print(json.dumps(_emit(rdir / "daily_audit.json", out), indent=1, ensure_ascii=False))
    return 0


def cmd_paper_advance(args) -> int:
    p = _account_file(args.account)
    if not p.exists():
        print(json.dumps({"status": "NO_ACCOUNT"}, indent=1))
        return 2
    led = ledger.PaperLedger.load(p)
    signals = ledger.load_signals(config.RUNS)
    audits = led.advance(args.to, signals)
    out = {"command": "paper-advance", "to": args.to, "account": args.account,
           "days": len(audits), "last": audits[-1] if audits else None}
    print(json.dumps(_emit(_run_dir(args.to) / "paper_advance.json", out),
                     indent=1, ensure_ascii=False))
    return 0


def cmd_monthly_report(args) -> int:
    p = _account_file(args.account)
    if not p.exists():
        print(json.dumps({"status": "NO_ACCOUNT"}, indent=1))
        return 2
    led = ledger.PaperLedger.load(p)
    month = args.month
    rows = [r for r in led.d["nav_history"] if r["date"][:7] == month]
    if not rows:
        out = {"command": "monthly-report", "month": month, "status": "NO_DATA"}
    else:
        start_nav = rows[0]["nav"]
        ret = rows[-1]["nav"] / start_nav - 1.0 if start_nav else None
        peak = rows[0]["nav"]
        mdd = 0.0
        for r in rows:
            peak = max(peak, r["nav"])
            mdd = min(mdd, r["nav"] / peak - 1.0)
        out = {"command": "monthly-report", "month": month, "status": "OK",
               "account": args.account, "days": len(rows),
               "start_nav": start_nav, "end_nav": rows[-1]["nav"], "return": ret,
               "max_drawdown": mdd,
               "avg_exposure": sum(r["exposure"] for r in rows) / len(rows),
               "fees": sum(r["fees"] for r in rows),
               "cash_reasons": _count([r.get("cash_reason") for r in rows])}
    print(json.dumps(_emit(_run_dir(month + "-report") / "monthly_report.json", out),
                     indent=1, ensure_ascii=False))
    return 0


def _count(items) -> dict:
    out: dict = {}
    for i in items:
        if i is None:
            continue
        out[str(i)] = out.get(str(i), 0) + 1
    return out


def cmd_validate(args) -> int:
    res = validate.run_all()
    out = _emit(config.RUNS / "validation" / "validation.json", res)
    print(json.dumps({"pass": res.get("pass"), "checks": res.get("checks")},
                     indent=1, ensure_ascii=False))
    return 0 if res.get("pass") else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="round-5 frozen strategy A paper infrastructure")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("freeze-check")
    p.set_defaults(func=cmd_freeze_check)

    p = sub.add_parser("state-check")
    p.add_argument("--asof", default=config.DATA_CUTOFF)
    p.add_argument("--cutoffs", default="2018-12-28,2021-06-30,2023-12-29,2026-10-19")
    p.set_defaults(func=cmd_state_check)

    p = sub.add_parser("monthly-signal")
    p.add_argument("--asof", required=True)
    p.add_argument("--kind", default="scheduled", choices=["scheduled", "initial"])
    p.add_argument("--account", default=config.PRIMARY_ACCOUNT)
    p.add_argument("--nav", type=float, default=None)
    p.set_defaults(func=cmd_monthly_signal)

    p = sub.add_parser("daily-audit")
    p.add_argument("--asof", required=True)
    p.add_argument("--account", default=config.PRIMARY_ACCOUNT)
    p.set_defaults(func=cmd_daily_audit)

    p = sub.add_parser("paper-advance")
    p.add_argument("--to", required=True)
    p.add_argument("--account", default=config.PRIMARY_ACCOUNT)
    p.set_defaults(func=cmd_paper_advance)

    p = sub.add_parser("monthly-report")
    p.add_argument("--month", required=True)
    p.add_argument("--account", default=config.PRIMARY_ACCOUNT)
    p.set_defaults(func=cmd_monthly_report)

    p = sub.add_parser("validate")
    p.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
