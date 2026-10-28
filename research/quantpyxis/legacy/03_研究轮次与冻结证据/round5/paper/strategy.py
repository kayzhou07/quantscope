# -*- coding: utf-8 -*-
"""Frozen strategy A signal generation from an as-of close (no engine, no future data)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config, panel, state as state_mod, util

PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")


class StrategyError(Exception):
    pass


def next_trading_day(date: str) -> str:
    days = panel.trading_days()
    later = [d for d in days if d > date]
    if not later:
        raise panel.DataUnavailable(f"no trading day after {date} in the calendar")
    return later[0]


def is_month_end(date: str) -> bool:
    """True only when the calendar contains the NEXT trading day and it is in a new month.

    If `date` is the last day of the published calendar we CANNOT confirm month-end, so we
    return False (the forward CLI then reports a calendar/data status instead of guessing).
    """
    days = panel.trading_days()
    if date not in days:
        return False
    i = days.index(date)
    if i + 1 >= len(days):
        return False
    return days[i + 1][:7] != date[:7]


def build_features(asof: str) -> pd.DataFrame:
    days, wide = panel.qfq_window(asof, 260)
    if len(days) < 260:
        raise panel.DataUnavailable(f"need 260 bars <= {asof}")
    cols = list(wide.columns)
    nvalid = wide.notna().sum()
    xs = wide.ffill()
    last_bar = wide.iloc[-1].notna()
    close = xs.iloc[-1]
    ma60 = xs.iloc[-60:].mean()
    ma120 = xs.iloc[-120:].mean()
    r = xs.pct_change()
    rvol = r.iloc[-61:].std() * math.sqrt(252.0)
    mom12 = xs.iloc[-22] / xs.iloc[-253] - 1.0
    mom6 = xs.iloc[-22] / xs.iloc[-127] - 1.0
    bars = panel.bars_on(asof)
    if bars.empty:
        raise panel.DataUnavailable(f"no bar on asof {asof}")
    raw = bars["close"].reindex(cols)
    amt60 = panel.amount_window(asof, 60).mean().reindex(cols)
    feats = pd.DataFrame(index=pd.Index(cols, name="code"))
    feats["raw"] = raw
    feats["rvol"] = rvol
    feats["mom12"] = mom12
    feats["mom6"] = mom6
    feats["nvalid"] = nvalid
    base = (nvalid >= 230) & last_bar & feats["rvol"].notna() & (feats["rvol"] > 0) \
        & feats["mom12"].notna() & feats["mom6"].notna() & feats["raw"].notna() \
        & np.isfinite(feats["rvol"]) & np.isfinite(feats["raw"])
    trend = (close > ma60) & (ma60 > ma120)
    ok = base & (amt60 >= config.MIN_AMOUNT) & trend & (raw >= config.MIN_PRICE) \
        & (rvol <= config.VOL_CAP)
    ras = (mom12 / rvol).where(base)
    m6 = mom6.where(base)
    inv = (1.0 / rvol).where(base)
    score = (config.W_RAS * ras.rank(pct=True, na_option="keep")
             + config.W_M6 * m6.rank(pct=True, na_option="keep")
             + config.W_INV * inv.rank(pct=True, na_option="keep"))
    feats["base"] = base
    feats["ok"] = ok
    feats["score"] = score
    feats["valid"] = ok & score.notna()
    return feats


def _data_hashes() -> dict:
    return {"data_manifest_sha256": config.FREEZE["data_manifest_sha256"],
            "regime_csv_sha256": config.FREEZE["regime_csv_sha256"],
            "freeze_id": config.FREEZE_ID}


def _finalize(sig: dict, asof: str) -> dict:
    """Set planned exec date and the deterministic signal id (generated_at excluded)."""
    sig["planned_exec_date"] = next_trading_day(asof)
    payload = {k: v for k, v in sig.items() if k != "generated_at"}
    sig["signal_id"] = "sig-" + util.sha256_text(util.canonical(payload))[:16]
    return sig


def build_signal(asof: str, kind: str, nav: float, holdings: dict | None = None,
                 account_id: str = config.PRIMARY_ACCOUNT) -> dict:
    """kind: 'initial' | 'scheduled' | 'risk_off'.  Holdings: {code: shares}."""
    holdings = dict(holdings or {})
    st = state_mod.recompute(asof)
    letter = st["letter"]
    exposure = config.EXPOSURE.get(letter, 0.0)
    sig: dict = {
        "schema": config.SCHEMA_SIGNAL,
        "account_id": account_id,
        "generated_at": util.now_iso(),
        "asof": asof,
        "available_at": f"{asof}T15:00:00+08:00",
        "freeze_id": config.FREEZE_ID,
        "freeze_at": config.FREEZE_AT,
        "kind": kind,
        "state": st,
        "nav": float(nav),
        "exposure": float(exposure),
        "holdings_before": holdings,
        "data": _data_hashes(),
        "diagnostics": {},
    }
    if letter in ("E", "C") or exposure <= 0:
        sig.update(action="risk_off", reason="state_forbidden", targets={})
        return _finalize(sig, asof)

    feats = build_features(asof)
    cands = feats[feats["valid"]].sort_values(
        by=["score", "code"], ascending=[False, True]).index.tolist()
    if not cands:
        sig.update(action="hold_cash", reason="no_candidate", targets={})
        sig["diagnostics"] = {"n_candidates": 0}
        return _finalize(sig, asof)

    rank = {c: i for i, c in enumerate(cands)}
    held_ranked = sorted([c for c in holdings if c in rank], key=lambda c: rank[c])
    picked = [c for c in held_ranked if rank[c] < config.BUFFER][: config.TOP_N]
    for c in cands:
        if len(picked) >= config.TOP_N:
            break
        if c not in picked:
            picked.append(c)
    budget = exposure * nav / config.TOP_N
    chosen, dropped = [], []
    for c in picked:
        p = float(feats.loc[c, "raw"])
        if math.isfinite(p) and p * 100.0 <= budget * 1.02:
            chosen.append(c)
        else:
            dropped.append({"code": c, "rank": rank[c], "raw_price": p,
                            "one_lot_value": p * 100.0, "budget": budget})
    if not chosen:
        sig.update(action="hold_cash", reason="no_affordable_candidate", targets={})
        sig["diagnostics"] = {"n_candidates": len(cands), "n_truncated": len(picked),
                              "n_dropped_unaffordable": len(dropped),
                              "budget_per_name": budget}
        return _finalize(sig, asof)
    w = exposure / len(chosen)
    sig.update(action="target", reason="scheduled" if kind == "scheduled" else kind,
               targets={c: w for c in chosen})
    sig["diagnostics"] = {"n_candidates": len(cands), "n_truncated": len(picked),
                          "n_selected": len(chosen), "n_dropped_unaffordable": len(dropped),
                          "budget_per_name": budget,
                          "dropped": dropped,
                          "chosen_detail": [{"code": c, "rank": rank[c],
                                             "raw_price": float(feats.loc[c, "raw"])}
                                            for c in chosen]}
    return _finalize(sig, asof)
