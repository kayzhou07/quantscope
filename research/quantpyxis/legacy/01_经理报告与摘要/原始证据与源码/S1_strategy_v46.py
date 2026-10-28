# -*- coding: utf-8 -*-
"""V4.6 试运行：V4(60/25) + NEUTRAL 快速减仓（run2 方向 B）。

V4 的 60/25 只在季度调仓日生效；BULL→NEUTRAL 后旧持仓可延续，NEUTRAL 日实际暴露
仍可能高达 62%（见 verifier 复核）。本策略在每日收盘检查：

  * 若当日状态为 NEUTRAL 且当前股票暴露 > NEUTRAL 目标 + 3pp：
        下一交易日开盘把持仓按比例缩减到 NEUTRAL 目标（同一 NEUTRAL 段只做一次）；
  * 状态离开 NEUTRAL 后重置标记（避免重复摩擦）；
  * BEAR/CRASH 仍由父类 V3 的每日清仓逻辑处理。

不改选股、不改季度调仓、不加其他择时。目的：修复 2025-04-07 式的"4/3 已转 NEUTRAL
却仍带 50.9% 仓位过夜"漏洞。现金计息由引擎 1.0.3 的 cash_rate 单独控制。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

WS = Path(__file__).resolve().parents[2]
QRUN = WS / "qrun_1416"
if str(QRUN) not in sys.path:
    sys.path.insert(0, str(QRUN))

from engine.core import Strategy  # noqa: E402


def _load_qrun_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, QRUN / "os" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_final = _load_qrun_module("qrun_strategy_final_v46", "strategy_final.py")
_BaseV3 = _final.BullBearOSv3


class BullBearOSv46(_BaseV3):
    """V4.6 = V3 engine + exposure budget + NEUTRAL fast de-risking."""

    name = "BullBearOS-v4.6"

    def __init__(self, top_n: int = 10, min_amount: float = 2.0e8,
                 max_vol: float | None = 0.55,
                 exposure: dict | None = None,
                 neutral_trim: bool = True,
                 trim_band: float = 0.03,
                 trim_weak_only: bool = False,
                 name: str | None = None):
        super().__init__(top_n=top_n, min_amount=min_amount, max_vol=max_vol,
                         exposure=exposure or {"BULL": 0.60, "NEUTRAL": 0.25,
                                               "BEAR": 0.0, "CRASH": 0.0},
                         daily_regime_exit=True)
        self.neutral_trim = bool(neutral_trim)
        self.trim_band = float(trim_band)
        self.trim_weak_only = bool(trim_weak_only)
        self.name = name or self.name

    def on_init(self, ctx):
        super().on_init(ctx)
        ctx.state.setdefault("neutral_trimmed", False)
        ctx.state.setdefault("n_neutral_trim", 0)

    def on_bar(self, ctx):
        super().on_bar(ctx)  # BEAR/CRASH daily exit from V3
        if not self.neutral_trim:
            return
        regime = ctx.regime or "NEUTRAL"
        st = ctx.state
        if regime == "NEUTRAL":
            target = float(self.exposure.get("NEUTRAL", 0.0))
            if target <= 0:
                return
            if self.trim_weak_only:
                # only de-risk when the index is already below MA20 (defensive regime),
                # avoiding whipsaws on NEUTRAL states that quickly revert to BULL.
                try:
                    s = ctx.index_history(60, "sh000300", "close")
                    import pandas as pd
                    s = pd.Series(s).dropna()
                    if len(s) < 20:
                        return
                    px = float(s.iloc[-1])
                    ma20 = float(s.iloc[-20:].mean())
                    if px >= ma20:
                        return
                except Exception:  # noqa: BLE001
                    return
            current = sum(float(p.value()) for p in ctx.positions.values()) / max(ctx.nav, 1.0)
            if current > target + self.trim_band and not st.get("neutral_trimmed"):
                factor = target / current if current > 0 else 0.0
                weights = {c: float(p.value()) / float(ctx.nav) * factor
                           for c, p in ctx.positions.items() if p.value() > 1.0}
                weights = {c: w for c, w in weights.items() if w > 1e-4}
                if weights:
                    ctx.set_targets(weights, note=f"neutral_trim_to_{target:.2f}")
                else:
                    ctx.set_targets({}, note=f"neutral_trim_cash")
                st["neutral_trimmed"] = True
                st["n_neutral_trim"] = int(st.get("n_neutral_trim", 0)) + 1
                ctx.add_event("neutral_trim", target=target, current=round(current, 4))
        else:
            st["neutral_trimmed"] = False
