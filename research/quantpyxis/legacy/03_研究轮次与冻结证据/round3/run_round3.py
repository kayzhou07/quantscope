# -*- coding: utf-8 -*-
"""Round 3: local S0/S1 backtest over 2014-07-01..2023-12-29 + full audit deliverables.

Runs
----
  S0        platform R2 monthly baseline (affordability filter AFTER top-10 truncation)
  S1        platform S1 candidate (full-ranked whole-lot affordability + backfill)
  S1b       diagnostic control: S1's backfill with S0's affordability test
  S0_20bp   S0 at 20bp slippage (the platform script literally uses PriceSlippage(0.002))
  S1_stress S1 with doubled commission + doubled slippage

Outputs (round3/out/)
---------------------
  {tag}_nav.csv, {tag}_trades.csv, {tag}_orders.csv, {tag}_events.csv, {tag}_result.json
  DAILY_AUDIT.csv, POSITIONS.csv, TRADES.csv, ORDERS.csv, CASH_REASONS.csv,
  RISK_FLAT_AUDIT.csv, REBALANCES.csv, metrics.json, summary.csv, yearly.csv,
  segments.csv, rolling.csv, cost_stress.csv, run.log

Window: 2014-07-01 .. 2023-12-29 (same as the platform R0-R4 research window).
Capital 100,000 CNY; daily; long only; main board + ChiNext; cash allowed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s0s1  # noqa: E402

REPO = s0s1.REPO
from engine.api import run_backtest, save_result_json  # noqa: E402

OUT = HERE / "out"
START, END = "2014-07-01", "2023-12-29"
CAP = 100_000.0
PLATFORM_R2 = {"total_return": 0.8400, "max_drawdown": 0.1116}
PLATFORM_R0 = {"total_return": 0.6358, "max_drawdown": 0.1433}

STATE_MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}
SEGMENTS = [("2014-07-01", "2018-12-31"), ("2019-01-01", "2023-12-29")]
WIN_EPS = 1e-6


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def load_regime():
    reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    letters = {d: STATE_MAP[s] for d, s in zip(reg["date"], reg["state"])}
    states = dict(zip(reg["date"], reg["state"]))
    return letters, states


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# one backtest
# --------------------------------------------------------------------------- #
def run_variant(tag, mode, labels, *, slippage_bps=10.0, commission_bps=2.5,
                extra_name=None):
    strat = s0s1.PlatformPort(mode=mode, name=extra_name or f"{mode}_{tag}")
    t0 = time.time()
    res = run_backtest(
        strat, start=START, end=END, capital=CAP, data_dir=REPO / "data",
        warmup_days=340, rebalance="M", benchmark=["sh000300", "sz399006"],
        slippage_bps=slippage_bps, commission_bps=commission_bps,
        min_commission=5.0, regime_labels=labels, name=strat.name,
    )
    elapsed = time.time() - t0
    OUT.mkdir(parents=True, exist_ok=True)
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    nav.to_csv(OUT / f"{tag}_nav.csv")
    res["trades"].to_csv(OUT / f"{tag}_trades.csv", index=False)
    res["orders"].to_csv(OUT / f"{tag}_orders.csv", index=False)
    res["events"].to_csv(OUT / f"{tag}_events.csv", index=False)
    save_result_json(res, OUT / f"{tag}_result.json", include_nav=True, include_trades=True)
    s = res["summary"]
    print(f"[run] {tag:10s} mode={mode} {elapsed:5.1f}s ret={s['total_return']:+.4f} "
          f"dd={s['max_drawdown']:.4f} trades={s['trade_count']} "
          f"fees={s['total_fees']:.1f} exp={s['avg_exposure']:.3f} "
          f"cash_days={s['all_cash_days']}", flush=True)
    return res, strat, elapsed


# --------------------------------------------------------------------------- #
# daily audit
# --------------------------------------------------------------------------- #
def build_daily(res, strat, states):
    nav = res["nav"].copy()
    nav.index = nav.index.astype(str)
    df = pd.DataFrame(index=nav.index)
    df["nav"] = nav["nav"].astype(float)
    df["cash"] = nav["cash"].astype(float)
    df["market_value"] = nav["market_value"].astype(float)
    df["exposure"] = nav["exposure"].astype(float)
    df["n_positions"] = nav["n_positions"].astype(int)
    df["regime_letter"] = nav["regime"].astype(str)
    df["regime_state"] = [states.get(d, "MISSING") for d in df.index]
    df["bench_sh000300"] = nav.get("bench_sh000300", np.nan)
    df["bench_sz399006"] = nav.get("bench_sz399006", np.nan)
    df["fees"] = nav["fees"].astype(float)
    df["traded_amount"] = nav["traded_amount"].astype(float)

    # daily P&L relative to previous close (first day vs 100k, like the platform audit)
    prev = df["nav"].shift(1)
    prev.iloc[0] = CAP
    df["prev_nav"] = prev
    df["daily_pnl"] = df["nav"] - df["prev_nav"]
    df["daily_ret"] = df["daily_pnl"] / df["prev_nav"]
    df["positive"] = df["daily_pnl"] > WIN_EPS
    df["negative"] = df["daily_pnl"] < -WIN_EPS
    df["flat"] = ~(df["positive"] | df["negative"])

    prev_pos = df["n_positions"].shift(1).fillna(0).astype(int)
    df["prev_n_positions"] = prev_pos
    df["risk_day"] = (df["n_positions"] > 0) | (prev_pos > 0)
    df["pure_cash_flat"] = (~df["risk_day"]) & df["flat"]

    # pending-book / last-rebalance metadata recorded by the strategy
    days = pd.DataFrame([{k: v for k, v in r.items() if k != "positions"}
                         for r in strat.audit_days]).set_index("date")
    for col in ("pending", "last_rebalance"):
        if col in days:
            df[col] = days[col].reindex(df.index)
    df["pending"] = df.get("pending", pd.Series(False, index=df.index)).fillna(False).astype(bool)

    rb = pd.DataFrame(strat.audit_rebalances)
    if not rb.empty:
        rb = rb.set_index("date")
        for col in ("reason", "exposure", "n_candidates", "n_selected",
                    "n_unaffordable", "n_truncated", "note"):
            df["rb_" + col] = rb[col].reindex(df.index)
        df["is_rebalance_signal"] = df.index.isin(rb.index)
    else:
        df["is_rebalance_signal"] = False

    # blocked/order events per day
    ev = res["events"]
    blocked_sell = ev[ev["type"] == "target_blocked"]
    if not blocked_sell.empty:
        blocked_sell = blocked_sell.copy()
        blocked_sell["date"] = blocked_sell["date"].astype(str)
        ds = blocked_sell[blocked_sell.get("side") == "sell"].groupby("date").size()
        db = blocked_sell[blocked_sell.get("side") == "buy"].groupby("date").size()
        df["blocked_sells"] = ds.reindex(df.index).fillna(0).astype(int)
        df["blocked_buys"] = db.reindex(df.index).fillna(0).astype(int)
    else:
        df["blocked_sells"] = 0
        df["blocked_buys"] = 0

    df["cash_reason"] = cash_reasons(df)
    df["pending_targets"] = [
        json.dumps(rec.get("last_weights", {}), ensure_ascii=False, sort_keys=True)
        if rec.get("pending") else ""
        for rec in strat.audit_days
    ]
    return df


def cash_reasons(df: pd.DataFrame) -> pd.Series:
    """Mutually exclusive attribution of the cash balance, handoff priority order."""
    reasons = []
    for d, r in df.iterrows():
        state = r["regime_letter"]
        cash = float(r["cash"])
        nav = float(r["nav"])
        if state in ("E", "C") and r["market_value"] <= 0.01:
            reasons.append("state_forbidden")
            continue
        if cash <= max(1.0, 0.01 * nav):
            reasons.append("fully_invested")
            continue
        if bool(r.get("is_rebalance_signal", False)):
            note = str(r.get("rb_note", ""))
            if note == "no_features":
                reasons.append("no_valid_data")
                continue
            if float(r.get("rb_n_selected", 0) or 0) <= 0:
                reasons.append("no_affordable_candidate")
                continue
            if float(r.get("rb_n_unaffordable", 0) or 0) > 0:
                reasons.append("lot_unaffordable")
                continue
        if bool(r.get("pending", False)) and r["market_value"] <= 0.01:
            reasons.append("pending_fill")
            continue
        target = s0s1.PlatformPort.EXPOSURE.get(state, 0.0)
        design_cash = (1.0 - target) * nav
        if r["market_value"] <= 0.01 and state in ("B", "N"):
            reasons.append("off_window_no_entry")
        elif cash <= design_cash + max(1.0, 0.05 * nav):
            reasons.append("design_cap_or_lot_rounding")
        else:
            reasons.append("off_window_cash_above_cap")
        reasons[-1] = reasons[-1]
    return pd.Series(reasons, index=df.index)


def positions_table(strat) -> pd.DataFrame:
    rows = []
    for rec in strat.audit_days:
        for p in rec["positions"]:
            rows.append(dict(date=rec["date"], code=p["code"], shares=p["shares"],
                             value=p["value"], has_bar=p["has_bar"],
                             close=p["close"], qfq_close=p["qfq_close"]))
    return pd.DataFrame(rows)


def orders_table(res) -> pd.DataFrame:
    """Unified order ledger: explicit orders + target-book blocked legs."""
    parts = []
    od = res["orders"].copy()
    if not od.empty:
        od["source"] = "explicit"
        od["date"] = od["created_date"].astype(str)
        parts.append(od[["date", "code", "side", "requested", "filled", "status",
                         "reason", "note", "source"]])
    ev = res["events"]
    blk = ev[ev["type"] == "target_blocked"].copy()
    if not blk.empty:
        blk["date"] = blk["date"].astype(str)
        blk["source"] = "target_book_blocked"
        blk["requested"] = np.nan
        blk["filled"] = 0.0
        blk["status"] = "blocked"
        blk["note"] = blk.get("signal_date", "").astype(str)
        parts.append(blk[["date", "code", "side", "requested", "filled", "status",
                          "reason", "note", "source"]])
    if not parts:
        return pd.DataFrame(columns=["date", "code", "side", "requested", "filled",
                                     "status", "reason", "note", "source"])
    out = pd.concat(parts, ignore_index=True)
    out["status"] = out["status"].astype(str)
    out["reason"] = out["reason"].astype(str)
    return out.sort_values(["date", "source", "code"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# risk-flat audit
# --------------------------------------------------------------------------- #
def risk_flat_audit(df: pd.DataFrame, pos: pd.DataFrame) -> pd.DataFrame:
    flat_days = df[df["risk_day"] & df["flat"]].index
    cols = ["date", "nav", "n_positions", "prev_n_positions", "cash",
            "n_held", "n_no_bar", "sum_daily_pnl", "classification", "detail"]
    if len(flat_days) == 0:
        return pd.DataFrame(columns=cols)
    # Reindex to every date with a position snapshot so that a day whose ONLY holding is
    # suspended (no usable close) still appears instead of looking like an exit day.
    all_dates = pd.Index(sorted(pos["date"].unique())) if len(pos) else flat_days
    wide_close = pos.pivot_table(index="date", columns="code", values="close",
                                 aggfunc="last").reindex(all_dates)
    wide_shares = pos.pivot_table(index="date", columns="code", values="shares",
                                  aggfunc="last").reindex(all_dates)
    prev_close = wide_close.shift(1)
    rows = []
    for d in flat_days:
        base = dict(date=d, nav=float(df.loc[d, "nav"]),
                    n_positions=int(df.loc[d, "n_positions"]),
                    prev_n_positions=int(df.loc[d, "prev_n_positions"]),
                    cash=float(df.loc[d, "cash"]))
        if d not in wide_close.index:
            # risk day caused by the PREVIOUS close's holdings; today is the exit day and the
            # NAV did not move between the last mark and the sale fill.
            cls = "exit_day_flat_nav" if int(df.loc[d, "prev_n_positions"]) > 0 \
                else "no_position_snapshot"
            rows.append(dict(**base, n_held=0, n_no_bar=0, sum_daily_pnl=np.nan,
                             classification=cls,
                             detail="no current holdings; risk day inherited from previous close"))
            continue
        cur_c = wide_close.loc[d]
        cur_s = wide_shares.loc[d]
        pc = prev_close.loc[d] if d in prev_close.index else pd.Series(np.nan, index=cur_c.index)
        # held names come from SHARES (a suspended name still has shares, no close)
        held = cur_s.dropna()[cur_s.dropna() > 0].index
        n_no_bar = int(sum(not np.isfinite(float(cur_c.get(code, np.nan))) for code in held))
        contrib = {}
        for code in held:
            c0 = pc.get(code, np.nan)
            c1 = cur_c.get(code, np.nan)
            sh = float(cur_s.get(code, 0) or 0)
            if not np.isfinite(c1) or not np.isfinite(c0) or sh <= 0:
                contrib[code] = np.nan
            else:
                contrib[code] = (c1 - c0) * sh
        vals = pd.Series(contrib)
        total = float(vals.sum(min_count=1)) if vals.notna().any() else np.nan
        moved = (vals.abs() > 1e-9) & vals.notna()
        if len(held) == 0:
            cls, detail = "no_holdings", ""
        elif n_no_bar == len(held):
            cls, detail = "all_held_suspended", ",".join(map(str, held))
        elif not vals.notna().any():
            cls, detail = "no_comparable_prices", ""
        elif not moved.any():
            cls = "partial_suspension_prices_unchanged" if n_no_bar else "prices_all_unchanged"
            detail = f"n_no_bar={n_no_bar}"
        elif np.isfinite(total) and abs(total) <= 1.0:
            cls, detail = "offsetting_moves", json.dumps(
                {str(k): round(float(v), 2) for k, v in vals.dropna().items()}, ensure_ascii=False)
        else:
            cls, detail = "unexplained_by_close_moves", json.dumps(
                {str(k): round(float(v), 2) for k, v in vals.dropna().items()}, ensure_ascii=False)
        rows.append(dict(**base, n_held=len(held), n_no_bar=n_no_bar,
                         sum_daily_pnl=float(total) if np.isfinite(total) else np.nan,
                         classification=cls, detail=detail))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def max_drawdown(nav: pd.Series):
    peak = nav.cummax()
    dd = nav / peak - 1.0
    trough = dd.idxmin()
    peak_date = nav.loc[:trough].idxmax()
    return float(dd.min()), str(peak_date), str(trough)


def segment_stats(df: pd.DataFrame, start: str, end: str) -> dict:
    sub = df.loc[(df.index >= start) & (df.index <= end)]
    if sub.empty:
        return {}
    nav = sub["nav"]
    ret = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    dd, pk, tr = max_drawdown(nav)
    return dict(start=str(sub.index[0]), end=str(sub.index[-1]), days=int(len(sub)),
                total_return=ret, max_drawdown=dd, dd_peak=pk, dd_trough=tr,
                avg_exposure=float(sub["exposure"].mean()),
                all_cash_days=int((sub["market_value"] <= 0.01).sum()),
                worst_day=float(sub["daily_ret"].min()),
                best_day=float(sub["daily_ret"].max()),
                flat_days=int(sub["flat"].sum()),
                risk_flat_days=int((sub["risk_day"] & sub["flat"]).sum()),
                trades=int((sub.get("fees", 0) > 0).sum()))


def rolling_returns(nav: pd.Series, window: int) -> pd.Series:
    ret = np.log1p(nav.pct_change().fillna(0.0))
    roll = np.expm1(ret.rolling(window, min_periods=window).sum())
    return roll.dropna()


def metrics_block(tag, res, df, pos, res_strat, elapsed):
    nav = df["nav"]
    days = len(df)
    pos_d = int(df["positive"].sum())
    neg_d = int(df["negative"].sum())
    flat_d = int(df["flat"].sum())
    risk_d = int(df["risk_day"].sum())
    risk_pos = int((df["risk_day"] & df["positive"]).sum())
    nonzero = pos_d + neg_d
    win_sum = float(df.loc[df["positive"], "daily_pnl"].sum())
    loss_sum = float(df.loc[df["negative"], "daily_pnl"].sum())
    total_ret = float(nav.iloc[-1] / CAP - 1.0)
    dd, pk, tr = max_drawdown(nav)
    m = res["metrics"]
    out = {
        "tag": tag, "name": res["name"], "start": str(df.index[0]), "end": str(df.index[-1]),
        "trading_days": days, "final_nav": float(nav.iloc[-1]), "total_return": total_ret,
        "max_drawdown": dd, "dd_peak": pk, "dd_trough": tr,
        "cagr": float((nav.iloc[-1] / CAP) ** (252.0 / days) - 1.0),
        "ann_vol": float(df["daily_ret"].std() * math.sqrt(252.0)),
        "sharpe": float(df["daily_ret"].mean() / df["daily_ret"].std() * math.sqrt(252.0))
        if df["daily_ret"].std() > 0 else None,
        # --- win-rate block (three denominators, all reported, none cherry-picked)
        "positive_days": pos_d, "negative_days": neg_d, "flat_days": flat_d,
        "risk_days": risk_d, "risk_positive_days": risk_pos,
        "winrate_all": pos_d / days if days else None,
        "winrate_risk": risk_pos / risk_d if risk_d else None,
        "winrate_nonzero": pos_d / nonzero if nonzero else None,
        "flat_day_share": flat_d / days if days else None,
        "pure_cash_flat_days": int(df["pure_cash_flat"].sum()),
        "mean_win_day": win_sum / pos_d if pos_d else None,
        "mean_loss_day": loss_sum / neg_d if neg_d else None,
        "daily_profit_factor": win_sum / -loss_sum if loss_sum < 0 else None,
        "daily_pnl_ratio_mean": (win_sum / pos_d) / abs(loss_sum / neg_d)
        if pos_d and neg_d and loss_sum < 0 else None,
        # --- exposure / turnover / costs
        "avg_exposure": float(df["exposure"].mean()),
        "end_exposure": float(df["exposure"].iloc[-1]),
        "all_cash_days": int((df["market_value"] <= 0.01).sum()),
        "avg_positions": float(df["n_positions"].mean()),
        "max_positions": int(df["n_positions"].max()),
        "trade_count": int(len(res["trades"])),
        "total_fees": float(res["total_fees"]),
        "fees_pct_of_capital": float(res["total_fees"] / CAP),
        "turnover_annual": float(m.get("turnover_annual", np.nan)),
        "total_slippage_cost": float(res.get("total_slippage_cost", np.nan)),
        # --- FIFO closed-trade block (engine-computed)
        "fifo_win_rate": float(m.get("fifo_win_rate", np.nan)),
        "fifo_win_count": int(m.get("fifo_win_count", 0)),
        "fifo_loss_count": int(m.get("fifo_loss_count", 0)),
        "fifo_avg_win": float(m.get("fifo_avg_win", np.nan)),
        "fifo_avg_loss": float(m.get("fifo_avg_loss", np.nan)),
        "fifo_profit_factor": float(m.get("fifo_profit_factor", np.nan)),
        "fifo_realized_pnl": float(m.get("fifo_realized_pnl", np.nan)),
        "fifo_unrealized_pnl": float(m.get("fifo_unrealized_pnl", np.nan)),
        "fifo_open_cost": float(m.get("fifo_open_cost", np.nan)),
        "open_positions_at_end": int(len(res["holdings_final"])),
        "pnl_reconciliation_gap": float(m.get("pnl_reconciliation_gap", np.nan)),
        # --- audit table summaries
        "risk_flat_days": int((df["risk_day"] & df["flat"]).sum()),
        "unaffordable_drops_total": int(df.get("rb_n_unaffordable", pd.Series(dtype=float)).fillna(0).sum()),
        "rebalance_count": int(df.get("is_rebalance_signal", pd.Series(False)).sum()),
        "blocked_sell_days": int((df["blocked_sells"] > 0).sum()),
        "blocked_buy_days": int((df["blocked_buys"] > 0).sum()),
        "bench_sh000300_ret": float(nav.iloc[-1] * 0 + (df["bench_sh000300"].iloc[-1] / CAP - 1.0))
        if "bench_sh000300" in df else None,
        "bench_sz399006_ret": float(df["bench_sz399006"].iloc[-1] / CAP - 1.0)
        if "bench_sz399006" in df else None,
        "elapsed_s": round(elapsed, 1),
        "cash_reason_counts": df["cash_reason"].value_counts().to_dict(),
    }
    return out


def yearly_table(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["year"] = [d[:4] for d in df.index]
    rows = []
    for y, sub in df.groupby("year"):
        # chain from the previous year-end NAV (first year starts at 100k)
        start_nav = float(sub["prev_nav"].iloc[0])
        ret = float(sub["nav"].iloc[-1] / start_nav - 1.0)
        dd, pk, tr = max_drawdown(sub["nav"])
        rows.append(dict(year=y, days=len(sub), start_nav=start_nav,
                         end_nav=float(sub["nav"].iloc[-1]), return_=ret,
                         max_drawdown=dd, dd_trough=tr,
                         avg_exposure=float(sub["exposure"].mean()),
                         all_cash_days=int((sub["market_value"] <= 0.01).sum()),
                         win_days=int(sub["positive"].sum()),
                         loss_days=int(sub["negative"].sum()),
                         flat_days=int(sub["flat"].sum()),
                         fees=float(sub["fees"].sum())))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    OUT.mkdir(parents=True, exist_ok=True)
    log_lines = []

    def log(s):
        print(s, flush=True)
        log_lines.append(str(s))

    log(f"round3 local backtest: start={START} end={END} capital={CAP}")
    log(f"python={sys.version.split()[0]} pandas={pd.__version__} numpy={np.__version__}")
    log(f"engine repo={REPO}")
    codes_hash = {
        "s0s1.py": sha256_file(HERE / "s0s1.py"),
        "run_round3.py": sha256_file(HERE / "run_round3.py"),
        "engine/core.py": sha256_file(REPO / "engine" / "core.py"),
        "engine/data.py": sha256_file(REPO / "engine" / "data.py"),
        "engine/metrics.py": sha256_file(REPO / "engine" / "metrics.py"),
        "regime_v1.csv": sha256_file(REPO / "os" / "out" / "regime_v1.csv"),
        "data/manifest.json": sha256_file(REPO / "data" / "meta" / "manifest.json"),
        "S0_platform_source.py": sha256_file(HERE.parent / "SuperMind_S0_月度基线_胜率诊断_v03.py"),
        "S1_platform_source.py": sha256_file(HERE.parent / "SuperMind_S1_整手候选补位_待验证_v03.py"),
    }
    for k, v in codes_hash.items():
        log(f"sha256 {k} = {v}")

    letters, states = load_regime()

    runs = [
        ("S0", "S0", dict(), "S0"),
        ("S1", "S1", dict(), "S1"),
        ("S1b", "S1b", dict(), "S1b"),
        ("S0_20bp", "S0", dict(slippage_bps=20.0), "S0_20bp"),
        ("S1_stress", "S1", dict(slippage_bps=20.0, commission_bps=5.0), "S1_stress"),
    ]
    results = {}
    for tag, mode, kw, nm in runs:
        res, strat, elapsed = run_variant(tag, mode, letters, extra_name=nm, **kw)
        df = build_daily(res, strat, states)
        pos = positions_table(strat)
        results[tag] = dict(res=res, strat=strat, df=df, pos=pos, elapsed=elapsed)

    # authoritative deliverable tables come from the two primary candidates
    for tag in ("S0", "S1"):
        r = results[tag]
        r["df"].to_csv(OUT / f"DAILY_AUDIT_{tag}.csv")
        r["pos"].to_csv(OUT / f"POSITIONS_{tag}.csv", index=False)
        r["res"]["trades"].to_csv(OUT / f"TRADES_{tag}.csv", index=False)
        orders_table(r["res"]).to_csv(OUT / f"ORDERS_{tag}.csv", index=False)
        rf = risk_flat_audit(r["df"], r["pos"])
        rf.to_csv(OUT / f"RISK_FLAT_AUDIT_{tag}.csv", index=False)
        log(f"[audit] {tag}: risk_flat_days={len(rf)} "
            f"classes={rf['classification'].value_counts().to_dict() if len(rf) else {}}")

    # S1 is the round's candidate: canonical unsuffixed files
    results["S1"]["df"].to_csv(OUT / "DAILY_AUDIT.csv")
    results["S1"]["pos"].to_csv(OUT / "POSITIONS.csv", index=False)
    results["S1"]["res"]["trades"].to_csv(OUT / "TRADES.csv", index=False)
    orders_table(results["S1"]["res"]).to_csv(OUT / "ORDERS.csv", index=False)
    results["S0"]["df"].to_csv(OUT / "DAILY_AUDIT_S0.csv")
    cash_cols = ["regime_letter", "regime_state", "cash", "market_value", "exposure",
                 "is_rebalance_signal", "rb_reason", "rb_exposure", "rb_n_selected",
                 "rb_n_unaffordable", "pending", "pending_targets", "cash_reason"]
    results["S1"]["df"][cash_cols].to_csv(OUT / "CASH_REASONS.csv")
    reb = []
    sels = []
    for tag in ("S0", "S1", "S1b"):
        for row in results[tag]["strat"].audit_rebalances:
            row = dict(row)
            detail = row.pop("detail", [])
            row["tag"] = tag
            reb.append(row)
            for d in detail:
                d = dict(d)
                d["tag"] = tag
                d["date"] = row["date"]
                d["state"] = row["state"]
                d["reason"] = row["reason"]
                sels.append(d)
    pd.DataFrame(reb).to_csv(OUT / "REBALANCES.csv", index=False)
    pd.DataFrame(sels).to_csv(OUT / "REBALANCE_SELECTIONS.csv", index=False)

    # metrics
    metrics = {"run_meta": {"engine_data_version": results["S0"]["res"].get("data_version"),
                            "settings": results["S0"]["res"].get("settings"),
                            "code_sha256": codes_hash,
                            "start": START, "end": END, "capital": CAP},
               "platform_reference": {"R2": PLATFORM_R2, "R0": PLATFORM_R0},
               "variants": {}}
    summary_rows = []
    for tag, r in results.items():
        mb = metrics_block(tag, r["res"], r["df"], r["pos"], r["strat"], r["elapsed"])
        metrics["variants"][tag] = mb
        summary_rows.append(mb)
    pd.DataFrame(summary_rows).to_csv(OUT / "summary.csv", index=False)

    yearly = []
    for tag, r in results.items():
        yt = yearly_table(r["df"])
        yt["tag"] = tag
        yearly.append(yt)
    pd.concat(yearly, ignore_index=True).to_csv(OUT / "yearly.csv", index=False)

    seg_rows = []
    for tag, r in results.items():
        for s, e in SEGMENTS:
            st = segment_stats(r["df"], s, e)
            if st:
                st["tag"] = tag
                seg_rows.append(st)
    pd.DataFrame(seg_rows).to_csv(OUT / "segments.csv", index=False)

    roll_rows = []
    for tag, r in results.items():
        for w, label in ((252, "12m"), (756, "36m")):
            rr = rolling_returns(r["df"]["nav"], w)
            if len(rr):
                roll_rows.append(dict(tag=tag, window=label, n=len(rr),
                                      worst=float(rr.min()), worst_end=str(rr.idxmin()),
                                      median=float(rr.median()), best=float(rr.max()),
                                      best_end=str(rr.idxmax()),
                                      pct_positive=float((rr > 0).mean())))
    pd.DataFrame(roll_rows).to_csv(OUT / "rolling.csv", index=False)

    stress = []
    for tag, base in (("S0_20bp", "S0"), ("S1_stress", "S1")):
        a = metrics["variants"][base]
        b = metrics["variants"][tag]
        stress.append(dict(base=base, stressed=tag,
                           base_return=a["total_return"], stress_return=b["total_return"],
                           delta_return=b["total_return"] - a["total_return"],
                           base_dd=a["max_drawdown"], stress_dd=b["max_drawdown"],
                           delta_dd=b["max_drawdown"] - a["max_drawdown"],
                           base_fees=a["total_fees"], stress_fees=b["total_fees"]))
    pd.DataFrame(stress).to_csv(OUT / "cost_stress.csv", index=False)

    with open(OUT / "metrics.json", "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=1, default=str, ensure_ascii=False)

    log("")
    log("=== summary (2014-07-01..2023-12-29, 100k, local engine) ===")
    cols = ["tag", "total_return", "max_drawdown", "winrate_all", "winrate_risk",
            "winrate_nonzero", "daily_profit_factor", "avg_exposure", "trade_count",
            "total_fees", "all_cash_days", "risk_flat_days", "open_positions_at_end"]
    log(pd.DataFrame(summary_rows)[cols].to_string(index=False))
    log("")
    log(f"platform reference: R2 monthly = {PLATFORM_R2['total_return']:.2%} / "
        f"maxDD {PLATFORM_R2['max_drawdown']:.2%} (platform engine/data, not comparable 1:1)")

    with open(OUT / "run.log", "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
