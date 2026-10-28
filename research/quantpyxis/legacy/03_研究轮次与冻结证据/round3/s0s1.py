# -*- coding: utf-8 -*-
"""Local port of the SuperMind platform S0/S1 strategies (winrate round 3, 2026-10-20).

Platform sources (READ-ONLY handoff pack):
  SuperMind_S0_月度基线_胜率诊断_v03.py      (VARIANT='R2': monthly baseline + diagnostics)
  SuperMind_S1_整手候选补位_待验证_v03.py    (S0 + full-ranked whole-lot affordability backfill)

What this file is
-----------------
A literal re-implementation of the *signal rules* of those two platform scripts on top of
the independent local engine (qrun_1416/engine, T close signal -> T+1 open fill, 100-share
lots, real fees, qfq total-return marks).  It is NOT the platform: data vendor, universe
construction, volume cap and round-lot fill amounts differ (see REPRODUCTION.md).

Rule map (platform line references are to SuperMind_S0_..._v03.py)
  * state_at(): frozen T-close regime tokens RUN_TEXT (B / N / E / C)          -> ctx.regime
  * exposure  .60 if B else .25 if N else 0                                   -> EXPOSURE
  * selection():
      - universe: main board (sh 600/601/603/605) + ChiNext (sz 000/001/002/003/300/301)
      - exclude ST / paused; >=230 of 260 qfq bars; 260-row window ffill
      - 12-1 momentum  x[-22]/x[-253]-1 ; 6-1 momentum x[-22]/x[-127]-1
      - 60d annualised vol; vol cap .55 ; raw close >= 2 ; 60d mean amount >= 2e8
      - trend  close > MA60 > MA120
      - score = .5*rank(RAS) + .3*rank(M6) + .2*rank(1/VOL)   (percent ranks)
      - candidates sorted by (-score, code)
      - retention buffer: currently held names with candidate rank < 20, best first, <=10
      - then fill from the ranked candidates up to 10
      - S0: affordability filter AFTER truncation: raw*100 <= exposure*nav/10*1.02
      - S1: affordability (100 lots + estimated buy fees) checked over the FULL ranking,
            then retention buffer + backfill to 10 (platform pick_affordable_ranked)
  * monthly rebalance = last trading day of the month (engine rebalance="M")
  * first trading day of the window: initial target at that day's close (platform `initial`)
  * state E/C -> target {} on the same close (platform daily_exit)

Exit fidelity note
------------------
The engine's *target book* deliberately tolerates a one-lot residual when trimming/selling
(engine/API.md "tolerance"), so a book-only exit would leave every single-lot position
behind.  At 100k / 25% exposure a typical position IS one lot, so book-only exits accumulate
dust holdings (observed before this fix: 30 positions vs a 10-name limit, avg exposure 57%
against a 25% target).  The platform calls `order_target(s, 0)`, which sells the whole
holding.  Exits therefore use an explicit `ctx.sell(code, shares=<all>)` for every name not
in the new target set (same next-open slot, executed before book buys), plus
`set_targets({})` in bear/crash states.  Entries/trims still go through the engine book.

The S1b variant is a diagnostic only: identical to S1 but re-uses S0's affordability test
(`raw*100 <= budget*1.02`, no fee estimate) so the S0->S1 delta can be decomposed into
"backfill" vs "stricter affordability test".  It is not a new platform candidate.

Audit instrumentation
---------------------
on_bar() records one row per trading day (NAV, cash, holdings, whether each holding had a
bar today, pending-book flag, regime) and one row per rebalance (candidates, truncation,
dropped-unaffordable counts, exposure).  The runner turns these into DAILY_AUDIT.csv,
POSITIONS.csv, CASH_REASONS.csv and RISK_FLAT_AUDIT.csv.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ABS_REPO = Path(r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416")
if not (_ABS_REPO / "engine").exists():  # portable fallback: <workspace>/qrun_1416
    _ABS_REPO = Path(__file__).resolve().parents[2] / "qrun_1416"
REPO = _ABS_REPO
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.core import Strategy  # noqa: E402

TOP_N = 10                    # platform top_n / 持股上限
HOLD_BUFFER = 2 * TOP_N       # platform retention buffer: rank < 20 (R2), rank < 2*top_n (S1)

# platform board prefix filter (main board + ChiNext only; no STAR/BSE/B shares)
_SH_PREFIX = ("600", "601", "603", "605")
_SZ_PREFIX = ("000", "001", "002", "003", "300", "301")


def board_ok(code) -> bool:
    c = str(code)
    return c.startswith(_SH_PREFIX) or c.startswith(_SZ_PREFIX)


class Features:
    """One cross-sectional feature snapshot at a signal close (no future rows)."""

    __slots__ = ("ok", "cands", "raw", "n_hist", "n_base", "n_universe")

    def __init__(self):
        self.ok = False
        self.cands: list = []
        self.raw = pd.Series(dtype=float)
        self.n_hist = 0
        self.n_base = 0
        self.n_universe = 0


def snapshot(ctx, min_amount: float = 2.0e8, max_vol: float = 0.55,
             min_price: float = 2.0, window: int = 260) -> Features:
    """Platform selection() feature block, vectorised over the panel."""
    f = Features()
    universe = [c for c in ctx.universe(tradable=True, exclude_st=True) if board_ok(c)]
    f.n_universe = len(universe)
    if len(universe) < 10:
        return f
    uni_set = set(universe)

    cols, mat = ctx.history_matrix("qfq_close", window)
    cols = list(cols)
    keep = [i for i, c in enumerate(cols) if c in uni_set]
    if len(keep) < 10:
        return f
    sel_cols = [cols[i] for i in keep]
    m = np.asarray(mat, dtype=np.float64)[:, keep]

    nvalid = np.isfinite(m).sum(axis=0)
    df = pd.DataFrame(m, columns=sel_cols).ffill()
    last_bar = np.isfinite(m[-1, :])
    close = df.iloc[-1]
    ma60 = df.iloc[-60:].mean()
    ma120 = df.iloc[-120:].mean()
    ret = df.pct_change()
    rvol = ret.iloc[-61:].std() * math.sqrt(252.0)
    mom12 = df.iloc[-22] / df.iloc[-253] - 1.0
    mom6 = df.iloc[-22] / df.iloc[-127] - 1.0

    raw = ctx.latest("close").reindex(sel_cols)
    amt_cols, amt_mat = ctx.history_matrix("amount", 60)
    amt60 = pd.DataFrame(np.asarray(amt_mat, dtype=np.float64)[:, keep],
                         columns=sel_cols).mean(axis=0)

    x_m253 = df.iloc[-253].to_numpy(dtype=float)
    x_m127 = df.iloc[-127].to_numpy(dtype=float)
    base = (pd.Series(nvalid, index=sel_cols) >= 230) & pd.Series(last_bar, index=sel_cols)
    base &= pd.Series(np.isfinite(x_m253), index=sel_cols)
    base &= pd.Series(np.isfinite(x_m127), index=sel_cols)
    base &= rvol.notna() & np.isfinite(rvol.to_numpy()) & (rvol > 0)
    base &= mom12.notna() & mom6.notna() & raw.notna() & np.isfinite(raw.to_numpy())

    liquid = amt60 >= min_amount
    trend = (close > ma60) & (ma60 > ma120)
    price_ok = raw >= min_price
    ok = base & liquid & trend & price_ok & (rvol <= max_vol)

    ras = (mom12 / rvol).where(base)
    m6 = mom6.where(base)
    inv = (1.0 / rvol).where(base)

    def _rk(s: pd.Series) -> pd.Series:
        return s.rank(pct=True, na_option="keep")

    score = 0.5 * _rk(ras) + 0.3 * _rk(m6) + 0.2 * _rk(inv)
    sel = score[ok].dropna()
    f.n_hist = int(base.sum())
    f.n_base = int(sel.shape[0])
    f.raw = raw
    f.cands = sorted(sel.index, key=lambda c: (-float(sel[c]), str(c)))
    f.ok = True
    return f


class PlatformPort(Strategy):
    """Port of platform S0 (mode='S0'), S1 ('S1') or the S1-decomposition control ('S1b')."""

    EXPOSURE = {"B": 0.60, "N": 0.25, "E": 0.0, "C": 0.0}

    def __init__(self, mode: str = "S0", name: str | None = None, top_n: int = TOP_N,
                 min_amount: float = 2.0e8, max_vol: float = 0.55,
                 min_price: float = 2.0, record_days: bool = True):
        if mode not in ("S0", "S1", "S1b"):
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.top_n = int(top_n)
        self.min_amount = float(min_amount)
        self.max_vol = float(max_vol)
        self.min_price = float(min_price)
        self.record_days = bool(record_days)
        self.name = name or f"Platform-{mode}"
        self.warmup = 280
        self._initialised = False
        self._book_active = False
        self._last_rebalance_date = None
        self._last_weights: dict = {}
        self.audit_days: list = []
        self.audit_rebalances: list = []

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _state(ctx) -> str:
        st = ctx.regime
        return st if st in ("B", "N", "E", "C") else "N"

    def _exposure(self, state: str) -> float:
        return float(self.EXPOSURE.get(state, 0.0))

    @staticmethod
    def _explicit_exits(ctx, keep: set) -> int:
        """Full exits for names outside `keep` (platform order_target(code, 0)).

        `fraction=1.0` (not an integer share count) is required so that odd lots left by
        corporate actions are sold too; the engine allows odd-lot full exits.
        """
        n = 0
        for c, p in list(ctx.positions.items()):
            if str(c) in keep:
                continue
            if p.shares <= 0:
                continue
            ctx.sell(c, fraction=1.0, note="exit_not_in_target")
            n += 1
        return n

    # ------------------------------------------------------------- lifecycle
    def on_bar(self, ctx):
        state = self._state(ctx)
        # (1) platform: if state in E/C and (held or book) -> target {}
        if state in ("E", "C") and (ctx.positions or self._book_active):
            self._explicit_exits(ctx, set())
            ctx.set_targets({}, note=f"daily_exit_{state}")
            self._book_active = False
            ctx.add_event("daily_exit", state=state, n_pos=len(ctx.positions))
        # (2) platform `initial`: first trading day of the window acts like a rebalance
        if not self._initialised:
            self._initialised = True
            if state in ("B", "N"):
                self._rebalance(ctx, state, "initial")
        # (3) daily audit record (post-execution, close marks)
        if self.record_days:
            self._record_day(ctx, state)

    def on_rebalance(self, ctx):
        state = self._state(ctx)
        if state in ("B", "N"):
            self._rebalance(ctx, state, "scheduled")

    # ------------------------------------------------------------- selection
    def _pick(self, ctx, feats: Features, exposure: float, nav: float) -> tuple[list, dict]:
        cands = feats.cands
        rank = {c: i for i, c in enumerate(cands)}
        held = [c for c in ctx.positions if c in rank]
        held.sort(key=lambda c: rank[c])
        budget = exposure * nav / self.top_n
        info = dict(n_candidates=len(cands), n_held_ranked=len(held),
                    budget_per_name=budget, nav=nav)
        detail = []   # per-code audit: rank, price, one-lot cost, decision

        def _one_lot(c):
            p = float(feats.raw.get(c, np.nan))
            if not math.isfinite(p) or p <= 0:
                return np.nan, np.nan
            value = p * 100.0
            cost = value * 1.001 + max(5.0, value * 1.001 * 0.00025) + value * 1.001 * 0.00001
            return p, cost

        if self.mode == "S0":
            # platform R2: truncate to top_n FIRST, then drop unaffordable (no backfill)
            selected = [c for c in held if rank[c] < HOLD_BUFFER][: self.top_n]
            for c in cands:
                if len(selected) >= self.top_n:
                    break
                if c not in selected:
                    selected.append(c)
            n_trunc = len(selected)
            kept = []
            for c in selected:
                p, cost = _one_lot(c)
                affordable = bool(math.isfinite(p) and p * 100.0 <= budget * 1.02)
                if affordable:
                    kept.append(c)
                    decision = "chosen"
                else:
                    decision = "dropped_unaffordable"
                detail.append(dict(code=c, cand_rank=rank[c], raw_price=p,
                                   one_lot_value=p * 100.0 if math.isfinite(p) else np.nan,
                                   one_lot_cost=cost, affordable=affordable,
                                   decision=decision))
            selected = kept
            info.update(n_truncated=n_trunc, n_unaffordable=n_trunc - len(selected))
        else:
            if self.mode == "S1":
                def feasible(c):
                    p = float(feats.raw.get(c, np.nan))
                    if not math.isfinite(p) or p <= 0:
                        return False
                    value = p * 100.0 * 1.001
                    est_cost = value + max(5.0, value * 0.00025) + value * 0.00001
                    return est_cost <= budget
            else:  # S1b: S0's affordability test, but backfilled over the full ranking
                def feasible(c):
                    p = float(feats.raw.get(c, np.nan))
                    return bool(math.isfinite(p) and p * 100.0 <= budget * 1.02)
            feas = {c for c in cands if feasible(c)}
            selected = [c for c in held if c in feas and rank[c] < HOLD_BUFFER][: self.top_n]
            for c in cands:
                if len(selected) >= self.top_n:
                    break
                if c in feas and c not in selected:
                    selected.append(c)
            for c in selected:
                p, cost = _one_lot(c)
                detail.append(dict(code=c, cand_rank=rank[c], raw_price=p,
                                   one_lot_value=p * 100.0 if math.isfinite(p) else np.nan,
                                   one_lot_cost=cost, affordable=True,
                                   decision="chosen"))
            info.update(n_feasible=len(feas))
        info.update(n_selected=len(selected), exposure=exposure, detail=detail)
        return selected, info

    def _rebalance(self, ctx, state: str, reason: str):
        day = str(ctx.date)
        if reason == "scheduled" and self._last_rebalance_date == day:
            return
        exposure = self._exposure(state)
        row = dict(date=day, state=state, reason=reason, exposure=exposure,
                   n_candidates=0, n_selected=0, n_unaffordable=0, n_truncated=0,
                   note="", budget_per_name=np.nan, nav=np.nan, detail=[])
        if exposure <= 0:
            self._explicit_exits(ctx, set())
            ctx.set_targets({}, note=f"cash_{reason}_{state}")
            self._book_active = False
            self._last_weights = {}
            row["note"] = "exposure_zero"
            self.audit_rebalances.append(row)
            self._last_rebalance_date = day
            return
        feats = snapshot(ctx, min_amount=self.min_amount, max_vol=self.max_vol,
                         min_price=self.min_price)
        if not feats.ok:
            self._explicit_exits(ctx, set())
            ctx.set_targets({}, note=f"no_features_{reason}")
            self._book_active = False
            self._last_weights = {}
            row["note"] = "no_features"
            self.audit_rebalances.append(row)
            self._last_rebalance_date = day
            ctx.add_event("no_features", state=state, reason=reason)
            return
        nav = float(ctx.nav)
        selected, info = self._pick(ctx, feats, exposure, nav)
        row.update(n_candidates=info.get("n_candidates", 0),
                   n_selected=len(selected),
                   n_unaffordable=info.get("n_unaffordable", 0),
                   n_truncated=info.get("n_truncated", 0),
                   budget_per_name=info.get("budget_per_name", np.nan),
                   nav=nav,
                   detail=info.get("detail", []))
        if not selected:
            self._explicit_exits(ctx, set())
            ctx.set_targets({}, note=f"empty_{reason}_{state}")
            self._book_active = False
            self._last_weights = {}
            row["note"] = "no_affordable_candidate"
        else:
            row["n_explicit_exits"] = self._explicit_exits(ctx, set(selected))
            w = exposure / len(selected)
            ctx.set_targets({c: w for c in selected},
                            note=f"{state}_{reason}_n{len(selected)}")
            self._book_active = True
            self._last_weights = {c: w for c in selected}
            row["note"] = "target_set"
        self.audit_rebalances.append(row)
        self._last_rebalance_date = day
        ctx.add_event("rebalance", state=state, reason=reason, exposure=exposure,
                      n_candidates=int(row["n_candidates"]), n_selected=len(selected),
                      n_unaffordable=int(row["n_unaffordable"]),
                      n_truncated=int(row["n_truncated"]), note=row["note"])

    def _record_day(self, ctx, state: str):
        held = []
        for c, p in sorted(ctx.positions.items()):
            b = ctx.bar(c)
            close = None if b is None else b.get("close")
            held.append(dict(
                code=str(c), shares=int(p.shares), value=float(p.value()),
                has_bar=bool(close is not None),          # a usable close today?
                close=close,
                qfq_close=None if b is None else b.get("qfq_close"),
            ))
        self.audit_days.append(dict(
            date=str(ctx.date), state=state, nav=float(ctx.nav), cash=float(ctx.cash),
            market_value=float(ctx.nav - ctx.cash), n_pos=len(ctx.positions),
            pending=bool(self._book_active), positions=held,
            last_rebalance=self._last_rebalance_date,
            last_weights=dict(self._last_weights),
        ))
