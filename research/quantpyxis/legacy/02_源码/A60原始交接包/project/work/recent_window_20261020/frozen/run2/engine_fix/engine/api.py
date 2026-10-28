"""Public API for the qrun_1416 backtest engine.

Single entry points (frozen; see engine/API.md for the full contract):

    result = run_backtest(strategy, start, end, capital=100000)
    results = run_many({"bull": strat_a, "bear": strat_b}, start, end)
    save_results_json(results, "path.json")

A `strategy` may be:
  * an instance of engine.core.Strategy (recommended),
  * a Strategy subclass,
  * a dict {"name","warmup","on_init","on_bar","on_rebalance"} of callables,
  * a shortcut string: "cash", "buyhold:sh000300", "equalweight:50", "momentum:20".
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .core import (
    Backtest,
    Context,
    FeeModel,
    Strategy,
    coerce_strategy,
    normalize_code,
    round_lot,
)
from .data import (
    DEFAULT_CACHE_DIR,
    DEFAULT_DATA_DIR,
    REPO_ROOT,
    DataBundle,
    DataNotReadyError,
    data_status,
    fmt_date,
    infer_index_code,
)
from . import metrics as _metrics
from . import ENGINE_VERSION


# --------------------------------------------------------------------------- #
# built-in strategies (benchmarks / smoke tests / perf reference)
# --------------------------------------------------------------------------- #

class Cash(Strategy):
    """Stay 100% in cash (legal portfolio state; no interest is paid on cash)."""

    name = "Cash"

    def on_init(self, ctx: Context):
        ctx.set_targets({})


class BuyHold(Strategy):
    """Buy and hold a single asset.

    `target` may be a stock code (100-share lots, normal A-share rules) or a
    published index code (`sh000300`, `sz399006`, ...).  Index targets are only a
    *proxy* for a benchmark/ETF position: fractional units, no stamp tax, no
    dividends (price index) - explicitly documented, never used to replace cash in
    a strategy (the user's framework forbids auto ETFs as a cash substitute).
    """

    def __init__(self, target: str = "sh000300", name: str | None = None):
        self.target = str(target)
        self.name = name or f"BuyHold({self.target})"
        self.warmup = 0

    def on_init(self, ctx: Context):
        ctx.set_targets({self.target: 1.0})

    def on_bar(self, ctx: Context):
        # keep the book alive if it expired (e.g. a blocked order); stop retrying when
        # the target stock has been delisted
        if self.target in ctx.positions or ctx._bt.pending_book is not None:
            return
        if ctx.is_delisted(self.target):
            return
        ctx.set_targets({self.target: 1.0})


class EqualWeight(Strategy):
    """Equal-weight the top-N most liquid names, rebalanced on a schedule.

    With `codes=None` the universe each rebalance date is the top `top_n` stocks
    by mean raw `amount` over the last `lookback` bars (liquidity proxy), excluding
    ST names and stocks that are suspended today.  This is the engine's reference
    multi-stock test strategy; research strategies live in os/ (Lead-owned).
    """

    def __init__(self, codes=None, top_n: int = 50, lookback: int = 20,
                 rebalance: str = "M", name: str | None = None,
                 min_history: int = 60, max_weight: float | None = None):
        self.codes = list(codes) if codes else None
        self.top_n = int(top_n)
        self.lookback = int(lookback)
        self.rebalance = rebalance
        self.name = name or (f"EqualWeight(fixed {len(self.codes)})" if self.codes
                             else f"EqualWeight(top{self.top_n})")
        self.min_history = int(min_history)
        self.max_weight = max_weight

    def on_rebalance(self, ctx: Context):
        codes = self.codes
        if codes is None:
            codes = ctx.top_by("amount", self.top_n, self.lookback)
        else:
            codes = [c for c in codes if ctx.is_tradable(c)]
        if not codes:
            ctx.set_targets({})
            return
        n = len(codes)
        w = 1.0 / n
        targets = {c: w for c in codes}
        ctx.set_targets(targets, note=f"equalweight n={n}")


class MomentumTopN(Strategy):
    """Monthly momentum-top-N equal-weight strategy (performance reference).

    Signal: qfq total-return momentum over `lookback` trading days, skipping the
    most recent `skip` days; universe filtered to currently tradable non-ST names
    with enough history and ranked by liquidity before selecting the top N.
    """

    def __init__(self, lookback: int = 120, top_n: int = 20, rebalance: str = "M",
                 skip: int = 0, liquidity_top: int = 800, name: str | None = None):
        self.lookback = int(lookback)
        self.top_n = int(top_n)
        self.rebalance = rebalance
        self.skip = int(skip)
        self.liquidity_top = int(liquidity_top)
        self.name = name or f"MomentumTopN(lb={lookback},n={top_n})"
        self.warmup = int(lookback + skip + 20)

    def on_rebalance(self, ctx: Context):
        need = self.lookback + 1 + self.skip
        cols, mat = ctx.history_matrix("qfq_close", need)
        if mat.shape[0] < need or mat.shape[1] == 0:
            return
        end = mat.shape[0] - 1 - self.skip
        start = end - self.lookback
        if start < 0:
            return
        with np.errstate(invalid="ignore", divide="ignore"):
            mom = mat[end] / mat[start] - 1.0
        universe = ctx.top_by("amount", self.liquidity_top, 20) if self.liquidity_top else \
            ctx.universe(tradable=True)
        idx = [i for i, c in enumerate(cols) if c in set(universe)]
        if not idx:
            ctx.set_targets({})
            return
        cand = [(float(mom[i]), cols[i]) for i in idx
                if np.isfinite(mom[i]) and np.isfinite(mat[:, i]).all()]
        cand.sort(key=lambda x: (-x[0], x[1]))
        picked = cand[: self.top_n]
        if not picked:
            ctx.set_targets({})
            return
        w = 1.0 / len(picked)
        ctx.set_targets({code: w for _, code in picked}, note=f"momentum n={len(picked)}")


def make_strategy(spec):
    """Build a built-in strategy from a shortcut string."""
    s = str(spec).strip()
    low = s.lower()
    if low in ("cash", "allcash", "all_cash"):
        return Cash()
    if low in ("buyhold", "buy_hold"):
        return BuyHold("sh000300")
    if low.startswith(("buyhold:", "buy_hold:")):
        return BuyHold(s.split(":", 1)[1])
    if low == "equalweight":
        return EqualWeight()
    if low.startswith("equalweight:"):
        return EqualWeight(top_n=int(s.split(":", 1)[1]))
    if low == "momentum":
        return MomentumTopN()
    if low.startswith("momentum:"):
        return MomentumTopN(top_n=int(s.split(":", 1)[1]))
    raise ValueError(f"unknown strategy shortcut {spec!r}; pass a Strategy instance, class or dict")


# --------------------------------------------------------------------------- #
# regime labels helper
# --------------------------------------------------------------------------- #

def load_regime_labels(regime_labels, data_dir=None) -> dict:
    """Normalize regime labels to {date: label}.

    Accepts None, a CSV path (columns date,regime), a pandas Series/DataFrame,
    or a plain dict.  A label attached to date T must be actionable at T's open
    (i.e. computed from data <= T-1, as engine.regime.build_regime_labels does).
    """
    if regime_labels is None:
        return {}
    if isinstance(regime_labels, dict):
        return {fmt_date(k): str(v) for k, v in regime_labels.items() if v is not None}
    if isinstance(regime_labels, pd.Series):
        return {fmt_date(k): str(v) for k, v in regime_labels.items() if pd.notna(v)}
    if isinstance(regime_labels, pd.DataFrame):
        df = regime_labels
        cols = {c.lower(): c for c in df.columns}
        if "regime" not in cols or "date" not in cols:
            raise ValueError("regime DataFrame must have 'date' and 'regime' columns")
        return {fmt_date(d): str(r) for d, r in zip(df[cols["date"]], df[cols["regime"]])
                if pd.notna(r)}
    if isinstance(regime_labels, (str, Path)):
        path = Path(regime_labels)
        if not path.is_absolute():
            path = (Path(data_dir) if data_dir else REPO_ROOT) / path
        if not path.exists():
            # allow the default auto path to be absent without failing the run
            return {}
        df = pd.read_csv(path, dtype={"date": str})
        return load_regime_labels(df)
    raise TypeError(f"unsupported regime_labels type {type(regime_labels)!r}")


def _default_regime_path(data_dir) -> Path:
    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    return Path(base).parent / "engine" / "out" / "regime_labels.csv"


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #

def run_backtest(strategy, start: str | None = "2014-01-01", end: str | None = None,
                 capital: float = 100_000.0, data_dir=None,
                 *,
                 rebalance="M",
                 benchmark="sh000300",
                 regime_labels=None,
                 slippage_bps: float = 10.0,
                 commission_bps: float = 2.5,
                 min_commission: float = 5.0,
                 allow_st_buy: bool = False,
                 allow_ipo_day_trade: bool = False,
                 max_pending_days: int = 20,
                 exec_timing: str = "next_open",
                 dividend_mode: str = "qfq_reinvest",
                 dividend_min_pct: float = 0.003,
                 warmup_days: int = 320,
                 require_panel: bool = True,
                 infer_missing_actions: bool = True,
                 cash_rate: float = 0.0,
                 cache_dir=None,
                 name: str | None = None,
                 verbose: bool = False,
                 output_json: str | None = None) -> dict:
    """Run one daily backtest and return the full result dict.

    Parameters
    ----------
    strategy : Strategy | type | dict | str
        What to run (see module docstring).
    start, end : 'YYYY-MM-DD'
        Backtest window.  `end=None` uses the last published trading day.
    capital : float, default 100000
        Initial cash in CNY (no leverage; cash earns no interest).
    data_dir : path, default <repo>/qrun_1416/data
    rebalance : 'D'|'W'|'M'|'Q'|'A'|int|list of dates|None, default 'M'
        Engine-scheduled rebalance days (:class:`Strategy.on_rebalance` is called at
        that day's close; execution at the next open by default).  None = only
        strategy-driven hooks.
    benchmark : str | list[str] | None
        Published index codes for buy-and-hold comparison, plus 'cash' always.
    regime_labels : None | dict | Series | DataFrame | CSV path
        Label per date (actionable at that date's open) exposed as ctx.regime.
    slippage_bps : float, default 10
        Per-side slippage in bp applied to the execution price (set 0 to disable).
    allow_st_buy : bool, default False
        ST names are not buyable unless this is True (sells are always allowed).
    allow_ipo_day_trade : bool, default False
        IPO-day bars are not traded by default (special first-day rules ignored).
    max_pending_days : int, default 20
        How long blocked orders / a target book are carried (suspension, limit board).
    exec_timing : 'next_open' (default) | 'next_close'
    dividend_mode : 'qfq_reinvest' (default) | 'none'
        Primary valuation is qfq total-return based: cash dividends are implicitly
        reinvested via a holding multiplier at the ex-date (documented approximation,
        no dividend cash is credited). 'none' = ignore corporate actions entirely
        (price-return sensitivity check).
    warmup_days : int, default 320
        Rows of history loaded before `start` for lookbacks (no future leakage).
    require_panel : bool, default True
        Raise if the stock panel is not published; set False for index-only smoke runs.
    infer_missing_actions : bool, default True
        Fallback that infers a value-preserving capital action when a held stock shows a raw
        price jump beyond any legal board limit that neither the reference `preclose` nor the
        `qfq` factor captured (data-defect mitigation; ~20 rare stock-days on the 2012-2026
        panel).  Default True is the behaviour all reported results were produced with; pass
        False for the strict frozen rule set only (documented switch for future work).
    output_json : path, optional
        When given, a JSON result dump is written there.

    Returns
    -------
    dict with keys: nav, trades, orders, events, positions, holdings_final,
    metrics, monthly, monthly_matrix, exposure, tail, fifo, benchmarks,
    benchmark_compare, regime_segments, summary, settings, data_version, warnings.
    """
    if start is None:
        start = "2014-01-01"
    if end is None:
        status = data_status(data_dir)
        end = "2030-12-31"
    bundle = DataBundle.load(data_dir=data_dir, start=start, end=end,
                             warmup_days=warmup_days, cache_dir=cache_dir,
                             verbose=verbose, require_panel=require_panel)
    strat = coerce_strategy(strategy)
    labels = load_regime_labels(regime_labels, data_dir=data_dir)
    if isinstance(regime_labels, str) and str(regime_labels).lower() == "auto":
        labels = load_regime_labels(_default_regime_path(data_dir))
    fees = FeeModel(commission_bps=commission_bps, min_commission=min_commission)
    bt = Backtest(
        bundle, strat, start_i=bundle._start_i, end_i=bundle._end_i,
        capital=capital, fees=fees, slippage_bps=slippage_bps,
        allow_st_buy=allow_st_buy, allow_ipo_day_trade=allow_ipo_day_trade,
        max_pending_days=max_pending_days, exec_timing=exec_timing,
        dividend_mode=dividend_mode, dividend_min_pct=dividend_min_pct,
        infer_missing_actions=infer_missing_actions,
        cash_rate=cash_rate,
        rebalance=rebalance, regime_labels=labels, verbose=verbose,
        run_name=name or strat.name,
    )
    raw = bt.run()
    result = _metrics.compute_result(raw, bundle=bundle, benchmark=benchmark, capital=capital)
    result["meta"] = {
        "engine_version": ENGINE_VERSION,
        "data_dir": str(bundle.data_dir),
        "data_version": bundle.data_version,
        "regime_labels_used": len(labels),
        "index_codes": sorted(bundle.index_dict),
        "warnings": list(result.get("warnings", [])),
    }
    if output_json:
        save_result_json(result, output_json)
    return result


def run_many(specs: dict, start: str | None = "2014-01-01", end: str | None = None,
             **kwargs) -> dict:
    """Run several named strategies over the same window and return {name: result}."""
    out = {}
    for name, spec in dict(specs).items():
        out[name] = run_backtest(spec, start=start, end=end, name=name, **kwargs)
    return out


# --------------------------------------------------------------------------- #
# JSON export helpers
# --------------------------------------------------------------------------- #

def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return [_jsonable(rec) for rec in obj.reset_index().to_dict(orient="records")]
    if isinstance(obj, pd.Series):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


RESULT_JSON_KEYS = [
    "name", "start", "end", "capital", "final_nav", "final_cash", "n_trading_days",
    "total_fees", "total_dividends", "total_slippage_cost", "total_traded_amount",
    "settings", "data_version", "meta", "warnings",
    "metrics", "summary", "monthly", "exposure", "tail", "fifo",
    "benchmark_compare", "regime_segments", "orders", "events", "holdings_final",
]


def result_to_json(result: dict, include_nav: bool = True, include_trades: bool = True,
                   include_positions: bool = False) -> dict:
    """Convert a result dict to a JSON-safe dict (DataFrames -> records)."""
    out = {}
    for key in RESULT_JSON_KEYS:
        if key in result:
            out[key] = _jsonable(result[key])
    if include_nav and "nav" in result:
        out["nav"] = _jsonable(result["nav"])
    if include_trades and "trades" in result:
        out["trades"] = _jsonable(result["trades"])
    if include_positions and "positions" in result:
        out["positions"] = _jsonable(result["positions"])
    return out


def save_result_json(result, path, include_nav: bool = True, include_trades: bool = True,
                     indent: int | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result_to_json(result, include_nav=include_nav, include_trades=include_trades)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")
    return path


def save_results_json(results: dict, path, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: result_to_json(res, **kwargs) for name, res in results.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def print_summary(result: dict, label: str | None = None):
    """Small human-readable summary (used by engine/run_api_smoke.py)."""
    s = result.get("summary", {})
    head = label or s.get("name", "run")
    print(f"=== {head} | {s.get('start')} .. {s.get('end')} ===")
    print(f"final_nav={s.get('final_nav'):,.2f} total_return={s.get('total_return'):.2%} "
          f"cagr={s.get('cagr'):.2%} vol={s.get('ann_vol'):.2%} sharpe={s.get('sharpe'):.2f}")
    print(f"maxDD={s.get('max_drawdown'):.2%} "
          f"({s.get('maxdd_peak_date')} -> {s.get('maxdd_trough_date')}) "
          f"avg_exposure={s.get('avg_exposure'):.1%} all_cash_days={s.get('all_cash_days')}")
    print(f"trades={s.get('trade_count')} trade_days={s.get('trade_days')} "
          f"fees={s.get('total_fees'):,.2f} turnover/yr={s.get('turnover_annual'):.2f}x "
          f"fifo_pnl={s.get('fifo_realized_pnl'):,.2f} "
          f"implicit_div={s.get('implicit_dividends'):,.2f}")
    bc = result.get("benchmark_compare")
    if bc is not None and len(bc):
        for _, row in bc.iterrows():
            print(f"  vs {row['benchmark']}: bench_ret={row['benchmark_total_return']:.2%} "
                  f"excess={row['excess_total_return']:+.2%} beta={row['beta']:.2f} corr={row['corr']:.2f}")
