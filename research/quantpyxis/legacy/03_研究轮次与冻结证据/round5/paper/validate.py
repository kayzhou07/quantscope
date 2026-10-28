# -*- coding: utf-8 -*-
"""Round-5 paper infrastructure validation (P1 acceptance)."""
from __future__ import annotations

import json
import math
import tempfile
import traceback
from pathlib import Path

import sys
import time

import numpy as np

from . import config, ledger, panel, state, strategy, util


def _check(name: str, fn) -> dict:
    t0 = time.time()
    print(f"[validate] {name} ...", flush=True, file=sys.stderr)
    try:
        detail = fn()
        print(f"[validate] {name} PASS {time.time() - t0:.1f}s", flush=True, file=sys.stderr)
        return {"name": name, "pass": True, "detail": detail,
                "seconds": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        print(f"[validate] {name} FAIL {time.time() - t0:.1f}s: {exc}", flush=True,
              file=sys.stderr)
        return {"name": name, "pass": False,
                "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - t0, 2),
                "traceback": traceback.format_exc().splitlines()[-6:]}


# ------------------------------------------------------------------ fixtures
class FakePanel:
    """Deterministic synthetic panel used to test ledger mechanics."""

    def __init__(self, days, bars_by_date, bands=None):
        self.days = days
        self.bars = bars_by_date
        self.bands = bands or {}

    def trading_days(self):
        return list(self.days)

    def has_bar(self, date):
        return date in self.bars and len(self.bars[date]) > 0

    def bars_on(self, date):
        rows = self.bars.get(date, [])
        if not rows:
            import pandas as pd
            return pd.DataFrame(columns=["code", "open", "high", "low", "close",
                                         "preclose", "volume", "amount"]).set_index("code")
        import pandas as pd
        df = pd.DataFrame(rows).set_index("code")
        return df

    def last_close_before(self, code, date, max_back=30):
        days = [d for d in self.days if d < date]
        for d in reversed(days[-max_back:]):
            rows = self.bars.get(d, [])
            for r in rows:
                if r["code"] == code:
                    return float(r["close"])
        return None

    def limit_band(self, code, date):
        return self.bands.get(code, 0.10)


def _tmp_dir() -> Path:
    """A writable directory INSIDE the workspace (the OS temp dir is sandbox-blocked)."""
    import uuid
    d = config.RUNS / "test_tmp" / uuid.uuid4().hex[:12]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fake_ledger(fake, cash=100000.0):
    d = _tmp_dir()
    led = ledger.PaperLedger.create(d / "acct.json", "test", cash)
    led._fake = fake
    return led


def _install_fake(led, fake):
    led_panel = ledger.panel
    ledger.panel = fake
    led._restore = led_panel
    return fake


def _restore_panel(led):
    if hasattr(led, "_restore"):
        ledger.panel = led._restore


# ------------------------------------------------------------- synthetic checks
def t_fees_and_lots():
    fake = FakePanel(
        days=["2020-01-02", "2020-01-03"],
        bars_by_date={
            "2020-01-02": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
            "2020-01-03": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
        })
    led = _fake_ledger(fake)
    _install_fake(led, fake)
    try:
        sig = {"signal_id": "s1", "asof": "2020-01-02", "nav": 100000.0,
               "targets": {"600000": 0.10}, "action": "target"}
        led.create_orders(sig, fill_date="2020-01-03")
        led.attempt_pending("2020-01-03")
        pos = led.positions["600000"]
        assert pos["shares"] % 100 == 0, pos
        fill = led.d["fills"][0]
        assert fill["commission"] >= 5.0 - 1e-9, fill
        assert abs(led.nav() - 100000.0) < 50, led.nav()
        return {"shares": pos["shares"], "commission": fill["commission"],
                "nav": led.nav()}
    finally:
        _restore_panel(led)


def t_limit_block_then_fill():
    fake = FakePanel(
        days=["2020-01-02", "2020-01-03", "2020-01-06"],
        bars_by_date={
            "2020-01-02": [{"code": "600000", "open": 11.0, "close": 11.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
            "2020-01-03": [{"code": "600000", "open": 11.0, "close": 11.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],   # limit-up, blocked
            "2020-01-06": [{"code": "600000", "open": 10.5, "close": 10.5,
                            "preclose": 11.0, "high": 11.5, "low": 10.0,
                            "volume": 1e6, "amount": 1e7}],   # fills
        })
    led = _fake_ledger(fake)
    _install_fake(led, fake)
    try:
        sig = {"signal_id": "s1", "asof": "2020-01-02", "nav": 100000.0,
               "targets": {"600000": 0.10}, "action": "target"}
        led.create_orders(sig, fill_date="2020-01-03")
        r1 = led.attempt_pending("2020-01-03")
        assert len(led.d["fills"]) == 0, led.d["fills"]
        assert len(led.d["pending_orders"]) == 1
        r2 = led.attempt_pending("2020-01-06")
        assert len(led.d["fills"]) == 1, led.d["fills"]
        return {"first_attempt": r1, "second_attempt": r2}
    finally:
        _restore_panel(led)


def t_suspension_retention():
    fake = FakePanel(
        days=["2020-01-02", "2020-01-03", "2020-01-06"],
        bars_by_date={
            "2020-01-02": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
            "2020-01-03": [],   # suspended, no bar
            "2020-01-06": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
        })
    led = _fake_ledger(fake, cash=50000.0)
    _install_fake(led, fake)
    try:
        led.d["positions"]["600000"] = {"shares": 100.0, "cost_total": 1000.0,
                                        "last_price": 10.0, "last_date": "2020-01-02",
                                        "open_date": "2020-01-02"}
        sig = {"signal_id": "s2", "asof": "2020-01-02", "nav": 51000.0,
               "targets": {}, "action": "risk_off"}
        led.create_orders(sig, fill_date="2020-01-03")
        led.attempt_pending("2020-01-03")
        assert len(led.d["pending_orders"]) == 1
        led.attempt_pending("2020-01-06")
        assert len(led.d["fills"]) == 1
        return {"retained_after_suspension": True}
    finally:
        _restore_panel(led)


def t_corporate_action():
    fake = FakePanel(
        days=["2020-01-02", "2020-01-03"],
        bars_by_date={
            "2020-01-02": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
            "2020-01-03": [{"code": "600000", "open": 5.0, "close": 5.0,
                            "preclose": 5.0, "high": 5.5, "low": 4.5,
                            "volume": 2e6, "amount": 1e7}],
        })
    led = _fake_ledger(fake, cash=0.0)
    _install_fake(led, fake)
    try:
        led.d["positions"]["600000"] = {"shares": 100.0, "cost_total": 1000.0,
                                        "last_price": 10.0, "last_date": "2020-01-02",
                                        "open_date": "2020-01-02"}
        before = led.nav()
        led.apply_corporate_actions("2020-01-03")
        led.mark("2020-01-03")
        after = led.nav()
        assert abs(led.positions["600000"]["shares"] - 200.0) < 1e-6
        assert abs(before - after) < 1e-6, (before, after)
        return {"shares": led.positions["600000"]["shares"], "nav_before": before,
                "nav_after": after}
    finally:
        _restore_panel(led)


def t_idempotent_orders():
    fake = FakePanel(
        days=["2020-01-02", "2020-01-03"],
        bars_by_date={
            "2020-01-02": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
            "2020-01-03": [{"code": "600000", "open": 10.0, "close": 10.0,
                            "preclose": 10.0, "high": 11.0, "low": 9.0,
                            "volume": 1e6, "amount": 1e7}],
        })
    led = _fake_ledger(fake, cash=100000.0)
    _install_fake(led, fake)
    try:
        sig = {"signal_id": "s3", "asof": "2020-01-02", "nav": 100000.0,
               "targets": {"600000": 0.10}, "action": "target"}
        b1 = led.create_orders(sig, fill_date="2020-01-03")
        n1 = len(led.d["pending_orders"])
        b2 = led.create_orders(sig, fill_date="2020-01-03")
        n2 = len(led.d["pending_orders"])
        assert b1 == b2 and n1 == n2, (b1, b2, n1, n2)
        led.attempt_pending("2020-01-03")
        nf1 = len(led.d["fills"])
        led.attempt_pending("2020-01-03")
        nf2 = len(led.d["fills"])
        assert nf1 == nf2 == 1, (nf1, nf2)
        return {"pending_before_after": [n1, n2], "fills": nf1}
    finally:
        _restore_panel(led)


# --------------------------------------------------------------- real-data checks
def t_no_future_input():
    asof = "2023-06-30"
    days, wide = panel.qfq_window(asof, 260)
    assert max(days) <= asof
    assert max(wide.index) <= asof
    try:
        panel.assert_not_future("2023-07-03", asof)
        raise AssertionError("future guard did not fire")
    except panel.FutureDataError:
        pass
    feats = strategy.build_features(asof)
    assert len(feats) > 100
    return {"qfq_window_last": max(days), "features": len(feats)}


def t_state_missing_fails():
    try:
        state.recompute("2026-10-20")
        raise AssertionError("missing-data state should fail, not fall back to N")
    except panel.DataUnavailable as exc:
        return {"raised": str(exc)[:120]}


def t_signal_idempotent_real():
    asof = "2023-06-30"
    s1 = strategy.build_signal(asof, "scheduled", 100000.0, {})
    s2 = strategy.build_signal(asof, "scheduled", 100000.0, {})
    assert s1["signal_id"] == s2["signal_id"], (s1["signal_id"], s2["signal_id"])
    a = dict(s1); a.pop("generated_at"); a.pop("signal_id")
    b = dict(s2); b.pop("generated_at"); b.pop("signal_id")
    assert util.canonical(a) == util.canonical(b)
    return {"signal_id": s1["signal_id"], "action": s1["action"],
            "n_targets": len(s1.get("targets", {}))}


def t_month_end_boundary():
    assert strategy.is_month_end("2023-06-30") is True
    assert strategy.is_month_end("2023-06-15") is False
    assert strategy.is_month_end("2023-06-30") == (strategy.next_trading_day("2023-06-30")[:7]
                                                   != "2023-06")
    return {"2023-06-30": strategy.is_month_end("2023-06-30"),
            "2023-06-15": strategy.is_month_end("2023-06-15")}


def t_initial_account_end_to_end():
    import sys
    asof = "2026-09-30"
    print("[ia] has_bar asof", flush=True, file=sys.stderr)
    if not panel.has_bar(asof):
        return {"skipped": f"no bar on {asof}"}
    print("[ia] build_signal", flush=True, file=sys.stderr)
    sig = strategy.build_signal(asof, "initial", 100000.0, {})
    assert sig["action"] in ("target", "hold_cash", "risk_off")
    print("[ia] create ledger", flush=True, file=sys.stderr)
    p = _tmp_dir() / "acct.json"
    led = ledger.PaperLedger.create(p, "test-initial", 100000.0)
    exec_day = sig["planned_exec_date"]
    print("[ia] exec_day", exec_day, flush=True, file=sys.stderr)
    if not panel.has_bar(exec_day):
        return {"signal_id": sig["signal_id"], "note": "exec day not in data",
                "action": sig["action"]}
    print("[ia] create_orders", flush=True, file=sys.stderr)
    led.create_orders(sig, fill_date=exec_day)
    print("[ia] attempt_pending", flush=True, file=sys.stderr)
    led.attempt_pending(exec_day)
    print("[ia] state", flush=True, file=sys.stderr)
    info = state.recompute(asof)
    print("[ia] mark", flush=True, file=sys.stderr)
    led.mark(exec_day, info["letter"], config.EXPOSURE.get(info["letter"], 0.0))
    row = led.d["nav_history"][-1]
    assert abs(row["nav"] - (row["cash"] + row["market_value"])) < 1e-6
    assert row["cash"] >= -1e-6
    assert row["n_positions"] <= config.TOP_N
    print("[ia] audit", flush=True, file=sys.stderr)
    audit = led.daily_audit(exec_day, info["letter"],
                            config.EXPOSURE.get(info["letter"], 0.0))
    return {"signal_id": sig["signal_id"], "exec_day": exec_day,
            "n_positions": row["n_positions"], "nav": row["nav"],
            "cash_reason": audit["cash_reason"]}


CORE = ["freeze_snapshot", "no_future_input", "state_missing_fails",
        "signal_idempotent", "month_end_boundary", "initial_account",
        "fees_and_lots", "limit_block_then_fill", "suspension_retention",
        "corporate_action", "idempotent_orders", "capital_conservation"]


def t_freeze_snapshot():
    import hashlib
    sums = config.SEAL / "SHA256SUMS.txt"
    bad = []
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        p = config.SEAL / rel
        if not p.exists():
            bad.append(rel)
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h != digest:
            bad.append(rel)
    assert not bad, bad
    return {"manifest_sha256": util.sha256_file(config.SEAL / "FREEZE_MANIFEST.json")}


def t_capital_conservation():
    days = ["2020-01-02", "2020-01-03", "2020-01-06"]
    bars = {}
    for i, d in enumerate(days):
        bars[d] = [{"code": "600000", "open": 10.0 + i * 0.1, "close": 10.0 + i * 0.1,
                    "preclose": 10.0 + max(0, i - 1) * 0.1, "high": 12.0, "low": 8.0,
                    "volume": 1e6, "amount": 1e7}]
    fake = FakePanel(days=days, bars_by_date=bars)
    led = _fake_ledger(fake, cash=100000.0)
    _install_fake(led, fake)
    try:
        sig = {"signal_id": "s4", "asof": "2020-01-02", "nav": 100000.0,
               "targets": {"600000": 0.50}, "action": "target"}
        led.create_orders(sig, fill_date="2020-01-03")
        for d in days:
            led.attempt_pending(d)
            led.mark(d)
            row = led.d["nav_history"][-1]
            assert abs(row["nav"] - (row["cash"] + row["market_value"])) < 1e-6
        return {"days": len(led.d["nav_history"]),
                "nav": led.d["nav_history"][-1]["nav"]}
    finally:
        _restore_panel(led)


def run_all() -> dict:
    tests = {
        "freeze_snapshot": t_freeze_snapshot,
        "no_future_input": t_no_future_input,
        "state_missing_fails": t_state_missing_fails,
        "signal_idempotent": t_signal_idempotent_real,
        "month_end_boundary": t_month_end_boundary,
        "initial_account": t_initial_account_end_to_end,
        "fees_and_lots": t_fees_and_lots,
        "limit_block_then_fill": t_limit_block_then_fill,
        "suspension_retention": t_suspension_retention,
        "corporate_action": t_corporate_action,
        "idempotent_orders": t_idempotent_orders,
        "capital_conservation": t_capital_conservation,
    }
    checks = [_check(name, tests[name]) for name in CORE]
    by = {c["name"]: c for c in checks}
    return {"schema": "round5-validation-1", "generated_at": util.now_iso(),
            "freeze_id": config.FREEZE_ID, "checks": checks,
            "summary": {c["name"]: ("PASS" if c["pass"] else "FAIL") for c in checks},
            "pass": all(by[n]["pass"] for n in CORE)}
