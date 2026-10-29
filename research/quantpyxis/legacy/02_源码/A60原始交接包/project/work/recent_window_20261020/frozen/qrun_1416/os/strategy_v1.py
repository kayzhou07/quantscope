# -*- coding: utf-8 -*-
"""Bull/Bear dual operating system v1 (Lead-owned; rules frozen in os/RULES_V1.md).

Strategies
----------
BullBearOS      : the full regime-adaptive system (BULL 90% / NEUTRAL 40% / BEAR 0%),
                  monthly rebalance, sector-agnostic risk-adjusted momentum, trailing
                  stops, trend-break exits and a portfolio-level drawdown ladder.
BullOnly        : same selection, always 90% exposure (control: shows what the market
                  regime layer adds/removes).
NeutralOnly     : same selection, always 40% exposure (control).
LegacyBQuarterly: reconstruction of the project's prior quarterly 12-1 momentum rule
                  (5 or 3 names x 19% depending on CSI300>MA120, -12% trailing stop) for
                  an apples-to-apples comparison on the independent 2014-2026 data.

Everything is measured with 100,000 CNY, no leverage, main-board + ChiNext only.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
import sys
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.core import Strategy  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _rank(values: pd.Series) -> pd.Series:
    return values.rank(pct=True, na_option="keep")


def _safe_series(cols, arr) -> pd.Series:
    return pd.Series(np.asarray(arr, dtype=float), index=pd.Index(cols, dtype=object))


class _UniverseFeatures:
    """Snapshot of cross-sectional features for one signal date (no lookahead)."""

    def __init__(self, ctx, min_history=250, min_amount=1.0e8, min_price=2.0,
                 need=260):
        universe = set(ctx.universe(tradable=True, exclude_st=True, min_history=0))
        if not universe:
            self.ok = False
            return
        cols, mat = ctx.history_matrix("qfq_close", need)
        cols = list(cols)
        keep = [i for i, c in enumerate(cols) if c in universe]
        if len(keep) < 10:
            self.ok = False
            return
        cols = [cols[i] for i in keep]
        mat = np.asarray(mat)[:, keep]
        nrows = mat.shape[0]
        # relaxed coverage: ffill handles suspension gaps (Chinese stocks suspend often, so
        # requiring every one of the last 250 bars to be present would exclude most names)
        nvalid = np.isfinite(mat).sum(axis=0)
        df = pd.DataFrame(mat, columns=cols).ffill()
        close = df.iloc[-1]
        ma60 = df.iloc[-60:].mean()
        ma120 = df.iloc[-120:].mean()
        ret = df.pct_change()
        rvol = ret.iloc[-61:].std() * math.sqrt(252.0)
        # 12-1 and 6-1 momentum on the loaded window (need>=253 rows before the signal day)
        mom12 = df.iloc[-22] / df.iloc[-253] - 1.0
        mom6 = df.iloc[-22] / df.iloc[-127] - 1.0
        raw_close = ctx.latest("close")
        amount60 = ctx.history(field="amount", window=60).mean()
        raw_close = raw_close.reindex(cols)
        amount60 = amount60.reindex(cols)
        # trend alignment
        trend_ok = (close > ma60) & (ma60 > ma120)
        liquid = amount60 >= min_amount
        price_ok = raw_close >= min_price
        valid = (nvalid >= 230) & close.notna() & ma60.notna() & ma120.notna() & \
            rvol.notna() & mom12.notna() & mom6.notna() & trend_ok & liquid & price_ok
        self.universe = [c for c in cols if bool(valid.get(c, False))]
        self.close = close
        self.raw_close = raw_close
        self.rvol = rvol
        self.score = None
        if not self.universe:
            self.ok = False
            return
        ras = mom12 / rvol.replace(0, np.nan)
        inv_vol = 1.0 / rvol.replace(0, np.nan)
        score = (0.5 * _rank(ras) + 0.3 * _rank(mom6) + 0.2 * _rank(inv_vol))
        self.score = score
        self.ok = True


def _pick(cands: list, n_target: int, exposure: float, nav: float,
          raw_close: pd.Series) -> list:
    """Pick affordable names in score order; returns list of codes."""
    picked = []
    if n_target <= 0 or exposure <= 0:
        return picked
    budget = exposure * nav / max(n_target, 1)
    for code in cands:
        price = float(raw_close.get(code, np.nan))
        if not np.isfinite(price) or price <= 0:
            continue
        if 100.0 * price > budget * 1.02:      # one lot must fit the per-name budget
            continue
        picked.append(code)
        if len(picked) >= n_target:
            break
    return picked


def _weights(picked: list, exposure: float) -> dict:
    if not picked or exposure <= 0:
        return {}
    w = exposure / len(picked)
    return {c: w for c in picked}


# --------------------------------------------------------------------------- #
# Bull / Bear operating system
# --------------------------------------------------------------------------- #
class BullBearOS(Strategy):
    name = "BullBearOS-v1"

    def __init__(self, top_bull=8, top_neutral=5, exposure=None,
                 stop_bull=0.15, stop_neutral=0.12, stop_bear=0.12,
                 min_amount=1.0e8, min_price=2.0, min_history=250,
                 use_regime=True, force_exposure=None, name=None,
                 regime_csv=None):
        self.name = name or (
            f"BullBearOS-v1" if use_regime and force_exposure is None
            else f"BullBearOS({force_exposure})" if force_exposure is not None
            else "BullBearOS(noRegime)")
        self.top_bull = int(top_bull)
        self.top_neutral = int(top_neutral)
        self.exposure = exposure or {"BULL": 0.90, "NEUTRAL": 0.40, "BEAR": 0.0, "CRASH": 0.0}
        self.stop_bull = float(stop_bull)
        self.stop_neutral = float(stop_neutral)
        self.stop_bear = float(stop_bear)
        self.min_amount = float(min_amount)
        self.min_price = float(min_price)
        self.min_history = int(min_history)
        self.use_regime = bool(use_regime)
        self.force_exposure = force_exposure
        self.regime_csv = Path(regime_csv) if regime_csv else (REPO / "os" / "out" / "regime_v1.csv")
        self.warmup = self.min_history + 20

    # ---- helpers -------------------------------------------------------
    def _target_exposure(self, ctx) -> float:
        if self.force_exposure is not None:
            return float(self.force_exposure)
        if not self.use_regime:
            return 0.90
        regime = ctx.regime or "NEUTRAL"
        return float(self.exposure.get(regime, 0.0))

    def _vol_cut(self, ctx) -> bool:
        """RULE 2.4: if 20d index vol is in the top decile of 3y, cut bull exposure to 60%."""
        try:
            px = ctx.index_history(window=770, index_code="sh000300", field="close")
        except Exception:  # noqa: BLE001
            return False
        if px is None or len(px) < 300:
            return False
        r = px.pct_change().dropna()
        vol = r.rolling(20).std() * math.sqrt(252.0)
        cur = vol.iloc[-1]
        hist = vol.iloc[-(750 + 1):-1].dropna()
        if not np.isfinite(cur) or len(hist) < 250:
            return False
        return bool((hist <= cur).mean() >= 0.90)

    def _selection(self, ctx, n_names: int, exposure: float, nav: float) -> dict:
        feats = _UniverseFeatures(ctx, min_history=self.min_history,
                                  min_amount=self.min_amount, min_price=self.min_price)
        if not feats.ok or feats.score is None:
            return {}
        score = feats.score.copy()
        cands = [c for c in feats.universe if np.isfinite(score.get(c, np.nan))]
        cands.sort(key=lambda c: (-float(score[c]), c))
        picked = _pick(cands, n_names, exposure, nav, feats.raw_close)
        return _weights(picked, exposure)

    # ---- lifecycle -----------------------------------------------------
    def on_init(self, ctx):
        ctx.state.setdefault("peak_nav", ctx.nav)
        ctx.state.setdefault("protection", False)
        ctx.state.setdefault("clear_nav", None)
        ctx.state.setdefault("clear_i", None)
        ctx.state.setdefault("dd10_done", False)
        ctx.state.setdefault("rebuild_stage", 0)
        ctx.state.setdefault("peak_close", {})
        ctx.state.setdefault("exit_queued", {})
        ctx.state.setdefault("rebalance_count", 0)
        ctx.state.setdefault("last_targets", {})
        # load regime csv only for the vol percentile column (reporting / rule 2.4)
        self._regime_df = None
        if self.regime_csv.exists():
            try:
                self._regime_df = pd.read_csv(self.regime_csv, dtype={"date": str})
                self._regime_df = self._regime_df.set_index("date")
            except Exception:  # noqa: BLE001
                self._regime_df = None

    def _vol_cut_df(self, ctx) -> bool:
        if self._regime_df is not None and "volpct" in self._regime_df.columns:
            try:
                v = float(self._regime_df.loc[ctx.date, "volpct"])
                return bool(np.isfinite(v) and v >= 0.90)
            except Exception:  # noqa: BLE001
                pass
        return self._vol_cut(ctx)

    def on_bar(self, ctx):
        st = ctx.state
        nav = float(ctx.nav)
        st["peak_nav"] = max(float(st.get("peak_nav", nav)), nav)
        peak = float(st["peak_nav"])
        dd = nav / peak - 1.0 if peak > 0 else 0.0

        # ---- portfolio-level drawdown ladder (RULE 2.6) -----------------
        # protection release: the original RULES text also required NAV to recover +5% from
        # the clearing level, which is impossible while 100% cash (the account cannot recover
        # without exposure).  Frozen bug-fix (documented as V1.1): release after 10 trading
        # days in BULL state, then rebuild in stages (45% at next rebalance -> normal).
        if st.get("protection"):
            clear_i = st.get("clear_i")
            days_in_cash = (ctx.i - clear_i) if clear_i is not None else 10 ** 6
            if ctx.regime == "BULL" and days_in_cash >= 10:
                st["protection"] = False
                st["dd10_done"] = False
                st["rebuild_stage"] = 1
                # V1.1 bug-fix: rebase the drawdown peak after a protection episode.
                # Otherwise a cash-only account can never recover the old peak and the
                # 15% ladder re-triggers forever (observed pathology in V1 backtest).
                st["peak_nav"] = nav
                ctx.add_event("protection_release", nav=round(nav, 2), date=ctx.date,
                              days_in_cash=int(days_in_cash))
        else:
            if dd <= -0.15:
                ctx.close_all(note="drawdown_ladder_15")
                st["protection"] = True
                st["clear_nav"] = nav
                st["clear_i"] = ctx.i
                st["dd10_done"] = True
                st["rebuild_stage"] = 0
                ctx.add_event("drawdown_ladder", level=0.15, dd=round(dd, 4), date=ctx.date)
            elif dd <= -0.10 and not st.get("dd10_done"):
                cur = {c: p.value() / nav for c, p in ctx.positions.items()}
                half = {c: w * 0.5 for c, w in cur.items()}
                ctx.set_targets(half, note="drawdown_ladder_10")
                st["dd10_done"] = True
                ctx.add_event("drawdown_ladder", level=0.10, dd=round(dd, 4), date=ctx.date)

        # ---- per-stock exits (trailing stop + trend break) --------------
        positions = dict(ctx.positions)
        if not positions:
            st["peak_close"] = {}
            st["exit_queued"] = {}
            return
        hold_codes = list(positions)
        q = ctx.history(codes=hold_codes, field="qfq_close", window=141)
        regime = ctx.regime if (self.use_regime and self.force_exposure is None) else "BULL"
        stop = self.stop_bull if regime == "BULL" else (
            self.stop_neutral if regime == "NEUTRAL" else self.stop_bear)
        peaks = st.setdefault("peak_close", {})
        queued = st.setdefault("exit_queued", {})
        for c in hold_codes:
            if c in queued:
                continue
            if c not in q.columns:
                continue
            series = q[c].dropna()
            if series.empty:
                continue
            last = float(series.iloc[-1])
            peaks[c] = max(float(peaks.get(c, -np.inf)), last)
            pk = float(peaks[c])
            exit_reason = None
            if pk > 0 and last <= pk * (1.0 - stop):
                exit_reason = "trailing_stop"
            elif len(series) >= 141:
                ma120 = float(series.iloc[-120:].mean())
                ma120_prev = float(series.iloc[-140:-20].mean())
                if last < ma120 and ma120 < ma120_prev:
                    exit_reason = "trend_break_ma120"
            if exit_reason:
                ctx.close(c, note=exit_reason)
                queued[c] = ctx.date
        # clear flags for closed positions
        for c in list(queued):
            if c not in positions:
                queued.pop(c, None)
                peaks.pop(c, None)

    def on_rebalance(self, ctx):
        st = ctx.state
        nav = float(ctx.nav)
        regime = ctx.regime if self.use_regime else "BULL"
        exposure = self._target_exposure(ctx)
        st.setdefault("rebalance_count", 0)
        if regime in ("BEAR", "CRASH") and self.force_exposure is None:
            ctx.set_targets({}, note=f"regime_{regime}_flush")
            st["peak_close"] = {}
            st["exit_queued"] = {}
            st["last_targets"] = {}
            st["rebalance_count"] += 1
            ctx.add_event("rebalance", regime=regime, exposure=0.0, n=0)
            return
        # volatility cut (RULE 2.4) applies to bull exposure only
        if regime == "BULL" and self.force_exposure is None and self._vol_cut_df(ctx):
            exposure = min(exposure, 0.60)
        # protection rebuild ladder
        if st.get("protection"):
            exposure = 0.0
        elif st.get("rebuild_stage", 0) > 0:
            exposure = min(exposure, 0.45)
            st["rebuild_stage"] = 0
        if exposure <= 0:
            ctx.set_targets({}, note=f"cash_{regime}")
            st["rebalance_count"] += 1
            ctx.add_event("rebalance", regime=regime, exposure=0.0, n=0)
            return
        n_names = self.top_bull if regime == "BULL" else self.top_neutral
        targets = self._selection(ctx, n_names, exposure, nav)
        if not targets:
            ctx.set_targets({}, note=f"no_candidates_{regime}")
        else:
            ctx.set_targets(targets, note=f"{regime}_n{len(targets)}_exp{exposure:.2f}")
        st["last_targets"] = dict(targets)
        st["rebalance_count"] += 1
        ctx.add_event("rebalance", regime=regime, exposure=round(exposure, 4),
                      n=len(targets))


# --------------------------------------------------------------------------- #
# Prior quarterly rule (2015-era B version), re-implemented on the new panel
# --------------------------------------------------------------------------- #
class LegacyBQuarterly(Strategy):
    """Quarterly 12-1 momentum, 5 names if CSI300>MA120 else 3, 19% each, -12% trailing stop."""

    name = "LegacyB-Quarterly"

    def __init__(self, top_n_bull=5, top_n_bear=3, per_name=0.19, stop=0.12,
                 min_amount=1.0e8, min_price=2.0, min_history=250, name=None):
        self.top_n_bull = int(top_n_bull)
        self.top_n_bear = int(top_n_bear)
        self.per_name = float(per_name)
        self.stop = float(stop)
        self.min_amount = float(min_amount)
        self.min_price = float(min_price)
        self.min_history = int(min_history)
        self.name = name or "LegacyB-Quarterly"
        self.warmup = self.min_history + 20

    def on_init(self, ctx):
        ctx.state.setdefault("peak_close", {})
        ctx.state.setdefault("exit_queued", {})
        ctx.state.setdefault("peak_nav", ctx.nav)

    def on_bar(self, ctx):
        st = ctx.state
        nav = float(ctx.nav)
        st["peak_nav"] = max(float(st.get("peak_nav", nav)), nav)
        positions = dict(ctx.positions)
        if not positions:
            st["peak_close"], st["exit_queued"] = {}, {}
            return
        codes = list(positions)
        q = ctx.history(codes=codes, field="qfq_close", window=141)
        peaks = st.setdefault("peak_close", {})
        queued = st.setdefault("exit_queued", {})
        for c in codes:
            if c in queued or c not in q.columns:
                continue
            s = q[c].dropna()
            if s.empty:
                continue
            last = float(s.iloc[-1])
            peaks[c] = max(float(peaks.get(c, -np.inf)), last)
            if last <= float(peaks[c]) * (1.0 - self.stop):
                ctx.close(c, note="legacy_trailing_stop")
                queued[c] = ctx.date
        for c in list(queued):
            if c not in positions:
                queued.pop(c, None)
                peaks.pop(c, None)

    def on_rebalance(self, ctx):
        try:
            px = ctx.index_history(window=130, index_code="sh000300", field="close")
            above = len(px) >= 121 and float(px.iloc[-1]) > float(px.iloc[-120:].mean())
        except Exception:  # noqa: BLE001
            above = True
        n_names = self.top_n_bull if above else self.top_n_bear
        exposure = n_names * self.per_name
        feats = _UniverseFeatures(ctx, min_history=self.min_history,
                                  min_amount=self.min_amount, min_price=self.min_price)
        if not feats.ok or feats.score is None:
            ctx.set_targets({})
            return
        # legacy used pure 12-1 momentum ranking; reconstruct it here
        cols = list(feats.close.index)
        q = ctx.history(codes=cols, field="qfq_close", window=260)
        score = {}
        for c in cols:
            s = q[c].dropna()
            if len(s) < 253:
                continue
            score[c] = float(s.iloc[-22] / s.iloc[0] - 1.0)
        cands = [c for c in feats.universe if c in score]
        cands.sort(key=lambda c: (-score[c], c))
        picked = _pick(cands, n_names, exposure, float(ctx.nav), feats.raw_close)
        ctx.set_targets(_weights(picked, exposure),
                        note=f"legacy_Q_n{len(picked)}_csi300_ma120={above}")


# --------------------------------------------------------------------------- #
# simple buy & hold benchmark on an index (proxy; documented in engine)
# --------------------------------------------------------------------------- #
class CashOnly(Strategy):
    name = "Cash"

    def on_init(self, ctx):
        ctx.set_targets({})

