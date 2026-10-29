# -*- coding: utf-8 -*-
"""Portfolio-level risk overlays on top of round-4 variants.

dd_fuse     : account drawdown from running peak. dd >= second -> 0 exposure; dd >= first
              -> halve exposure.  Evaluated at the close, executed T+1 open.  Account-level
              fuse, not a user-confirmed drawdown limit.
crash_brake : index 1-day <= -5% or 5-day <= -8% -> go flat for the next 10 sessions
              (a priori thresholds borrowed from the earlier V4 project, not tuned here).

Both only REDUCE exposure.  A material target change (>15pp) triggers an off-month rebalance
so the overlay is actually implemented between scheduled month-ends.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import variants as V  # noqa: E402


class OverlayVariant(V.Variant):
    def __init__(self, *a, dd_fuse=None, crash_brake=None, **kw):
        super().__init__(*a, **kw)
        self.dd_fuse = tuple(dd_fuse) if dd_fuse else None
        self.crash_brake = tuple(crash_brake) if crash_brake else None
        self._peak = 0.0
        self._lock_until = -1
        self._prev_target = None

    def _desired_exposure(self, ctx) -> float:
        exp = super()._desired_exposure(ctx)
        if self.crash_brake is not None:
            px = ctx.index_history(30, "sh000300", "close")
            if px is not None and len(px) >= 6:
                r1 = float(px.iloc[-1] / px.iloc[-2] - 1.0)
                r5 = float(px.iloc[-1] / px.iloc[-6] - 1.0)
                if r1 <= float(self.crash_brake[0]) or r5 <= float(self.crash_brake[1]):
                    self._lock_until = max(self._lock_until,
                                           ctx.day + int(self.crash_brake[2]))
            if ctx.day < self._lock_until:
                exp = 0.0
        if self.dd_fuse is not None and self._peak > 0 and exp > 0:
            dd = 1.0 - float(ctx.nav) / self._peak
            if dd >= float(self.dd_fuse[1]):
                exp = 0.0
            elif dd >= float(self.dd_fuse[0]):
                exp *= 0.5
        return max(0.0, min(1.0, exp))

    def on_bar(self, ctx):
        nav = float(ctx.nav)
        self._peak = max(self._peak, nav) if self._peak > 0 else nav
        prev = self._prev_target
        super().on_bar(ctx)
        cur = self._prev_exp
        if (prev is not None and cur > 0 and prev > 0
                and abs(cur - prev) > 0.15 and not ctx.is_rebalance_day):
            self._rebalance(ctx, cur, "overlay")
        self._prev_target = cur
