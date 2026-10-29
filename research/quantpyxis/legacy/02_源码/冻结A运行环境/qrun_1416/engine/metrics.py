"""Performance / risk / trade statistics for qrun_1416 backtests.

All functions are pure and deterministic; the public entry point is
`compute_result(raw_result, bundle, benchmark=...)`, called by engine.api.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------- #
# basic series helpers
# --------------------------------------------------------------------------- #

def to_nav_series(nav) -> pd.Series:
    """Accept Series/DataFrame/list and return a clean float NAV Series indexed by date."""
    if isinstance(nav, pd.Series):
        s = nav.astype(float)
    elif isinstance(nav, pd.DataFrame):
        col = "nav" if "nav" in nav.columns else nav.columns[0]
        s = nav[col].astype(float)
    else:
        s = pd.Series(nav, dtype=float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def _returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change(fill_method=None).dropna()


def total_return(nav) -> float:
    s = to_nav_series(nav)
    if len(s) < 2 or s.iloc[0] <= 0:
        return 0.0
    return float(s.iloc[-1] / s.iloc[0] - 1.0)


def cagr(nav) -> float:
    s = to_nav_series(nav)
    if len(s) < 2 or s.iloc[0] <= 0 or s.iloc[-1] <= 0:
        return 0.0
    years = len(s) / TRADING_DAYS
    if years <= 0:
        return 0.0
    return float((s.iloc[-1] / s.iloc[0]) ** (1.0 / years) - 1.0)


def annualized_vol(ret) -> float:
    r = pd.Series(ret, dtype=float).dropna()
    if len(r) < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    return float(sd * math.sqrt(TRADING_DAYS))


def sharpe(ret, rf: float = 0.0, ann: int = TRADING_DAYS) -> float:
    r = pd.Series(ret, dtype=float).dropna()
    if len(r) < 2:
        return 0.0
    excess = r - rf / ann
    sd = float(excess.std(ddof=1))
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float(excess.mean() / sd * math.sqrt(ann))


def sortino(ret, rf: float = 0.0, ann: int = TRADING_DAYS) -> float:
    r = pd.Series(ret, dtype=float).dropna()
    if len(r) < 2:
        return 0.0
    excess = r - rf / ann
    downside = excess[excess < 0]
    if len(downside) == 0:
        return 0.0
    dd = float(np.sqrt((downside ** 2).mean()))
    if dd == 0:
        return 0.0
    return float(excess.mean() / dd * math.sqrt(ann))


def drawdown_series(nav) -> pd.Series:
    s = to_nav_series(nav)
    peak = s.cummax()
    return s / peak - 1.0


def max_drawdown(nav) -> dict:
    """Max drawdown with peak/trough/recovery dates and duration.

    duration is calendar days from peak to recovery (or to the last date when
    still under water); underwater_days is the trading-day count under water.
    """
    s = to_nav_series(nav)
    out = {"max_drawdown": 0.0, "peak_date": None, "trough_date": None,
           "recovery_date": None, "duration_days": None, "underwater_days": 0,
           "longest_drawdown_days": 0}
    if len(s) < 2:
        return out
    dd = drawdown_series(s)
    trough_pos = int(np.argmin(dd.to_numpy()))
    max_dd = float(dd.iloc[trough_pos])
    out["max_drawdown"] = max_dd
    if max_dd >= 0:
        return out
    trough_date = s.index[trough_pos]
    peak_pos = int(np.argmax(s.iloc[: trough_pos + 1].to_numpy()))
    peak_date = s.index[peak_pos]
    out["peak_date"] = str(peak_date)
    out["trough_date"] = str(trough_date)
    # recovery
    after = s.iloc[trough_pos:]
    rec = after[after >= s.iloc[peak_pos]]
    if len(rec):
        out["recovery_date"] = str(rec.index[0])
        out["duration_days"] = int((pd.Timestamp(rec.index[0]) - pd.Timestamp(peak_date)).days)
    else:
        out["duration_days"] = int((pd.Timestamp(s.index[-1]) - pd.Timestamp(peak_date)).days + 1)
    # longest underwater stretch (peak-to-recovery or peak-to-end)
    pos = 0
    n = len(s)
    longest = 0
    underwater = 0
    peak_val = -np.inf
    peak_idx = 0
    for k in range(n):
        v = float(s.iloc[k])
        if v >= peak_val:
            # previous stretch ended at k-1
            if peak_val > -np.inf and k - 1 > peak_idx:
                longest = max(longest, k - 1 - peak_idx)
            peak_val = v
            peak_idx = k
        else:
            underwater += 1
    longest = max(longest, (n - 1) - peak_idx)
    out["underwater_days"] = int((dd < 0).sum())
    out["longest_drawdown_days"] = int(longest)
    return out


# --------------------------------------------------------------------------- #
# monthly / tail / exposure
# --------------------------------------------------------------------------- #

def monthly_returns(nav, capital: float | None = None) -> pd.DataFrame:
    """Month-by-month return table with columns ['year','month','ret','nav_end']."""
    s = to_nav_series(nav)
    if len(s) == 0:
        return pd.DataFrame(columns=["year", "month", "ret", "nav_end"])
    first_capital = float(capital) if capital else float(s.iloc[0])
    month_end = s.groupby(pd.Index([str(i)[:7] for i in s.index])).last()
    rows = []
    prev = first_capital
    for key, val in month_end.items():
        rows.append({"year": int(str(key)[:4]), "month": int(str(key)[5:7]),
                     "ret": float(val / prev - 1.0) if prev > 0 else 0.0,
                     "nav_end": float(val)})
        prev = float(val)
    return pd.DataFrame(rows)


def monthly_matrix(monthly: pd.DataFrame) -> pd.DataFrame:
    """Year x month (% ) pivot for reporting."""
    if monthly is None or len(monthly) == 0:
        return pd.DataFrame()
    m = monthly.copy()
    m["col"] = m["month"].map(lambda x: MONTH_NAMES[int(x) - 1])
    piv = m.pivot_table(index="year", columns="col", values="ret", aggfunc="last")
    piv = piv.reindex(columns=MONTH_NAMES)
    return piv


def tail_stats(nav, capital: float | None = None) -> dict:
    s = to_nav_series(nav)
    r = _returns(s)
    out = {"worst_day": 0.0, "worst_day_date": None, "best_day": 0.0, "best_day_date": None,
           "worst_week": 0.0, "worst_week_date": None, "best_week": 0.0,
           "worst_month": 0.0, "worst_month_date": None, "best_month": 0.0,
           "var95_daily": 0.0, "cvar95_daily": 0.0, "skew": 0.0, "kurtosis": 0.0,
           "pct_positive_days": 0.0}
    if len(r) == 0:
        return out
    out["worst_day"] = float(r.min())
    out["worst_day_date"] = str(r.idxmin())
    out["best_day"] = float(r.max())
    out["best_day_date"] = str(r.idxmax())
    weeks = _compound_by_period(s, "W")
    months = _compound_by_period(s, "M")
    if len(weeks):
        out["worst_week"] = float(weeks.min())
        out["worst_week_date"] = str(weeks.idxmin())
        out["best_week"] = float(weeks.max())
    if len(months):
        out["worst_month"] = float(months.min())
        out["worst_month_date"] = str(months.idxmin())
        out["best_month"] = float(months.max())
    q05 = float(np.percentile(r.to_numpy(), 5))
    out["var95_daily"] = q05
    tail = r[r <= q05]
    out["cvar95_daily"] = float(tail.mean()) if len(tail) else q05
    out["skew"] = float(r.skew()) if len(r) > 2 else 0.0
    out["kurtosis"] = float(r.kurt()) if len(r) > 3 else 0.0
    out["pct_positive_days"] = float((r > 0).mean())
    return out


def _compound_by_period(nav: pd.Series, freq: str) -> pd.Series:
    """Compound returns per W/M period using the last NAV of each period."""
    if len(nav) < 2:
        return pd.Series(dtype=float)
    if freq == "W":
        keys = [f"{pd.Timestamp(d).isocalendar()[0]}-W{pd.Timestamp(d).isocalendar()[1]:02d}"
                for d in nav.index]
    else:
        keys = [str(d)[:7] for d in nav.index]
    last = nav.groupby(pd.Index(keys)).last()
    first = nav.groupby(pd.Index(keys)).first()
    return last / first - 1.0


# --------------------------------------------------------------------------- #
# exposure / turnover / trades
# --------------------------------------------------------------------------- #

def exposure_stats(nav_df: pd.DataFrame) -> dict:
    out = {"avg_exposure": 0.0, "median_exposure": 0.0, "end_exposure": 0.0,
           "all_cash_days": 0, "days_in_market": 0, "avg_positions": 0.0,
           "max_exposure": 0.0}
    if nav_df is None or len(nav_df) == 0 or "exposure" not in nav_df.columns:
        return out
    ex = nav_df["exposure"].astype(float)
    out["avg_exposure"] = float(ex.mean())
    out["median_exposure"] = float(ex.median())
    out["end_exposure"] = float(ex.iloc[-1])
    out["max_exposure"] = float(ex.max())
    out["all_cash_days"] = int((ex <= 1e-9).sum())
    out["days_in_market"] = int((ex > 1e-9).sum())
    if "n_positions" in nav_df.columns:
        out["avg_positions"] = float(nav_df["n_positions"].astype(float).mean())
    return out


def turnover_stats(nav_df: pd.DataFrame, trades: pd.DataFrame, capital: float) -> dict:
    out = {"trade_count": 0, "buy_count": 0, "sell_count": 0, "trade_days": 0,
           "total_traded_amount": 0.0, "turnover_total": 0.0, "turnover_annual": 0.0,
           "avg_trade_amount": 0.0}
    if trades is not None and len(trades):
        real = trades[trades["side"].isin(["buy", "sell"])]
        out["trade_count"] = int(len(real))
        out["buy_count"] = int((real["side"] == "buy").sum())
        out["sell_count"] = int((real["side"] == "sell").sum())
        out["trade_days"] = int(real["date"].nunique()) if len(real) else 0
        out["total_traded_amount"] = float(real["amount"].sum()) if len(real) else 0.0
        out["avg_trade_amount"] = float(real["amount"].mean()) if len(real) else 0.0
    if nav_df is not None and len(nav_df):
        avg_nav = float(nav_df["nav"].astype(float).mean())
        if avg_nav > 0:
            out["turnover_total"] = out["total_traded_amount"] / avg_nav
            years = len(nav_df) / TRADING_DAYS
            out["turnover_annual"] = out["turnover_total"] / years if years > 0 else 0.0
    return out


def fifo_pnl(trades: pd.DataFrame, final_prices: dict | None = None) -> dict:
    """FIFO-matched realized P&L net of all fees, plus open-lot summary.

    `side == 'adjust'` rows are virtual zero-cost lots created by the qfq total-return
    approximation (implicit dividend reinvestment): they carry no cost and their full
    sale proceeds are realized gain.

    Buy lots store cost-per-unit including buy fees; sells realize
    (net sell price - allocated cost price) * matched units.
    """
    out = {"realized_pnl": 0.0, "matched_units": 0.0, "n_sells_matched": 0,
           "win_count": 0, "loss_count": 0, "win_rate": 0.0,
           "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
           "gross_profit": 0.0, "gross_loss": 0.0,
           "open_units": 0.0, "open_cost": 0.0,
           "n_adjust_events": 0,
           "top_winners": {}, "top_losers": {}}
    if trades is None or len(trades) == 0:
        return out
    df = trades.sort_values(["date"], kind="stable")
    lots: dict[str, deque] = {}
    by_code = {}
    wins, losses = [], []
    for _, t in df.iterrows():
        code = t["code"]
        units = float(t.get("shares", 0) or 0) + float(t.get("units", 0) or 0)
        if units <= 0:
            continue
        amount = float(t.get("amount", 0.0))
        fee = float(t.get("fee_total", 0.0))
        side = t["side"]
        if side == "adjust":
            lots.setdefault(code, deque()).append([units, 0.0])
            out["n_adjust_events"] += 1
            continue
        if side not in ("buy", "sell"):
            continue
        if side == "buy":
            cost_per = (amount + fee) / units
            lots.setdefault(code, deque()).append([units, cost_per])
            continue
        net_per = (amount - fee) / units
        remaining = units
        dq = lots.get(code)
        pnl = 0.0
        matched = 0.0
        while remaining > 1e-12 and dq:
            lot = dq[0]
            take = min(remaining, lot[0])
            pnl += take * (net_per - lot[1])
            lot[0] -= take
            remaining -= take
            matched += take
            if lot[0] <= 1e-12:
                dq.popleft()
        if matched > 1e-12:
            out["realized_pnl"] += pnl
            out["matched_units"] += matched
            out["n_sells_matched"] += 1
            by_code[code] = by_code.get(code, 0.0) + pnl
            if pnl >= 0:
                out["win_count"] += 1
                out["gross_profit"] += pnl
                wins.append(pnl)
            else:
                out["loss_count"] += 1
                out["gross_loss"] += pnl
                losses.append(pnl)
        # unmatched remainders (should not happen in long-only engine) are ignored
    n = out["win_count"] + out["loss_count"]
    out["win_rate"] = float(out["win_count"] / n) if n else 0.0
    out["avg_win"] = float(np.mean(wins)) if wins else 0.0
    out["avg_loss"] = float(np.mean(losses)) if losses else 0.0
    denom = abs(out["gross_loss"])
    out["profit_factor"] = float(out["gross_profit"] / denom) if denom > 0 else float("inf") if out["gross_profit"] > 0 else 0.0
    open_units = open_cost = 0.0
    for code, dq in lots.items():
        for lot in dq:
            open_units += lot[0]
            open_cost += lot[0] * lot[1]
    out["open_units"] = open_units
    out["open_cost"] = open_cost
    ranked = sorted(by_code.items(), key=lambda kv: kv[1])
    out["top_losers"] = {k: round(v, 2) for k, v in ranked[:5]}
    out["top_winners"] = {k: round(v, 2) for k, v in reversed(ranked[-5:])}
    return out


# --------------------------------------------------------------------------- #
# regime segmentation / benchmark comparison
# --------------------------------------------------------------------------- #

def regime_segments(nav_df: pd.DataFrame, trades: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-regime stats driven by the frozen labels in nav_df['regime']."""
    if nav_df is None or len(nav_df) == 0 or "regime" not in nav_df.columns:
        return pd.DataFrame()
    df = nav_df.copy()
    df["regime"] = df["regime"].fillna("unknown")
    rows = []
    for regime, sub in df.groupby("regime", sort=False):
        ret = sub["ret"].astype(float)
        nav = sub["nav"].astype(float)
        # chain the daily returns inside the segment to get a segment return
        seg_ret = float(np.prod(1.0 + ret.to_numpy()) - 1.0)
        dd = max_drawdown(nav) if len(nav) > 1 else {"max_drawdown": 0.0}
        row = {
            "regime": regime,
            "days": int(len(sub)),
            "start": str(sub.index[0]),
            "end": str(sub.index[-1]),
            "segment_return": seg_ret,
            "avg_daily_ret": float(ret.mean()),
            "ann_vol": float(ret.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(ret) > 1 else 0.0,
            "sharpe": sharpe(ret),
            "max_drawdown": float(dd.get("max_drawdown", 0.0)),
            "avg_exposure": float(sub["exposure"].mean()) if "exposure" in sub else 0.0,
            "pct_positive_days": float((ret > 0).mean()) if len(ret) else 0.0,
        }
        if trades is not None and len(trades):
            tt = trades[trades["date"].isin(set(sub.index))]
            row["trade_count"] = int(len(tt))
            row["traded_amount"] = float(tt["amount"].sum()) if len(tt) else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def benchmark_compare(nav: pd.Series, benchmarks: dict, rf: float = 0.0) -> pd.DataFrame:
    """Compare the strategy NAV against each benchmark NAV (aligned, starting at same value)."""
    if not benchmarks:
        return pd.DataFrame()
    strat_ret = _returns(nav)
    rows = []
    for name, bnav in benchmarks.items():
        b = to_nav_series(bnav).reindex(nav.index).ffill()
        if b.isna().all() or len(b.dropna()) < 2:
            continue
        b = b.dropna()
        br = _returns(b)
        common = strat_ret.index.intersection(br.index)
        sr, brr = strat_ret.loc[common], br.loc[common]
        beta = 0.0
        alpha = 0.0
        corr = 0.0
        if len(common) > 2 and brr.std(ddof=1) > 0:
            beta = float(np.cov(sr, brr, ddof=1)[0, 1] / np.var(brr, ddof=1))
            alpha = float((sr.mean() - rf / TRADING_DAYS - beta * (brr.mean() - rf / TRADING_DAYS))
                          * TRADING_DAYS)
            sd_s, sd_b = float(sr.std(ddof=1)), float(brr.std(ddof=1))
            if sd_s > 0 and sd_b > 0:
                corr = float(np.cov(sr, brr, ddof=1)[0, 1] / (sd_s * sd_b))
        rows.append({
            "benchmark": name,
            "benchmark_total_return": float(b.iloc[-1] / b.iloc[0] - 1.0),
            "benchmark_cagr": cagr(b),
            "benchmark_ann_vol": annualized_vol(br),
            "benchmark_max_drawdown": float(drawdown_series(b).min()),
            "strategy_total_return": total_return(nav),
            "excess_total_return": float(total_return(nav) - (b.iloc[-1] / b.iloc[0] - 1.0)),
            "beta": beta,
            "annualized_alpha": alpha,
            "corr": corr,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# full result assembly
# --------------------------------------------------------------------------- #

def _benchmark_navs(bundle, nav_index, codes, capital: float) -> dict:
    """Buy-and-hold NAV of published price indices aligned to the strategy dates."""
    out = {}
    for code in codes or []:
        try:
            if not bundle.has_index(code):
                continue
            close = bundle.index_close_series(code)
            aligned = close.reindex(pd.Index(nav_index)).ffill()
            aligned = aligned.dropna()
            if len(aligned) < 2 or aligned.iloc[0] <= 0:
                continue
            # the benchmark is bought at the first available close (no lookahead issue:
            # it is a comparison series, not a trade signal)
            out[code] = capital * aligned / float(aligned.iloc[0])
        except Exception:
            continue
    return out


def compute_result(raw: dict, bundle=None, benchmark="sh000300", capital: float | None = None,
                   rf: float = 0.0) -> dict:
    """Attach the full metric block to a raw Backtest result (in place + return)."""
    capital = float(capital if capital is not None else raw.get("capital", 100000.0))
    nav_df = raw.get("nav")
    if nav_df is None or len(nav_df) == 0:
        raise ValueError("raw result has no nav rows")
    nav_series = nav_df["nav"].astype(float)
    ret = nav_series.pct_change(fill_method=None).dropna()
    trades = raw.get("trades")

    bench_codes = []
    if benchmark:
        bench_codes = [benchmark] if isinstance(benchmark, str) else list(benchmark)
    benchmarks = _benchmark_navs(bundle, nav_series.index, bench_codes, capital)
    benchmarks["cash"] = pd.Series(capital, index=nav_series.index, dtype=float)
    for name, bnav in benchmarks.items():
        if name == "cash":
            continue
        nav_df[f"bench_{name}"] = bnav.reindex(nav_series.index).ffill()

    dd = max_drawdown(nav_series)
    monthly = monthly_returns(nav_series, capital=capital)
    exposure = exposure_stats(nav_df)
    turn = turnover_stats(nav_df, trades, capital)
    fifo = fifo_pnl(trades)
    tail = tail_stats(nav_series, capital=capital)
    bench_cmp = benchmark_compare(nav_series, {k: v for k, v in benchmarks.items()})
    regime_df = regime_segments(nav_df, trades)

    fees_total = float(trades["fee_total"].sum()) if trades is not None and len(trades) else 0.0
    dividend_total = float(nav_df["dividends"].sum()) if "dividends" in nav_df.columns else 0.0
    final_nav = float(nav_series.iloc[-1])
    total_ret = float(final_nav / capital - 1.0)
    n_days = len(nav_series)
    # unrealized P&L of positions still open at the end.  Use the FIFO remaining cost
    # (not the engine's average-cost basis) so that realized + unrealized == total P&L.
    open_value = float(sum(float(pos.get("value", 0.0))
                           for pos in (raw.get("holdings_final") or {}).values()))
    engine_open_cost = float(sum(float(pos.get("cost_total", 0.0))
                                 for pos in (raw.get("holdings_final") or {}).values()))
    unrealized = float(open_value - fifo["open_cost"])

    metrics = {
        # return / risk
        "initial_capital": capital,
        "final_nav": final_nav,
        "total_return": total_ret,
        "cagr": float((final_nav / capital) ** (TRADING_DAYS / n_days) - 1.0)
        if final_nav > 0 and n_days > 0 else 0.0,
        "ann_vol": annualized_vol(ret),
        "sharpe": sharpe(ret, rf=rf),
        "sortino": sortino(ret, rf=rf),
        "calmar": float(float((final_nav / capital) ** (TRADING_DAYS / n_days) - 1.0)
                        / abs(dd["max_drawdown"])) if dd["max_drawdown"] < 0 else 0.0,
        "max_drawdown": dd["max_drawdown"],
        "maxdd_peak_date": dd["peak_date"],
        "maxdd_trough_date": dd["trough_date"],
        "maxdd_recovery_date": dd["recovery_date"],
        "maxdd_duration_days": dd["duration_days"],
        "longest_drawdown_days": dd["longest_drawdown_days"],
        "underwater_days": dd["underwater_days"],
        "trading_days": int(n_days),
        "calendar_days": int((pd.Timestamp(nav_series.index[-1]) -
                              pd.Timestamp(nav_series.index[0])).days + 1),
        # exposure / turnover / costs
        "avg_exposure": exposure["avg_exposure"],
        "median_exposure": exposure["median_exposure"],
        "end_exposure": exposure["end_exposure"],
        "max_exposure": exposure["max_exposure"],
        "all_cash_days": exposure["all_cash_days"],
        "days_in_market": exposure["days_in_market"],
        "avg_positions": exposure["avg_positions"],
        "turnover_total": turn["turnover_total"],
        "turnover_annual": turn["turnover_annual"],
        "trade_count": turn["trade_count"],
        "buy_count": turn["buy_count"],
        "sell_count": turn["sell_count"],
        "trade_days": turn["trade_days"],
        "total_traded_amount": turn["total_traded_amount"],
        "avg_trade_amount": turn["avg_trade_amount"],
        "total_fees": fees_total,
        "fee_pct_of_capital": fees_total / capital if capital else 0.0,
        "implicit_dividends": dividend_total,
        "total_slippage_cost": float(raw.get("total_slippage_cost", 0.0)),
        # trade pnl
        "fifo_realized_pnl": fifo["realized_pnl"],
        "fifo_win_count": fifo["win_count"],
        "fifo_loss_count": fifo["loss_count"],
        "fifo_win_rate": fifo["win_rate"],
        "fifo_avg_win": fifo["avg_win"],
        "fifo_avg_loss": fifo["avg_loss"],
        "fifo_profit_factor": fifo["profit_factor"],
        "fifo_open_units": fifo["open_units"],
        "fifo_open_cost": fifo["open_cost"],
        "engine_open_cost": engine_open_cost,
        "fifo_unrealized_pnl": unrealized,
        "fifo_adjust_events": fifo["n_adjust_events"],
        "monthly_win_rate": float((monthly["ret"] > 0).mean()) if len(monthly) else 0.0,
        "best_month": tail["best_month"],
        "worst_month": tail["worst_month"],
        "n_months": int(len(monthly)),
        # reconciliation: total P&L must equal FIFO realized + open unrealized
        "pnl_realized_plus_unrealized": float(fifo["realized_pnl"] + unrealized),
        "pnl_actual": float(final_nav - capital),
        "pnl_reconciliation_gap": float(final_nav - capital - fifo["realized_pnl"] - unrealized),
    }
    raw["metrics"] = metrics
    raw["monthly"] = monthly
    raw["monthly_matrix"] = monthly_matrix(monthly)
    raw["exposure"] = exposure
    raw["tail"] = tail
    raw["fifo"] = fifo
    raw["benchmarks"] = benchmarks
    raw["benchmark_compare"] = bench_cmp
    raw["regime_segments"] = regime_df
    raw["nav"] = nav_df
    raw["summary"] = _summary_lines(raw)
    return raw


def _summary_lines(raw: dict) -> dict:
    m = raw["metrics"]
    return {
        "name": raw.get("name"),
        "start": raw.get("start"),
        "end": raw.get("end"),
        "final_nav": round(m["final_nav"], 2),
        "total_return": round(m["total_return"], 6),
        "cagr": round(m["cagr"], 6),
        "ann_vol": round(m["ann_vol"], 6),
        "sharpe": round(m["sharpe"], 4),
        "max_drawdown": round(m["max_drawdown"], 6),
        "maxdd_peak_date": m["maxdd_peak_date"],
        "maxdd_trough_date": m["maxdd_trough_date"],
        "avg_exposure": round(m["avg_exposure"], 4),
        "end_exposure": round(m["end_exposure"], 4),
        "all_cash_days": m["all_cash_days"],
        "trade_count": m["trade_count"],
        "trade_days": m["trade_days"],
        "total_fees": round(m["total_fees"], 2),
        "turnover_annual": round(m["turnover_annual"], 4),
        "fifo_realized_pnl": round(m["fifo_realized_pnl"], 2),
        "implicit_dividends": round(m["implicit_dividends"], 2),
        "pnl_reconciliation_gap": round(m["pnl_reconciliation_gap"], 4),
    }
