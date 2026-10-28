# -*- coding: utf-8 -*-
"""FINAL Bull/Bear operating system (V3) — 2026-10-20, Lead.

This is the deliverable system after two documented in-sample design iterations:
    V1 (os/RULES_V1.md): frozen a-priori rules -> failed mechanically (daily stock stops
        churned 772 trades; peak-based drawdown ladder trapped the account in cash forever).
    V2 (os/strategy_v2.py): removed daily stock exits + fixed the ladder -> +26.9%,
        maxDD -37%, still weak.
    V3 (this file): same frozen regime engine + monthly/quarterly rebalance of the top-N
        12-1 momentum names, liquidity floor 200M CNY, optional volatility ceiling,
        daily regime risk exit. Chosen on the 2014-2026 sample (in-sample iteration,
        explicitly not out-of-sample; see report).

Bull OS = when regime is BULL: hold top-N risk-sorted momentum names, 95% target exposure.
Bear OS = when regime is BEAR/CRASH: 100% cash (with a daily check, not only at quarter end).
Transition (NEUTRAL) = 30% target exposure, defensive participation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.core import Strategy  # noqa: E402


def _load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "os" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_v1 = _load_sibling("os_strategy_v1_helpers", "strategy_v1.py")
_UniverseFeatures = _v1._UniverseFeatures
_pick = _v1._pick


class BullBearOSv3(Strategy):
    name = "BullBearOS-v3"

    def __init__(self, top_n=10, min_amount=2.0e8, max_vol=0.55,
                 exposure=None, buffer_mult=2, daily_regime_exit=True,
                 use_regime=True, force_exposure=None, name=None):
        self.top_n = int(top_n)
        self.min_amount = float(min_amount)
        self.max_vol = None if max_vol is None else float(max_vol)
        self.exposure = exposure or {"BULL": 0.95, "NEUTRAL": 0.30,
                                     "BEAR": 0.0, "CRASH": 0.0}
        self.buffer_mult = int(buffer_mult)
        self.daily_regime_exit = bool(daily_regime_exit)
        self.use_regime = bool(use_regime)
        self.force_exposure = force_exposure
        self.warmup = 280
        self.name = name or ("BullBearOS-v3" if use_regime and force_exposure is None
                             else f"BullBearOSv3({force_exposure})" if force_exposure is not None
                             else "BullBearOSv3(noRegime)")

    def _regime(self, ctx) -> str:
        if not self.use_regime:
            return "BULL"
        return ctx.regime or "NEUTRAL"

    def on_init(self, ctx):
        ctx.state.setdefault("n_rebalance", 0)
        ctx.state.setdefault("n_daily_exits", 0)

    def on_bar(self, ctx):
        # Bear OS: get out on the day the regime turns, not only at the next quarter end.
        if not self.daily_regime_exit or self.force_exposure is not None:
            return
        regime = self._regime(ctx)
        if regime in ("BEAR", "CRASH") and ctx.positions:
            ctx.close_all(note="daily_regime_exit")
            ctx.state["n_daily_exits"] = ctx.state.get("n_daily_exits", 0) + 1
            ctx.add_event("daily_regime_exit", regime=regime)

    def on_rebalance(self, ctx):
        regime = self._regime(ctx)
        if self.force_exposure is not None:
            exposure = float(self.force_exposure)
        else:
            exposure = float(self.exposure.get(regime, 0.0))
        if exposure <= 0:
            ctx.set_targets({}, note=f"cash_{regime}")
            ctx.add_event("rebalance", regime=regime, exposure=0.0, n=0)
            return
        feats = _UniverseFeatures(ctx, min_history=250, min_amount=self.min_amount,
                                  min_price=2.0)
        if not feats.ok or feats.score is None:
            ctx.set_targets({}, note=f"no_features_{regime}")
            return
        score = feats.score
        cands = [c for c in feats.universe if np.isfinite(score.get(c, np.nan))]
        cands.sort(key=lambda c: (-float(score[c]), c))
        if self.max_vol is not None:
            cands = [c for c in cands if float(feats.rvol.get(c, 99.0)) <= self.max_vol]
        if not cands:
            ctx.set_targets({}, note=f"no_candidates_{regime}")
            return
        rank = {c: i for i, c in enumerate(cands)}
        keep_limit = min(len(cands), max(self.top_n * self.buffer_mult, self.top_n + 4))
        held = [c for c in ctx.positions if c in rank and rank[c] < keep_limit]
        held.sort(key=lambda c: rank[c])
        picked = held[:self.top_n]
        for c in cands:
            if len(picked) >= self.top_n:
                break
            if c not in picked:
                picked.append(c)
        picked = _pick(picked, self.top_n, exposure, float(ctx.nav), feats.raw_close)
        if not picked:
            ctx.set_targets({}, note=f"unaffordable_{regime}")
            return
        w = exposure / len(picked)
        ctx.set_targets({c: w for c in picked},
                        note=f"{regime}_n{len(picked)}_exp{exposure:.2f}")
        ctx.state["n_rebalance"] = ctx.state.get("n_rebalance", 0) + 1
        ctx.add_event("rebalance", regime=regime, exposure=round(exposure, 4), n=len(picked))
