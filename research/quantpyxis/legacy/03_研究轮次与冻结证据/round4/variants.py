# -*- coding: utf-8 -*-
"""Round 4: parameterised variants of the monthly momentum baseline (user granted free redesign).

Starting point = round-3 S0 (platform R2 rules) on the independent local engine.  Each variant
changes ONE thing unless noted, so the 2014-2023 comparison stays interpretable.

Variant axes
------------
  state_mode   'frozen'  : the frozen RUN_TEXT machine (B .60 / N .25 / E-C 0)  [baseline]
               'none'    : no market state, constant exposure
               'ma200'   : sh000300 close > MA200 -> full, else cash
               'dual_ma' : close > MA60 and MA60 > MA120 -> full; close < MA120 -> cash;
                           otherwise neutral exposure
  exposure     per-state or per-mode target stock exposure
  top_n        max names (default 10)
  buffer       retention buffer = candidate rank < buffer (default 20; 1 = no retention)
  max_vol      per-stock 60d annualised vol cap (default .55; None = no cap)
  score_mode   'base' .5*RAS + .3*M6 + .2*(1/VOL)   [platform]
               'equal', 'ras', 'm6', 'lowvol'
  afford       'drop'     : truncate to top_n THEN drop unaffordable (platform S0)
               'backfill' : full-ranked affordability scan + backfill (S1b / strict single change)
               'fees'     : backfill with fee-inclusive one-lot test (S1)
  full         full exposure used by non-frozen state modes (and 'none')

Everything else (universe prefix filter, 260 bars, >=230 valid, 12-1 & 6-1 momentum, 55% vol cap,
raw>=2, 60d amount>=2e8, trend P>MA60>MA120, monthly last-trading-day rebalance, first-day initial,
E/C daily exit, T+1 open fills, explicit full exits, fees) is unchanged from round 3.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ABS = Path(r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416")
if not (_ABS / "engine").exists():
    _ABS = Path(__file__).resolve().parents[3] / "qrun_1416"
REPO = _ABS
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.core import Strategy  # noqa: E402

_SH = ("600", "601", "603", "605")
_SZ = ("000", "001", "002", "003", "300", "301")
BODY_LETTERS = {"B", "N", "E", "C"}


def board_ok(code) -> bool:
    c = str(code)
    return c.startswith(_SH) or c.startswith(_SZ)


def rank_pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, na_option="keep")


class Feat:
    __slots__ = ("ok", "cands", "raw", "n_candidates", "n_universe")

    def __init__(self):
        self.ok = False
        self.cands: list = []
        self.raw = pd.Series(dtype=float)
        self.n_candidates = 0
        self.n_universe = 0


def snapshot(ctx, *, min_amount=2.0e8, max_vol=0.55, min_price=2.0,
             window=260, score_mode="base") -> Feat:
    f = Feat()
    universe = [c for c in ctx.universe(tradable=True, exclude_st=True) if board_ok(c)]
    f.n_universe = len(universe)
    if len(universe) < 10:
        return f
    uni = set(universe)
    cols, mat = ctx.history_matrix("qfq_close", window)
    cols = list(cols)
    keep = [i for i, c in enumerate(cols) if c in uni]
    if len(keep) < 10:
        return f
    sel = [cols[i] for i in keep]
    m = np.asarray(mat, dtype=np.float64)[:, keep]

    nvalid = np.isfinite(m).sum(axis=0)
    df = pd.DataFrame(m, columns=sel).ffill()
    last_bar = np.isfinite(m[-1, :])
    close = df.iloc[-1]
    ma60 = df.iloc[-60:].mean()
    ma120 = df.iloc[-120:].mean()
    rvol = df.pct_change().iloc[-61:].std() * math.sqrt(252.0)
    mom12 = df.iloc[-22] / df.iloc[-253] - 1.0
    mom6 = df.iloc[-22] / df.iloc[-127] - 1.0
    raw = ctx.latest("close").reindex(sel)
    _, amt_mat = ctx.history_matrix("amount", 60)
    amt60 = pd.DataFrame(np.asarray(amt_mat, dtype=np.float64)[:, keep], columns=sel).mean(axis=0)

    x253 = df.iloc[-253].to_numpy(dtype=float)
    x127 = df.iloc[-127].to_numpy(dtype=float)
    base = (pd.Series(nvalid, index=sel) >= 230) & pd.Series(last_bar, index=sel)
    base &= pd.Series(np.isfinite(x253), index=sel) & pd.Series(np.isfinite(x127), index=sel)
    base &= rvol.notna() & np.isfinite(rvol.to_numpy()) & (rvol > 0)
    base &= mom12.notna() & mom6.notna() & raw.notna() & np.isfinite(raw.to_numpy())

    ok = base & (amt60 >= min_amount) & (close > ma60) & (ma60 > ma120) \
        & (raw >= min_price)
    if max_vol is not None:
        ok &= (rvol <= float(max_vol))

    ras = (mom12 / rvol).where(base)
    m6 = mom6.where(base)
    inv = (1.0 / rvol).where(base)
    if score_mode == "base":
        score = .5 * rank_pct(ras) + .3 * rank_pct(m6) + .2 * rank_pct(inv)
    elif score_mode == "equal":
        score = (rank_pct(ras) + rank_pct(m6) + rank_pct(inv)) / 3.0
    elif score_mode == "ras":
        score = rank_pct(ras)
    elif score_mode == "m6":
        score = rank_pct(m6)
    elif score_mode == "lowvol":
        score = .4 * rank_pct(ras) + .4 * rank_pct(inv) + .2 * rank_pct(m6)
    else:
        raise ValueError(f"unknown score_mode {score_mode!r}")
    s = score[ok].dropna()
    f.ok = True
    f.n_candidates = int(s.shape[0])
    f.raw = raw
    f.cands = sorted(s.index, key=lambda c: (-float(s[c]), str(c)))
    return f


class Variant(Strategy):
    EXPOSURE = {"B": 0.60, "N": 0.25, "E": 0.0, "C": 0.0}

    def __init__(self, name="variant", *, state_mode="frozen", exposure=None,
                 full=1.0, neutral=0.30, top_n=10, buffer=20,
                 max_vol=0.55, score_mode="base", afford="drop",
                 min_amount=2.0e8, min_price=2.0, record=False,
                 vol_target=None, index_cap=None):
        self.name = name
        self.state_mode = state_mode
        self.exposure = dict(exposure) if exposure else dict(self.EXPOSURE)
        self.full = float(full)
        self.neutral = float(neutral)
        self.top_n = int(top_n)
        self.buffer = int(buffer)
        self.max_vol = None if max_vol is None else float(max_vol)
        self.score_mode = score_mode
        self.afford = afford
        self.min_amount = float(min_amount)
        self.min_price = float(min_price)
        self.record = bool(record)
        self.vol_target = None if vol_target is None else float(vol_target)
        self.index_cap = index_cap
        self.warmup = 300
        self._init = False
        self._book = False
        self._last_reb = None
        self._prev_exp = 0.0
        self.audit_days: list = []
        self.audit_reb: list = []

    # ---------------------------------------------------------------- state
    def _desired_exposure(self, ctx) -> float:
        mode = self.state_mode
        px = ctx.index_history(280, "sh000300", "close")
        if mode == "frozen":
            st = ctx.regime
            st = st if st in BODY_LETTERS else "N"
            self._state_letter = st
            exp = float(self.exposure.get(st, 0.0))
        else:
            self._state_letter = "T"
            if px is None or len(px) < 200 or not np.isfinite(px.iloc[-1]):
                return 0.0
            close = float(px.iloc[-1])
            if mode == "none":
                exp = self.full
            elif mode == "ma200":
                exp = self.full if close > float(px.iloc[-200:].mean()) else 0.0
            elif mode == "dual_ma":
                ma60 = float(px.iloc[-60:].mean())
                ma120 = float(px.iloc[-120:].mean())
                if close < ma120:
                    exp = 0.0
                elif close > ma60 and ma60 > ma120:
                    exp = self.full
                else:
                    exp = self.neutral
            else:
                raise ValueError(mode)
        # ---- optional risk overlays (only reduce exposure, never increase) ----
        if exp > 0 and px is not None and len(px) >= 260 and np.isfinite(px.iloc[-1]):
            close = float(px.iloc[-1])
            if self.index_cap == "ma200" and close < float(px.iloc[-200:].mean()):
                exp = 0.0
            elif self.index_cap == "ma120" and close < float(px.iloc[-120:].mean()):
                exp = 0.0
            if exp > 0 and self.vol_target is not None:
                r = px.pct_change().dropna()
                vol20 = float(r.iloc[-20:].std() * math.sqrt(252.0))
                if np.isfinite(vol20) and vol20 > 0:
                    exp *= min(1.0, self.vol_target / vol20)
        return max(0.0, min(1.0, exp))

    # ------------------------------------------------------------- picking
    def _one_lot_cost(self, price: float) -> float:
        v = price * 100.0 * 1.001
        return v + max(5.0, v * 0.00025) + v * 0.00001

    def _select(self, ctx, feats: Feat, exposure: float, nav: float, letters: dict):
        """Returns (weights dict, info dict)."""
        cands = feats.cands
        rank = {c: i for i, c in enumerate(cands)}
        held = sorted([c for c in ctx.positions if c in rank], key=lambda c: rank[c])
        budget = exposure * nav / self.top_n
        info = dict(n_candidates=len(cands), n_held=len(held), budget=budget,
                    n_dropped=0, n_truncated=0)
        raw = feats.raw

        if self.afford == "drop":
            picked = [c for c in held if rank[c] < self.buffer][: self.top_n]
            for c in cands:
                if len(picked) >= self.top_n:
                    break
                if c not in picked:
                    picked.append(c)
            info["n_truncated"] = len(picked)
            kept = []
            for c in picked:
                p = float(raw.get(c, np.nan))
                if math.isfinite(p) and p * 100.0 <= budget * 1.02:
                    kept.append(c)
            info["n_dropped"] = len(picked) - len(kept)
            picked = kept
        else:
            if self.afford == "fees":
                def feas(c):
                    p = float(raw.get(c, np.nan))
                    return bool(math.isfinite(p) and self._one_lot_cost(p) <= budget)
            elif self.afford == "backfill":
                def feas(c):
                    p = float(raw.get(c, np.nan))
                    return bool(math.isfinite(p) and p * 100.0 <= budget * 1.02)
            else:
                raise ValueError(self.afford)
            fset = {c for c in cands if feas(c)}
            picked = [c for c in held if c in fset and rank[c] < self.buffer][: self.top_n]
            for c in cands:
                if len(picked) >= self.top_n:
                    break
                if c in fset and c not in picked:
                    picked.append(c)
            info["n_feasible"] = len(fset)
        info["n_selected"] = len(picked)
        if not picked:
            return {}, info
        w = exposure / len(picked)
        return {c: w for c in picked}, info

    # ------------------------------------------------------------ lifecycle
    @staticmethod
    def _exit_all(ctx, keep: set) -> int:
        n = 0
        for c, p in list(ctx.positions.items()):
            if str(c) in keep or p.shares <= 0:
                continue
            ctx.sell(c, fraction=1.0, note="variant_exit")
            n += 1
        return n

    def on_bar(self, ctx):
        desired = self._desired_exposure(ctx)
        letters = {"state": self._state_letter}
        # risk-off: exit everything (frozen E/C, or trend state 0)
        if desired <= 0 and (ctx.positions or self._book):
            self._exit_all(ctx, set())
            ctx.set_targets({}, note="risk_off")
            self._book = False
            self._prev_exp = 0.0
        if not self._init:
            self._init = True
            if desired > 0:
                self._rebalance(ctx, desired, "initial")
        elif desired > 0 and self._prev_exp <= 0:
            # trend-mode re-entry (frozen mode re-enters only on month-end, like the platform)
            if self.state_mode != "frozen":
                self._rebalance(ctx, desired, "trend_reentry")
        self._prev_exp = desired
        if self.record:
            self.audit_days.append(dict(
                date=str(ctx.date), state=getattr(self, "_state_letter", "?"),
                desired=desired, nav=float(ctx.nav), cash=float(ctx.cash),
                market_value=float(ctx.nav - ctx.cash),
                n_pos=len(ctx.positions), book=bool(self._book),
                positions=[(str(c), int(p.shares)) for c, p in ctx.positions.items()]))

    def on_rebalance(self, ctx):
        desired = self._desired_exposure(ctx)
        if desired > 0:
            self._rebalance(ctx, desired, "scheduled")

    def _rebalance(self, ctx, exposure: float, reason: str):
        day = str(ctx.date)
        if reason == "scheduled" and self._last_reb == day:
            return
        feats = snapshot(ctx, min_amount=self.min_amount, max_vol=self.max_vol,
                         min_price=self.min_price, score_mode=self.score_mode)
        row = dict(date=day, state=getattr(self, "_state_letter", "?"), reason=reason,
                   exposure=exposure, n_candidates=0, n_selected=0, n_dropped=0,
                   n_truncated=0, note="")
        if not feats.ok:
            self._exit_all(ctx, set())
            ctx.set_targets({})
            self._book = False
            row["note"] = "no_features"
        else:
            weights, info = self._select(ctx, feats, exposure, float(ctx.nav), {})
            row.update(n_candidates=info["n_candidates"], n_selected=info["n_selected"],
                       n_dropped=info.get("n_dropped", 0),
                       n_truncated=info.get("n_truncated", 0))
            if not weights:
                self._exit_all(ctx, set())
                ctx.set_targets({})
                self._book = False
                row["note"] = "no_candidate"
            else:
                self._exit_all(ctx, set(weights))
                ctx.set_targets(weights, note=f"{reason}_n{len(weights)}")
                self._book = True
                row["note"] = "target_set"
        if self.record:
            self.audit_reb.append(row)
        self._last_reb = day
