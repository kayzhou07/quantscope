# -*- coding: utf-8 -*-
"""Bull/Bear dual operating system v2 (Lead, 2026-10-20) — simpler, lower-turnover.

Why V2 exists
-------------
V1 (os/RULES_V1.md) was frozen before seeing results. The first real-data run exposed two
mechanical pathologies that are documented as failed design, not silently patched:
  1) stock-level *daily* trailing stops (15%) on high-volatility momentum names caused
     repeated sell-then-rebuy churn (772 trades, 4.5x annual turnover, 6,000 CNY fees);
  2) the account-level drawdown ladder based on the all-time NAV peak kept an all-cash
     account permanently "under water" vs the old peak and re-triggered forever.
V2 keeps the same frozen market-regime engine (os/regime_v1.py) and selection score but:
  * removes all daily stock-level exits; risk is managed at the monthly rebalance plus the
    market-regime switch (which is the real bear-market defence);
  * uses a hold buffer (keep names ranked in the top 2N) to cut turnover;
  * applies a simple volatility cap to bull exposure;
  * allows a one-off "risk-off switch" only when both the market regime is not BULL and the
    strategy drawdown vs its post-reset peak exceeds 20% (with peak reset on re-entry so it
    cannot trap itself).
Everything is still 100k, long-only, main-board + ChiNext only, integer lots, T+1,
next-open execution, same fees. See os/RULES_V2.md for the frozen parameter set.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

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


_v1 = _load_sibling("os_strategy_v1_mod", "strategy_v1.py")
_UniverseFeatures = _v1._UniverseFeatures
_pick = _v1._pick
_weights = _v1._weights


class BullBearOSv2(Strategy):
    name = "BullBearOS-v2"

    def __init__(self, top_bull=8, top_neutral=5, buffer_mult=2,
                 exposure=None, min_amount=5.0e7, min_price=2.0,
                 use_regime=True, force_exposure=None, name=None,
                 vol_cap=0.22):
        self.top_bull = int(top_bull)
        self.top_neutral = int(top_neutral)
        self.buffer_mult = int(buffer_mult)
        self.exposure = exposure or {"BULL": 0.95, "NEUTRAL": 0.30,
                                     "BEAR": 0.0, "CRASH": 0.0}
        self.min_amount = float(min_amount)
        self.min_price = float(min_price)
        self.use_regime = bool(use_regime)
        self.force_exposure = force_exposure
        self.vol_cap = float(vol_cap)
        self.warmup = 280
        self.name = name or ("BullBearOS-v2" if use_regime and force_exposure is None
                             else f"BullBearOSv2({force_exposure})" if force_exposure is not None
                             else "BullBearOSv2(noRegime)")

    # ---- regime helper --------------------------------------------------
    def _regime(self, ctx) -> str:
        if not self.use_regime:
            return "BULL"
        return ctx.regime or "NEUTRAL"

    def _target_exposure(self, ctx, regime: str) -> float:
        if self.force_exposure is not None:
            return float(self.force_exposure)
        return float(self.exposure.get(regime, 0.0))

    # ---- selection with hold buffer ------------------------------------
    def _select(self, ctx, n_names: int, exposure: float, nav: float) -> dict:
        feats = _UniverseFeatures(ctx, min_history=250, min_amount=self.min_amount,
                                  min_price=self.min_price)
        if not feats.ok or feats.score is None:
            return {}
        score = feats.score
        cands = [c for c in feats.universe if np.isfinite(score.get(c, np.nan))]
        cands.sort(key=lambda c: (-float(score[c]), c))
        rank = {c: i for i, c in enumerate(cands)}
        keep_limit = min(len(cands), max(n_names * self.buffer_mult, n_names + 4))
        held = [c for c in ctx.positions if c in rank and rank[c] < keep_limit]
        held.sort(key=lambda c: rank[c])
        picked = held[:n_names]
        for c in cands:
            if len(picked) >= n_names:
                break
            if c in picked:
                continue
            picked.append(c)
        picked = _pick(picked, n_names, exposure, nav, feats.raw_close)
        # volatility cap: if the average vol of the picked basket is high, trim exposure
        if picked:
            vols = [float(feats.rvol.get(c, np.nan)) for c in picked]
            vols = [v for v in vols if np.isfinite(v)]
            if vols:
                avg_vol = sum(vols) / len(vols)
                if avg_vol > self.vol_cap / max(exposure, 0.2):
                    cap = exposure * (self.vol_cap / max(exposure, 0.2)) / avg_vol
                    exposure = max(0.0, min(exposure, cap))
        return _weights(picked, exposure)

    # ---- lifecycle ------------------------------------------------------
    def on_init(self, ctx):
        st = ctx.state
        st.setdefault("peak_nav", ctx.nav)
        st.setdefault("risk_off", False)
        st.setdefault("prev_regime", None)
        st.setdefault("rebalance_count", 0)

    def on_bar(self, ctx):
        # V2 has no daily stock exits.  Only one account-level circuit breaker:
        # if the market is NOT in BULL and the strategy is >20% below its (rebased) peak,
        # go flat and reset the peak so the portfolio can re-enter later without a trap.
        st = ctx.state
        nav = float(ctx.nav)
        regime = self._regime(ctx)
        peak = float(st.get("peak_nav", nav))
        if not st.get("risk_off"):
            if nav > peak:
                st["peak_nav"] = nav
                peak = nav
            dd = nav / peak - 1.0 if peak > 0 else 0.0
            if regime in ("BEAR", "CRASH") and dd <= -0.20:
                ctx.close_all(note="v2_risk_off_switch")
                st["risk_off"] = True
                st["peak_nav"] = nav
                ctx.add_event("risk_off_switch", dd=round(dd, 4), date=ctx.date)
        else:
            # risk-off is released when the market regime returns to BULL/NEUTRAL
            if regime in ("BULL", "NEUTRAL"):
                st["risk_off"] = False
                st["peak_nav"] = max(float(st.get("peak_nav", nav)), nav)

    def on_rebalance(self, ctx):
        st = ctx.state
        nav = float(ctx.nav)
        regime = self._regime(ctx)
        st.setdefault("rebalance_count", 0)
        if regime in ("BEAR", "CRASH") and self.force_exposure is None:
            ctx.set_targets({}, note=f"regime_{regime}_flush")
            st["rebalance_count"] += 1
            ctx.add_event("rebalance", regime=regime, exposure=0.0, n=0)
            return
        exposure = self._target_exposure(ctx, regime)
        if st.get("risk_off"):
            exposure = 0.0
        if exposure <= 0:
            ctx.set_targets({}, note=f"cash_{regime}")
            st["rebalance_count"] += 1
            ctx.add_event("rebalance", regime=regime, exposure=0.0, n=0)
            return
        n_names = self.top_bull if regime == "BULL" else self.top_neutral
        targets = self._select(ctx, n_names, exposure, nav)
        if not targets:
            ctx.set_targets({}, note=f"no_candidates_{regime}")
        else:
            ctx.set_targets(targets, note=f"{regime}_n{len(targets)}_exp{exposure:.2f}")
        st["rebalance_count"] += 1
        ctx.add_event("rebalance", regime=regime,
                      exposure=round(float(sum(targets.values())), 4), n=len(targets))
