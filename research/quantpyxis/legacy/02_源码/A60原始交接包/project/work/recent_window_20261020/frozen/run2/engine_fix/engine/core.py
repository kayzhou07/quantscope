"""Event-driven daily A-share backtest kernel (engine-dev owned).
Frozen execution / cost rules (see engine/API.md and SPEC_TEAM.md):
* signal is generated after the close of day T, using data <= T close only;
  default execution is day T+1 OPEN ("close signal -> next open");
* long only, no leverage, cash may never go negative; cash earns no interest;
* stock buys in multiples of 100 shares; sells may be odd lots (corporate actions);
* T+1: shares bought on day D cannot be sold before day D+1;
* limit-up blocks buys / limit-down blocks sells at the execution price, detected
  from raw prices vs the raw `preclose` field (board-specific bands, ST 5%);
* suspension (missing bar or tradestatus == 0) -> order is carried, never guessed;
* delisting -> forced liquidation at the last available price with an explicit event;
* fees: commission 2.5bp (min 5 CNY) both sides; stamp tax on sells 0.1% before
  2023-08-28 and 0.05% from 2023-08-28; transfer fee 0.001% both sides
  (documented simplification: applied to SH and SZ symmetric per the 2022 rule);
* optional slippage in bp per side on the execution price;
* corporate actions: the qfq panel is used to infer the total-return factor; cash
  dividends are credited to cash and share splits/transfers are applied to the
  holding (forward-adjusted total-return approximation, documented in API.md).

The engine only ever exposes data with row index <= ctx.i through the Context API,
so strategies cannot look ahead unless they bypass the Context and read raw files.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from .data import (
    DataBundle,
    board_of,
    fmt_date,
    infer_index_code,
    normalize_code,
)

LOT = 100
EPS = 1e-9
_PRICE_EPS = 1e-3
STAMP_TAX_SWITCH = "2023-08-28"
CHINEXT_REFORM = "2020-08-24"

# --------------------------------------------------------------------------- #
# data field aliases: the panel stores `qfq_close` etc.; the Context API accepts
# both spellings so strategy code cannot silently read the wrong (raw) series.
# --------------------------------------------------------------------------- #
FIELD_ALIASES = {
    "close_qfq": "qfq_close", "open_qfq": "qfq_open",
    "high_qfq": "qfq_high", "low_qfq": "qfq_low",
    "adj_close": "qfq_close", "adj_open": "qfq_open",
    "close_raw": "close", "open_raw": "open",
    "high_raw": "high", "low_raw": "low",
}


def canonical_field(name: str) -> str:
    return FIELD_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())


TRADE_COLUMNS = ["date", "code", "kind", "side", "shares", "units", "price", "amount",
                 "commission", "stamp", "transfer", "fee_total", "slippage_cost",
                 "reason", "target_weight", "note"]
ORDER_COLUMNS = ["created_date", "filled_date", "code", "side", "kind", "requested",
                 "filled", "status", "reason", "note"]
EVENT_COLUMNS = ["date", "type", "code", "note"]
HOLDING_COLUMNS = ["date", "code", "kind", "shares", "units", "price", "value",
                   "weight", "cost_total"]


def _rows_to_df(rows: list, columns: list) -> pd.DataFrame:
    """Stable DataFrame schema even when there are no rows / heterogeneous keys."""
    df = pd.DataFrame(rows)
    if len(df) == 0:
        return pd.DataFrame(columns=columns)
    for col in columns:
        if col not in df.columns:
            df[col] = np.nan
    leading = [c for c in columns if c in df.columns]
    trailing = [c for c in df.columns if c not in leading]
    return df[leading + trailing]


# --------------------------------------------------------------------------- #
# rules helpers (pure functions, unit tested directly)
# --------------------------------------------------------------------------- #

def round_cny(x: float, nd: int = 2) -> float:
    """Half-up rounding to cents (banker's rounding is wrong for money).

    Uses the shortest decimal repr of the float (e.g. 1.005 -> 1.01) so that
    binary floating point noise does not silently round money down.
    """
    if not isinstance(x, (int, float, np.floating, np.integer)):
        x = float(x)
    if not np.isfinite(x):
        return float(x)
    from decimal import ROUND_HALF_UP, Decimal
    quantum = Decimal(1).scaleb(-int(nd))
    return float(Decimal(repr(float(x))).quantize(quantum, rounding=ROUND_HALF_UP))


def round_lot(shares: float, lot: int = LOT) -> int:
    """Round *down* to an integer number of lots."""
    if not np.isfinite(shares) or shares <= 0:
        return 0
    return int(math.floor(shares / lot)) * lot


def limit_rates(date: str, board: str = "main", is_st: bool = False) -> tuple[float, float]:
    """(up_rate, down_rate) for the board on that date.

    main board: 10%, ST 5%
    ChiNext (300/301): 10% before 2020-08-24, 20% from 2020-08-24; after the
    reform ChiNext ST names also move 20% (pre-reform ChiNext ST used 5%).
    """
    date = fmt_date(date)
    board = (board or "main").lower()
    if board == "chinext":
        if date >= CHINEXT_REFORM:
            return 0.20, 0.20
        return (0.05, 0.05) if is_st else (0.10, 0.10)
    return (0.05, 0.05) if is_st else (0.10, 0.10)


def limit_prices(preclose: float, date: str, board: str = "main",
                 is_st: bool = False) -> tuple[float, float]:
    """Exchange-style daily limit prices from the raw previous close."""
    if not np.isfinite(preclose) or preclose <= 0:
        return (np.nan, np.nan)
    up_r, dn_r = limit_rates(date, board, is_st)
    return round_cny(preclose * (1.0 + up_r)), round_cny(preclose * (1.0 - dn_r))


def stamp_tax_rate(date: str) -> float:
    """Sell-side stamp tax: 0.1% before 2023-08-28, 0.05% from 2023-08-28."""
    return 0.0005 if fmt_date(date) >= STAMP_TAX_SWITCH else 0.0010


@dataclass(frozen=True)
class FeeModel:
    commission_bps: float = 2.5
    min_commission: float = 5.0
    transfer_rate: float = 1e-5      # 0.001% both sides (documented simplification)
    audit_slippage_bps: float = 0.0  # informational only

    def fees(self, side: str, amount: float, date: str) -> dict:
        """Return fee breakdown for a fill of `amount` CNY. side in {buy, sell}."""
        amount = float(abs(amount))
        commission = max(round_cny(amount * self.commission_bps / 1e4), self.min_commission)
        stamp = round_cny(amount * stamp_tax_rate(date)) if side == "sell" else 0.0
        transfer = round_cny(amount * self.transfer_rate)
        return {
            "commission": float(commission),
            "stamp": float(stamp),
            "transfer": float(transfer),
            "total": float(round_cny(commission + stamp + transfer)),
        }


# --------------------------------------------------------------------------- #
# strategy protocol
# --------------------------------------------------------------------------- #

class Strategy:
    """Strategy protocol base class.

    Override any of the three hooks.  A strategy instance is used for exactly one
    backtest run; create a fresh instance per run (or pass a class / dict spec).
    """

    name: str = "strategy"
    warmup: int = 0            # informational: bars of history the strategy expects
    rebalance: str | None = None  # optional per-strategy default rebalance frequency

    def on_init(self, ctx: "Context") -> None:
        """Called once after data load, before the first trading day."""

    def on_bar(self, ctx: "Context") -> None:
        """Called at the close of every trading day (data <= today only)."""

    def on_rebalance(self, ctx: "Context") -> None:
        """Called at the close of every engine-scheduled rebalance day."""


class _DictStrategy(Strategy):
    def __init__(self, spec: dict):
        self.name = str(spec.get("name", "dict_strategy"))
        self.warmup = int(spec.get("warmup", 0))
        self.rebalance = spec.get("rebalance")
        self._on_init = spec.get("on_init")
        self._on_bar = spec.get("on_bar")
        self._on_rebalance = spec.get("on_rebalance")

    def on_init(self, ctx):
        if self._on_init:
            self._on_init(ctx)

    def on_bar(self, ctx):
        if self._on_bar:
            self._on_bar(ctx)

    def on_rebalance(self, ctx):
        if self._on_rebalance:
            self._on_rebalance(ctx)


def coerce_strategy(spec) -> Strategy:
    """Accept Strategy instance / Strategy subclass / dict of callables / string key."""
    if isinstance(spec, Strategy):
        return spec
    if isinstance(spec, type) and issubclass(spec, Strategy):
        return spec()
    if isinstance(spec, dict):
        return _DictStrategy(spec)
    if isinstance(spec, str):
        from .api import make_strategy  # local import to avoid cycle
        return make_strategy(spec)
    raise TypeError(f"cannot interpret strategy spec of type {type(spec)!r}")


# --------------------------------------------------------------------------- #
# positions / orders
# --------------------------------------------------------------------------- #

@dataclass
class Position:
    code: str
    kind: str = "stock"        # 'stock' | 'index' (index proxy, benchmark/smoke only)
    shares: float = 0.0        # stocks: shares; fractional only from the qfq-reinvest approximation
    units: float = 0.0         # index proxy: fractional units
    sellable: float = 0.0      # stocks: shares that are T+1 unlocked
    cost_total: float = 0.0    # CNY, including buy fees
    avg_cost: float = 0.0
    open_date: str = ""
    last_price: float = np.nan
    last_price_date: str = ""
    hold_days: int = 0

    def value(self) -> float:
        if self.kind == "index":
            return float(self.units * self.last_price) if np.isfinite(self.last_price) else 0.0
        return float(self.shares * self.last_price) if np.isfinite(self.last_price) else 0.0

    def to_dict(self, nav: float | None = None) -> dict:
        d = {
            "code": self.code,
            "kind": self.kind,
            "shares": float(self.shares),
            "units": float(self.units),
            "sellable": float(self.sellable),
            "cost_total": float(self.cost_total),
            "avg_cost": float(self.avg_cost),
            "last_price": float(self.last_price),
            "last_price_date": self.last_price_date,
            "open_date": self.open_date,
            "hold_days": int(self.hold_days),
            "value": float(self.value()),
            "unrealized_pnl": float(self.value() - self.cost_total),
        }
        if nav:
            d["weight"] = float(self.value() / nav)
        return d


@dataclass
class _TargetBook:
    weights: dict
    base_nav: float
    created_i: int
    signal_date: str


# --------------------------------------------------------------------------- #
# context handed to strategies
# --------------------------------------------------------------------------- #

class Context:
    """Strategy-facing API.  All data accessors are truncated at the current bar."""

    def __init__(self, bt: "Backtest"):
        self._bt = bt
        self.state: dict = {}
        self.strategy_name = bt.strategy.name

    # ---- identity / time ---------------------------------------------------
    @property
    def date(self) -> str:
        return self._bt.date

    @property
    def date_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self._bt.date)

    @property
    def i(self) -> int:
        """Global row index into the loaded panel (<= available history)."""
        return self._bt.i

    @property
    def day(self) -> int:
        """0-based index of the current day inside the requested window."""
        return self._bt.i - self._bt.start_i

    @property
    def start_date(self) -> str:
        return self._bt.bundle.date_at(self._bt.start_i)

    @property
    def end_date(self) -> str:
        return self._bt.bundle.date_at(self._bt.end_i)

    @property
    def is_rebalance_day(self) -> bool:
        return self._bt.i in self._bt.rebalance_set

    @property
    def regime(self) -> str | None:
        """Frozen regime label actionable today (derived from data <= t-1)."""
        return self._bt.regime_labels.get(self._bt.date) if self._bt.regime_labels else None

    # ---- portfolio ---------------------------------------------------------
    @property
    def cash(self) -> float:
        return self._bt.cash

    @property
    def nav(self) -> float:
        return self._bt.nav

    @property
    def positions(self) -> dict:
        return self._bt.positions

    def position(self, code) -> Position | None:
        code = self._bt.resolve_code(code)
        return self._bt.positions.get(code)

    @property
    def weights(self) -> dict:
        nav = self._bt.nav
        if nav <= 0:
            return {}
        return {c: p.value() / nav for c, p in self._bt.positions.items()}

    # ---- calendar / universe ----------------------------------------------
    def dates(self, window: int | None = None) -> list:
        """Trading dates known so far (up to and including today)."""
        dates = self._bt.bundle.dates[: self._bt.i + 1]
        if window is not None:
            dates = dates[-int(window):]
        return [str(d) for d in dates]

    def history(self, codes=None, field: str = "close_qfq", window: int = 60) -> pd.DataFrame:
        """Window x codes DataFrame, only rows <= today. Columns are stock codes."""
        bt = self._bt
        cols, mat = self._bt.history_matrix(field, window, codes)
        lo = max(0, bt.i - int(window) + 1)
        index = pd.Index([str(d) for d in bt.bundle.dates[lo: bt.i + 1]], name="date")
        return pd.DataFrame(mat, index=index, columns=pd.Index(cols, name="code"))

    def history_matrix(self, field: str = "close_qfq", window: int = 60, codes=None):
        """(window, n_codes) numpy matrix of the last `window` bars up to today."""
        return self._bt.history_matrix(field, window, codes)

    def latest(self, field: str = "close_qfq", codes=None) -> pd.Series:
        """Today's value of `field` for the requested codes (no future rows)."""
        cols, vals = self._bt.latest_values(field, codes)
        return pd.Series(vals, index=pd.Index(cols, name="code"), name=field)

    def stock_codes(self) -> np.ndarray:
        return self._bt.bundle.codes

    def universe(self, tradable: bool = True, exclude_st: bool = True,
                 min_history: int = 0, as_of=None) -> list:
        """Codes listed & (optionally) tradable & not ST at the reference date."""
        return self._bt.universe_list(tradable=tradable, exclude_st=exclude_st,
                                      min_history=min_history, as_of=as_of)

    def is_tradable(self, code) -> bool:
        return self._bt.is_tradable(code)

    def is_delisted(self, code) -> bool:
        """True once the listing has ended (out_date reached)."""
        return self._bt.is_delisted(code)

    def is_st(self, code) -> bool:
        code = self._bt.resolve_code(code)
        j = self._bt.bundle.code_index.get(code)
        if j is None:
            return False
        v = self._bt.bundle.field("isst")[self._bt.i, j]
        return bool(np.isfinite(v) and v > 0.5)

    def bar(self, code, date=None) -> dict | None:
        """Latest raw/qfq bar of a stock (or None when no bar that day)."""
        return self._bt.bar_of(code, date)

    def top_by(self, field: str = "amount", top_n: int = 50, lookback: int = 20,
               exclude_st: bool = True) -> list:
        """Top-N codes by mean `field` over the last `lookback` bars (no lookahead)."""
        return self._bt.top_by(field, top_n, lookback, exclude_st)

    # ---- index -------------------------------------------------------------
    def index(self, index_code: str = "sh000300", window: int | None = None,
              field: str = "close") -> pd.Series:
        """Index time series up to today (strings dates index)."""
        return self._bt.index_history(index_code, window, field)

    def index_history(self, window: int = 250, index_code: str = "sh000300",
                      field: str = "close") -> pd.Series:
        return self._bt.index_history(index_code, window, field)

    def index_latest(self, index_code: str = "sh000300", field: str = "close") -> float:
        return self._bt.index_value(index_code, field)

    def index_returns(self, window: int = 250, index_code: str = "sh000300") -> pd.Series:
        s = self._bt.index_history(index_code, window + 1, "close")
        return s.pct_change().dropna()

    # ---- orders (queued for the next execution slot) -----------------------
    def set_targets(self, weights: dict, normalize: bool = False, note: str = "") -> None:
        """Replace the whole target book: {code: target weight of signal-day NAV}.

        Weights must be >= 0 and their sum <= 1.0 (cash residual).  Execution is at
        the next open (default timing).  The book is retried daily until filled or
        `max_pending_days` elapsed (suspension / limit-up / limit-down carry).
        """
        clean = {}
        for k, v in dict(weights).items():
            code = self._bt.resolve_code(k)
            w = float(v)
            if not np.isfinite(w):
                raise ValueError(f"target weight for {k!r} is not finite")
            if w < 0:
                raise ValueError(f"short target weight for {k!r}: {w}")
            if w > 1.0 + 1e-9:
                raise ValueError(f"target weight for {k!r} exceeds 1.0: {w}")
            clean[code] = w
        total = sum(clean.values())
        if normalize and total > 1.0 + 1e-9:
            clean = {c: w / total for c, w in clean.items()}
            total = 1.0
        if total > 1.0 + 1e-6:
            raise ValueError(f"target weights sum to {total:.6f} > 1.0 (no leverage allowed)")
        self._pending_targets = clean
        self._pending_targets_note = note

    def order_target_percent(self, code, weight: float) -> None:
        """Adjust one position to `weight` of signal-day NAV, keeping the rest."""
        partial = dict(getattr(self, "_pending_partial", None) or {})
        partial[self._bt.resolve_code(code)] = float(weight)
        self._pending_partial = partial

    def buy(self, code, shares: int | None = None, value: float | None = None,
            weight: float | None = None, note: str = "") -> None:
        """Explicit buy order: exact shares, CNY value or fraction of NAV."""
        n_args = sum(x is not None for x in (shares, value, weight))
        if n_args != 1:
            raise ValueError("buy() needs exactly one of shares= / value= / weight=")
        code = self._bt.resolve_code(code)
        if weight is not None and not (0 < float(weight) <= 1.0):
            raise ValueError(f"buy weight must be in (0, 1]: {weight}")
        self._orders.append(dict(kind="buy", code=code, shares=shares, value=value,
                                 weight=None if weight is None else float(weight),
                                 note=note))

    def sell(self, code, shares: int | None = None, value: float | None = None,
             fraction: float | None = None, note: str = "") -> None:
        """Explicit sell order: exact shares, CNY value or fraction of the holding."""
        n_args = sum(x is not None for x in (shares, value, fraction))
        if n_args != 1:
            raise ValueError("sell() needs exactly one of shares= / value= / fraction=")
        code = self._bt.resolve_code(code)
        if fraction is not None and not (0 < float(fraction) <= 1.0):
            raise ValueError(f"sell fraction must be in (0, 1]: {fraction}")
        self._orders.append(dict(kind="sell", code=code, shares=shares, value=value,
                                 fraction=None if fraction is None else float(fraction),
                                 note=note))

    def close(self, code, note: str = "") -> None:
        """Explicitly close the whole position."""
        self.sell(code, fraction=1.0, note=note or "close")

    def close_all(self, note: str = "") -> None:
        self.set_targets({}, note=note or "close_all")

    def cancel_pending(self) -> None:
        """Drop queued explicit orders and the pending target book."""
        self._orders = []
        self._pending_targets = None
        self._pending_partial = None
        self._bt.pending_book = None
        self._bt.pending_orders = []

    def log(self, message: str) -> None:
        self._bt.log(str(message))

    def add_event(self, kind: str, code=None, **detail) -> None:
        self._bt.add_event(kind, code=code, **detail)

    # ---- internal collection (called by the engine, not by strategies) -----
    def _reset_orders(self):
        self._orders = []
        self._pending_targets = None
        self._pending_partial = None
        self._pending_targets_note = ""


# --------------------------------------------------------------------------- #
# backtest engine
# --------------------------------------------------------------------------- #

class Backtest:
    """Single-run event loop: corporate actions -> T+1 unlock -> execution at open
    -> mark to close -> strategy hooks -> collect queued orders for tomorrow."""

    def __init__(self, bundle: DataBundle, strategy: Strategy, start_i: int, end_i: int,
                 capital: float = 100_000.0, fees: FeeModel | None = None,
                 slippage_bps: float = 10.0, allow_st_buy: bool = False,
                 allow_ipo_day_trade: bool = False, max_pending_days: int = 20,
                 exec_timing: str = "next_open", dividend_mode: str = "qfq_reinvest",
                 dividend_min_pct: float = 0.003, rebalance="M",
                 regime_labels: dict | None = None, verbose: bool = False,
                 run_name: str | None = None, lot: int = LOT,
                 tolerance_pct: float = 0.003, ipo_no_limit_bars: int = 5,
                 infer_missing_actions: bool = True, cash_rate: float = 0.0):
        if capital <= 0:
            raise ValueError("capital must be > 0")
        if exec_timing not in ("next_open", "next_close"):
            raise ValueError("exec_timing must be 'next_open' or 'next_close'")
        if dividend_mode == "qfq_implied":
            dividend_mode = "qfq_reinvest"
        if dividend_mode not in ("qfq_reinvest", "none"):
            raise ValueError("dividend_mode must be 'qfq_reinvest' (default) or 'none'")
        self.bundle = bundle
        self.strategy = strategy
        self.start_i = int(start_i)
        self.end_i = int(end_i)
        self.capital = float(capital)
        self.cash = float(capital)
        self.fees = fees or FeeModel()
        self.slippage_bps = float(slippage_bps)
        self.allow_st_buy = bool(allow_st_buy)
        self.allow_ipo_day_trade = bool(allow_ipo_day_trade)
        self.max_pending_days = int(max_pending_days)
        self.exec_timing = exec_timing
        self.dividend_mode = dividend_mode
        self.dividend_min_pct = float(dividend_min_pct)
        self.lot = int(lot)
        self.tolerance_pct = float(tolerance_pct)
        self.ipo_no_limit_bars = max(1, int(ipo_no_limit_bars))
        self.infer_missing_actions = bool(infer_missing_actions)
        self.cash_rate = float(cash_rate)          # annual simple rate on idle cash (0 = no interest)
        self.regime_labels = dict(regime_labels or {})
        self.verbose = verbose
        self.run_name = run_name or strategy.name
        self.rebalance = rebalance
        self.rebalance_set = self._build_rebalance_set()

        # state
        self.positions: dict[str, Position] = {}
        self.pending_book: _TargetBook | None = None
        self.pending_orders: list[dict] = []
        self.trades: list[dict] = []
        self.orders: list[dict] = []
        self.events: list[dict] = []
        self.nav_records: list[dict] = []
        self.holding_records: list[dict] = []
        self.logs: list[str] = []
        self.i = self.start_i - 1
        self.date = ""
        self.nav = float(capital)
        self._prev_nav = float(capital)
        self._prev_snapshot: dict = {}
        self._trade_cursor = 0
        self._last_price = np.full(bundle.n_stocks(), np.nan, dtype=np.float64)
        self._index_arr_close: dict[str, np.ndarray] = {}
        self._index_arr_open: dict[str, np.ndarray] = {}
        self._index_last: dict[str, float] = {}
        self._first_bar = None
        self._div_total = 0.0
        self._corp_total = 0.0
        self._fee_total = 0.0
        self._slippage_total = 0.0
        self._traded_amount = 0.0
        self._day_fee = 0.0
        self._day_div = 0.0
        self._day_traded = 0.0
        self._day_interest = 0.0
        self._corporate_action_warnings = 0
        self.ctx = Context(self)
        self.ctx._reset_orders()

    # ------------------------------------------------------------------ setup
    def _build_rebalance_set(self) -> set:
        rb = self.rebalance
        if rb is None:
            return set()
        dates = self.bundle.dates
        rng = range(self.start_i, self.end_i + 1)
        if isinstance(rb, (list, tuple, set, np.ndarray)):
            wanted = {fmt_date(d) for d in rb}
            return {i for i in rng if str(dates[i]) in wanted}
        if isinstance(rb, (int, np.integer)):
            k = max(1, int(rb))
            return {i for i in rng if (i - self.start_i) % k == 0}
        freq = str(rb).strip().upper()
        if freq in ("NONE", "OFF", "FALSE", ""):
            return set()
        if freq == "D":
            return set(rng)
        keys = [str(dates[i]) for i in rng]
        if freq == "W":
            pkeys = [pd.Timestamp(k).isocalendar()[:2] for k in keys]
        elif freq == "M":
            pkeys = [k[:7] for k in keys]
        elif freq == "Q":
            pkeys = [(k[:4], (int(k[5:7]) - 1) // 3) for k in keys]
        elif freq == "A":
            pkeys = [k[:4] for k in keys]
        else:
            raise ValueError(f"unsupported rebalance frequency {rb!r}")
        out = set()
        idx = list(rng)
        for pos, i in enumerate(idx):
            if pos == len(idx) - 1 or pkeys[pos] != pkeys[pos + 1]:
                out.add(i)
        return out

    def _ensure_first_bar(self) -> np.ndarray:
        if self._first_bar is None:
            close = self.bundle.field("close")
            finite = np.isfinite(close)
            fb = np.where(finite.any(axis=0), finite.argmax(axis=0), -1)
            self._first_bar = fb.astype(np.int32)
        return self._first_bar

    def _ensure_index_arrays(self):
        for code in self.bundle.index_dict:
            if code in self._index_arr_close:
                continue
            df = self.bundle.index_dict[code]
            idx = pd.Index(self.bundle.dates)
            close = df["close"].reindex(idx).ffill() if "close" in df else pd.Series(np.nan, index=idx)
            openp = df["open"].reindex(idx).ffill() if "open" in df else close
            self._index_arr_close[code] = close.to_numpy(dtype=np.float64)
            self._index_arr_open[code] = openp.to_numpy(dtype=np.float64)

    def log(self, message: str):
        self.logs.append(message)

    def add_event(self, kind: str, code=None, **detail):
        rec = {"date": self.date, "type": kind}
        if code is not None:
            rec["code"] = code
        rec.update(detail)
        self.events.append(rec)

    # ------------------------------------------------------- code resolution
    def resolve_code(self, code) -> str:
        """Map a stock code or index code to its canonical engine key."""
        raw = str(code).strip()
        low = raw.lower()
        if low.startswith(("sh", "sz")) or (len(low) == 8 and low[:2] in ("sh", "sz")):
            canon = infer_index_code(low)
            if canon in self.bundle.index_dict:
                return canon
            return canon
        norm = normalize_code(raw)
        return norm

    def _is_index_code(self, code: str) -> bool:
        return code in self.bundle.index_dict

    # ------------------------------------------------------- data accessors
    def history_matrix(self, field_name: str, window: int, codes=None):
        field_name = canonical_field(field_name)
        panel = self.bundle.panel
        if field_name not in panel.fields:
            raise KeyError(f"unknown field {field_name!r}; available: {sorted(panel.fields)}")
        lo = max(0, self.i - int(window) + 1)
        mat = panel.fields[field_name][lo: self.i + 1]
        if codes is None:
            return ([str(c) for c in self.bundle.codes], mat)
        if isinstance(codes, (str, np.str_)):
            codes = [codes]
        cols = []
        idx = []
        for c in codes:
            j = self.bundle.code_index.get(normalize_code(c))
            if j is not None:
                cols.append(str(self.bundle.codes[j]))
                idx.append(j)
        if not idx:
            return ([], np.empty((mat.shape[0], 0), dtype=np.float32))
        return (cols, mat[:, np.array(idx, dtype=int)])

    def latest_values(self, field_name: str, codes=None):
        field_name = canonical_field(field_name)
        panel = self.bundle.panel
        row = panel.fields[field_name][self.i]
        if codes is None:
            return ([str(c) for c in self.bundle.codes], row)
        if isinstance(codes, (str, np.str_)):
            codes = [codes]
        cols, idx = [], []
        for c in codes:
            j = self.bundle.code_index.get(normalize_code(c))
            if j is not None:
                cols.append(str(self.bundle.codes[j]))
                idx.append(j)
        if not idx:
            return ([], np.array([], dtype=float))
        return (cols, row[np.array(idx, dtype=int)])

    def _listed_row(self, i: int) -> np.ndarray:
        return self.bundle.listed_mask()[i]

    def is_tradable(self, code) -> bool:
        code = self.resolve_code(code)
        j = self.bundle.code_index.get(code)
        if j is None:
            return False
        i = self.i
        ts = self.bundle.field("tradestatus")[i, j]
        close = self.bundle.field("close")[i, j]
        if not (np.isfinite(ts) and ts >= 0.5 and np.isfinite(close)):
            return False
        return bool(self._listed_row(i)[j])

    def is_delisted(self, code) -> bool:
        """True once the listing has ended (out_date reached) for a panel stock."""
        code = self.resolve_code(code)
        j = self.bundle.code_index.get(code)
        if j is None:
            return False
        return self.i >= int(self.bundle.universe.out_is[j])

    def universe_list(self, tradable: bool = True, exclude_st: bool = True,
                      min_history: int = 0, as_of=None) -> list:
        i = self.i if as_of is None else self.bundle.i_of(as_of)
        listed = self._listed_row(i)
        mask = listed.copy()
        if tradable:
            ts = self.bundle.field("tradestatus")[i]
            close = self.bundle.field("close")[i]
            mask &= np.isfinite(ts) & (ts >= 0.5) & np.isfinite(close)
        if exclude_st:
            isst = self.bundle.field("isst")[i]
            mask &= ~(np.isfinite(isst) & (isst > 0.5))
        if min_history > 0:
            lo = max(0, i - int(min_history) + 1)
            q = self.bundle.field("qfq_close")[lo: i + 1]
            mask &= np.isfinite(q).sum(axis=0) >= min_history
        return [str(c) for c in self.bundle.codes[mask]]

    def top_by(self, field_name: str, top_n: int, lookback: int, exclude_st: bool = True) -> list:
        i = self.i
        lo = max(0, i - int(lookback) + 1)
        mat = self.bundle.field(field_name)[lo: i + 1]
        with np.errstate(invalid="ignore"):
            score = np.nanmean(mat, axis=0)
        mask = np.isfinite(score)
        listed = self._listed_row(i)
        mask &= listed
        ts = self.bundle.field("tradestatus")[i]
        mask &= np.isfinite(ts) & (ts >= 0.5)
        if exclude_st:
            isst = self.bundle.field("isst")[i]
            mask &= ~(np.isfinite(isst) & (isst > 0.5))
        idx = np.flatnonzero(mask)
        if len(idx) == 0:
            return []
        order = idx[np.argsort(-score[idx], kind="stable")]
        return [str(self.bundle.codes[j]) for j in order[: int(top_n)]]

    def bar_of(self, code, date=None) -> dict | None:
        code = self.resolve_code(code)
        j = self.bundle.code_index.get(code)
        if j is None:
            return None
        i = self.i if date is None else self.bundle.i_of(date)
        p = self.bundle.panel
        out = {"code": code, "date": str(self.bundle.dates[i])}
        for name in ("open", "high", "low", "close", "preclose", "volume", "amount",
                     "turn", "pctchg", "isst", "tradestatus",
                     "qfq_open", "qfq_close", "qfq_high", "qfq_low"):
            v = p.fields[name][i, j]
            out[name] = float(v) if np.isfinite(v) else None
        return out

    def index_history(self, index_code: str = "sh000300", window: int | None = None,
                      field: str = "close") -> pd.Series:
        self._ensure_index_arrays()
        code = infer_index_code(index_code)
        if code not in self.bundle.index_dict:
            raise KeyError(f"index {index_code!r} not published; available: "
                           f"{sorted(self.bundle.index_dict)}")
        arr = self._index_arr_close[code] if field == "close" else self._index_arr_open[code]
        hi = self.i + 1
        lo = 0 if window is None else max(0, hi - int(window))
        dates = [str(d) for d in self.bundle.dates[lo:hi]]
        return pd.Series(arr[lo:hi], index=pd.Index(dates, name="date"), name=code)

    def index_value(self, index_code: str = "sh000300", field: str = "close") -> float:
        self._ensure_index_arrays()
        code = infer_index_code(index_code)
        if code not in self.bundle.index_dict:
            raise KeyError(f"index {index_code!r} not published")
        arr = self._index_arr_close[code] if field == "close" else self._index_arr_open[code]
        v = arr[self.i]
        return float(v) if np.isfinite(v) else np.nan

    # ------------------------------------------------------------ main loop
    def run(self) -> dict:
        self.ctx._reset_orders()
        if hasattr(self.strategy, "on_init"):
            self.strategy.on_init(self.ctx)
        self._collect_orders(self.start_i - 1 if self.start_i > 0 else 0,
                             signal_nav=self.capital, signal_date="")
        for i in range(self.start_i, self.end_i + 1):
            self.i = i
            self.date = str(self.bundle.dates[i])
            self._day_fee = 0.0
            self._day_div = 0.0
            self._day_traded = 0.0
            self._begin_day(i)
            # cash interest (run2 engine 1.0.3): idle cash earns `cash_rate`/252 per day
            self._day_interest = 0.0
            if self.cash_rate > 0.0 and self.cash > 0.0:
                self._day_interest = self.cash * self.cash_rate / 252.0
                self.cash += self._day_interest
            self._process_corporate_actions(i)
            self._process_delistings(i)
            self._release_t1(i)
            if self.exec_timing == "next_open":
                self._execute_pending(i, "open")
                self._mark_to_market(i)
            else:  # next_close: mark, then execute at today's close, then re-mark
                self._mark_to_market(i)
                self._execute_pending(i, "close")
                self._mark_to_market(i)
            nav = self._nav()
            if not np.isfinite(nav):
                raise RuntimeError(f"non-finite NAV on {self.date}")
            self.nav = nav
            self._check_nav_identity(i)
            self._record_nav(i)
            ctx = self.ctx
            if hasattr(self.strategy, "on_bar"):
                self.strategy.on_bar(ctx)
            if i in self.rebalance_set and hasattr(self.strategy, "on_rebalance"):
                self.strategy.on_rebalance(ctx)
            self._collect_orders(i, signal_nav=self.nav, signal_date=self.date)
            if self.verbose and (i - self.start_i) % 250 == 0:
                print(f"[bt] {self.date} nav={self.nav:,.2f}")
        self._flush_pending()
        return self._build_result()

    def _flush_pending(self):
        for o in self.pending_orders:
            if o["status"] != "filled":
                o["status"] = "expired" if o["status"] == "expired" else "unfilled_at_end"
                o["reason"] = o["reason"] or "end_of_backtest"
                self._log_order(o)
        self.pending_orders = []
        if self.pending_book is not None:
            book = self.pending_book
            actual = {c: (round(p.value() / self.nav, 6) if self.nav > 0 else 0.0)
                      for c, p in self.positions.items()}
            self.add_event("target_book_unfilled_at_end", signal_date=book.signal_date,
                           targets={c: round(w, 6) for c, w in book.weights.items()},
                           actual=actual)
            self.pending_book = None

    def _begin_day(self, i: int):
        # advance hold counters / last known prices for reporting
        for pos in self.positions.values():
            if pos.kind == "stock":
                pos.hold_days += 1
        self._prev_nav = float(self.nav)
        self._prev_snapshot = {
            c: (p.kind, float(p.shares), float(p.units), float(p.last_price))
            for c, p in self.positions.items()
        }
        self._trade_cursor = len(self.trades)

    # --------------------------------------------------- corporate actions
    def _corporate_action_multiplier(self, i: int, j: int):
        """Total-return share multiplier for one stock between rows i-1 and i.

        Primary (frozen) approximation: dividends are NOT credited to cash; the qfq
        panel is treated as a total-return series and a corporate action multiplies
        the holding by m = (qfq total-return factor) / (raw price ratio), i.e.
        dividends are implicitly reinvested at the ex-date price.  When the provider
        publishes a *reference* preclose (P0 != preclose, i.e. the ex-rights
        reference price), m = raw_close[i-1] / preclose[i] is used instead because
        it is computed from 2-decimal prices rather than rounded qfq prices.
        Returns (m, source) or (None, reason) when no reliable action is detected.
        """
        p = self.bundle.panel
        c_prev = float(p.fields["close"][i - 1, j])
        c_now = float(p.fields["close"][i, j])
        if not (np.isfinite(c_prev) and np.isfinite(c_now) and c_prev > 0 and c_now > 0):
            return None, "no_raw_prices"
        pre_now = float(p.fields["preclose"][i, j])
        q_prev = float(p.fields["qfq_close"][i - 1, j])
        q_now = float(p.fields["qfq_close"][i, j])
        qfq_ok = (p.qfq_available and np.isfinite(q_prev) and np.isfinite(q_now)
                  and q_prev > 0 and q_now > 0)
        g = (q_now / q_prev) if qfq_ok else np.nan
        r = c_now / c_prev
        m = None
        source = ""
        # path 1: provider preclose looks like the ex-rights reference price.  The
        # dataset contract publishes this on ex-dates (see data/scripts/merge_panel.py),
        # so it is authoritative; the qfq ratio is used as a cross-check only.
        if np.isfinite(pre_now) and pre_now > 0 and (c_prev - pre_now) > 0.0105:
            m_ref = c_prev / pre_now
            if 0.2 <= m_ref <= 3.0:
                m, source = m_ref, "preclose_reference"
                if qfq_ok:
                    implied = g / r
                    if abs(m_ref / implied - 1.0) > 0.02:
                        source = "preclose_reference_mismatch"
        # path 2: derive everything from the qfq / raw ratio (providers that publish a
        # plain previous close instead of the reference price)
        if m is None and qfq_ok:
            m_est = g / r
            # qfq prices are rounded; suppress rounding noise
            tol = max(0.0025, 0.6 * (0.01 / q_prev + 0.01 / q_now))
            if abs(m_est - 1.0) > tol:
                m, source = m_est, "qfq_ratio"
        if m is None or not np.isfinite(m):
            # last resort: an unregistered capital action (see _infer_missing_action).
            # Switchable via `infer_missing_actions=False` (documented in API.md) for
            # analysis that wants the strict frozen rule set only; default True is the
            # behaviour all reported results were produced with.
            if self.infer_missing_actions:
                m, source = self._infer_missing_action(i, j)
            if m is None:
                return None, "no_action"
        if not (0.2 <= m <= 3.0):
            return None, "implausible_multiplier"
        if abs(m - 1.0) < 5e-4:
            return None, "below_tolerance"
        return float(m), source

    def _infer_missing_action(self, i: int, j: int) -> tuple:
        """Infer an unregistered value-preserving capital action from a raw price jump.

        A-share non-ST daily moves beyond the board band are impossible except in a new
        listing's first trading days (no price limit) or special resumption days.  When
        neither the reference `preclose` nor the qfq factor captured an ex-rights event,
        the raw close level drops permanently (e.g. 送股/转增 whose factor row is missing
        from the source).  In that case we apply a value-preserving share multiplier
        m = close[i-1]/close[i] (drop-only, m in [1.1, 3.0]) after checking that the new
        level persists over the next few bars, and disclose it as an inferred action.
        """
        p = self.bundle.panel
        T = len(self.bundle.dates)
        if i <= 0:
            return None, "no_history"
        c_prev = float(p.fields["close"][i - 1, j])
        c_now = float(p.fields["close"][i, j])
        if not (np.isfinite(c_prev) and np.isfinite(c_now) and c_prev > 0 and c_now > 0):
            return None, "no_raw_prices"
        pre_now = float(p.fields["preclose"][i, j])
        if np.isfinite(pre_now) and (c_prev - pre_now) > 0.0105:
            return None, "reference_present"
        first = self._first_bar_index(j)
        if first < 0 or (i - first) < max(1, int(self.ipo_no_limit_bars)):
            return None, "ipo_window"
        code = str(self.bundle.codes[j])
        board = board_of(code)
        band, _ = limit_rates(self.bundle.date_at(i), board, False)
        r = c_now / c_prev
        if not (1.10 <= 1.0 / r <= 3.0):
            return None, "not_a_split_like_drop"
        if r > 1.0 - band * 1.15:
            return None, "within_band"
        # persistence: the new price level should survive the next ~5 traded bars
        checks = 0
        for k in range(i + 1, min(i + 6, T)):
            ck = float(p.fields["close"][k, j])
            if not np.isfinite(ck) or ck <= 0:
                continue
            if abs(ck / c_now - 1.0) > 0.15:
                return None, "not_persistent"
            checks += 1
        if checks < 1:
            return None, "no_confirmation"
        return float(c_prev / c_now), "inferred_missing_action"

    def _process_corporate_actions(self, i: int):
        if self.dividend_mode == "none" or i == 0 or not self.positions:
            return
        qfq_ok = self.bundle.panel.qfq_available
        if not qfq_ok and not self._corporate_action_warnings:
            self._corporate_action_warnings += 1
            self.add_event("corporate_actions_degraded",
                           note="qfq panel missing: only reference-preclose corporate "
                                "actions can be applied; total return may be understated")
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            if pos.kind != "stock" or pos.shares <= 0:
                continue
            j = self.bundle.code_index.get(code)
            if j is None:
                continue
            m, source = self._corporate_action_multiplier(i, j)
            if m is None:
                if source in ("implausible_multiplier",) and self._corporate_action_warnings < 50:
                    self._corporate_action_warnings += 1
                    self.add_event("corporate_action_warning", code=code, detail=source)
                continue
            old_shares = float(pos.shares)
            new_shares = old_shares * m
            c_now = float(self.bundle.panel.fields["close"][i, j])
            pos.shares = new_shares
            pos.cost_total = float(pos.cost_total)   # cost basis unchanged by the action
            pos.avg_cost = pos.cost_total / new_shares if new_shares > 0 else 0.0
            # informational: CNY value added relative to a price-only holding
            added_value = old_shares * c_now * (m - 1.0)
            if 1.0 < m < 1.5:
                # likely a cash-dividend total-return adjustment (splits have m >= 2)
                self._div_total += added_value
            self._corp_total += added_value
            self._day_div += added_value
            self.add_event("corporate_action", code=code, multiplier=float(m),
                           source=source, old_shares=float(old_shares),
                           new_shares=float(new_shares), implicit_dividend=float(added_value))
            if source == "preclose_reference_mismatch" and self._corporate_action_warnings < 50:
                self._corporate_action_warnings += 1
                self.add_event("corporate_action_warning", code=code,
                               note="reference preclose and qfq total-return factors disagree; "
                                    "reference preclose used")
            # virtual lot so FIFO / trade accounting can see the reinvested value
            self.trades.append(dict(
                date=self.date, code=code, kind="stock", side="adjust",
                shares=float(new_shares - old_shares), units=0.0, price=0.0, amount=0.0,
                commission=0.0, stamp=0.0, transfer=0.0, fee_total=0.0,
                slippage_cost=0.0, reason=f"corporate_action_{source}",
                target_weight=None, note=f"m={m:.6f} implicit dividend reinvestment",
            ))

    # --------------------------------------------------------- delistings
    def _process_delistings(self, i: int):
        if not self.positions:
            return
        uni = self.bundle.universe
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            if pos.kind != "stock" or pos.shares <= 0:
                continue
            j = self.bundle.code_index.get(code)
            if j is None:
                continue
            out_is = int(uni.out_is[j])
            if out_is > len(self.bundle.dates) - 1:
                continue
            if i < out_is:
                continue
            # forced liquidation: prefer today's open if it traded, else last known close
            openp = self.bundle.field("open")[i, j]
            ts = self.bundle.field("tradestatus")[i, j]
            px = float(openp) if (np.isfinite(openp) and np.isfinite(ts) and ts >= 0.5) else float(pos.last_price)
            if not np.isfinite(px) or px <= 0:
                px = float(pos.avg_cost) if np.isfinite(pos.avg_cost) else 0.0
            shares = pos.shares
            amount = shares * px
            if px > 0 and shares > 0:
                f = self.fees.fees("sell", amount, self.date)
                self.cash += amount - f["total"]
                self._day_fee += f["total"]
                self._fee_total += f["total"]
                self._day_traded += amount
                self._traded_amount += amount
                self.trades.append(dict(
                    date=self.date, code=code, kind="stock", side="sell", shares=float(shares),
                    units=0.0, price=float(px), amount=float(round_cny(amount)),
                    commission=f["commission"], stamp=f["stamp"], transfer=f["transfer"],
                    fee_total=f["total"], slippage_cost=0.0, reason="delisting_forced_liquidation",
                    target_weight=0.0, note="forced liquidation (delisted / out_date reached)",
                ))
                self.add_event("delisting_forced_liquidation", code=code, shares=float(shares),
                               price=float(px), out_date=str(uni.out_date(j)))
            del self.positions[code]

    # --------------------------------------------------------------- T+1
    def _release_t1(self, i: int):
        for pos in self.positions.values():
            if pos.kind == "stock":
                pos.sellable = pos.shares

    # ----------------------------------------------------------- mark/nav
    def _mark_to_market(self, i: int):
        close = self.bundle.field("close")[i]
        valid = np.isfinite(close)
        if valid.any():
            self._last_price[valid] = close[valid]
        self._ensure_index_arrays()
        for code, pos in self.positions.items():
            if pos.kind == "index":
                self._index_last[code] = self._index_value_at(code, "close", i)
                if np.isfinite(self._index_last[code]):
                    pos.last_price = self._index_last[code]
                    pos.last_price_date = self.date
                continue
            j = self.bundle.code_index[code]
            lp = self._last_price[j]
            if np.isfinite(lp):
                pos.last_price = float(lp)
                pos.last_price_date = self.date
            elif not np.isfinite(pos.last_price):
                pos.last_price = float(pos.avg_cost) if np.isfinite(pos.avg_cost) else 0.0

    def _index_value_at(self, code: str, field_name: str, i: int) -> float:
        self._ensure_index_arrays()
        if code not in self._index_arr_close:
            return np.nan
        arr = self._index_arr_close[code] if field_name == "close" else self._index_arr_open[code]
        v = arr[i]
        return float(v) if np.isfinite(v) else np.nan

    def _nav(self) -> float:
        market = sum(p.value() for p in self.positions.values())
        return float(self.cash + market)

    def _market_value(self) -> float:
        return float(sum(p.value() for p in self.positions.values()))

    def _mark_price_for(self, code: str, i: int) -> float:
        if code in self.bundle.index_dict:
            return self._index_value_at(code, "close", i)
        j = self.bundle.code_index.get(code)
        if j is None:
            return np.nan
        return float(self._last_price[j])

    def _check_nav_identity(self, i: int):
        """Account in two independent ways and require them to agree.

        1. NAV = cash + sum(position marks)  (portfolio bookkeeping)
        2. NAV_t - NAV_{t-1} = sum of per-name mark-to-market P&L at today's close
           + dividends credited - fees paid (cash-flow decomposition)
        """
        market = self._market_value()
        nav = self.cash + market
        ref = max(1.0, abs(nav))
        if abs(nav - self.nav) > 1e-6 * ref:
            raise RuntimeError(
                f"NAV identity violated on {self.date}: nav={self.nav!r} cash+market={nav!r}")
        if self.cash < -1e-6:
            raise RuntimeError(f"negative cash (leverage) on {self.date}: {self.cash!r}")
        prev_snap = getattr(self, "_prev_snapshot", {})
        prev_nav = getattr(self, "_prev_nav", self.capital)
        day_trades = self.trades[getattr(self, "_trade_cursor", 0):]
        by_code = {}
        for t in day_trades:
            by_code.setdefault(t["code"], []).append(t)
        delta = 0.0
        for code in set(prev_snap) | set(self.positions) | set(by_code):
            kind_prev, shares_prev, units_prev, price_prev = prev_snap.get(
                code, ("stock", 0, 0.0, np.nan))
            pos = self.positions.get(code)
            if pos is not None:
                mark = float(pos.last_price)
                kind_now = pos.kind
            else:
                mark = self._mark_price_for(code, i)
                kind_now = kind_prev
            if not np.isfinite(mark):
                mark = price_prev
            is_index = (kind_prev == "index" or kind_now == "index"
                        or any(t["kind"] == "index" for t in by_code.get(code, [])))
            if is_index:
                if np.isfinite(price_prev) and np.isfinite(mark):
                    delta += units_prev * (mark - price_prev)
                for t in by_code.get(code, []):
                    if t["side"] not in ("buy", "sell"):
                        continue
                    units = float(t.get("units", 0.0))
                    if t["side"] == "buy":
                        delta += units * (mark - t["price"])
                    else:
                        delta += units * (t["price"] - mark)
            else:
                if np.isfinite(price_prev) and np.isfinite(mark) and shares_prev:
                    delta += shares_prev * (mark - price_prev)
                for t in by_code.get(code, []):
                    if t["side"] not in ("buy", "sell"):
                        continue
                    shares = float(t.get("shares", 0))
                    if t["side"] == "buy":
                        delta += shares * (mark - t["price"])
                    else:
                        delta += shares * (t["price"] - mark)
        expected = prev_nav + delta + self._day_div - self._day_fee + self._day_interest
        if abs(expected - self.nav) > 1e-5 * max(1.0, abs(self.nav)):
            detail = {
                "prev_nav": round(prev_nav, 4), "mark_pnl": round(delta, 4),
                "day_div": round(self._day_div, 4), "day_fee": round(self._day_fee, 4),
                "expected": round(expected, 4), "actual": round(self.nav, 4),
                "prev_snapshot": {c: [float(s), float(p)] for c, (k, s, u, p)
                                  in prev_snap.items()},
                "positions": {c: [float(p.shares), float(p.last_price)]
                              for c, p in self.positions.items()},
                "trades_today": [(t["code"], t["side"], t.get("shares"), t.get("price"))
                                 for t in self.trades[getattr(self, "_trade_cursor", 0):]],
            }
            raise RuntimeError(f"P&L decomposition violated on {self.date}: {detail}")

    def _record_nav(self, i: int):
        market = self._market_value()
        prev_nav = self.nav_records[-1]["nav"] if self.nav_records else self.capital
        nav = self.cash + market
        self.nav = nav
        rec = {
            "date": self.date,
            "nav": float(nav),
            "cash": float(self.cash),
            "market_value": float(market),
            "exposure": float(market / nav) if nav > 0 else 0.0,
            "ret": float(nav / prev_nav - 1.0) if prev_nav > 0 else 0.0,
            "n_positions": int(len(self.positions)),
            "fees": float(self._day_fee),
            "dividends": float(self._day_div),
            "interest": float(self._day_interest),
            "traded_amount": float(self._day_traded),
            "turnover": float(self._day_traded / nav) if nav > 0 else 0.0,
            "regime": self.regime_labels.get(self.date) if self.regime_labels else None,
        }
        self.nav_records.append(rec)
        for code, pos in self.positions.items():
            self.holding_records.append({
                "date": self.date, "code": code, "kind": pos.kind,
                "shares": float(pos.shares), "units": float(pos.units),
                "price": float(pos.last_price), "value": float(pos.value()),
                "weight": float(pos.value() / nav) if nav > 0 else 0.0,
                "cost_total": float(pos.cost_total),
            })

    # ----------------------------------------------------------- execution
    def _slippage(self, price: float, side: str) -> float:
        if self.slippage_bps == 0 or not np.isfinite(price):
            return float(price)
        adj = float(price) * (1.0 + self.slippage_bps / 1e4 * (1 if side == "buy" else -1))
        return round_cny(adj)

    def _limit_prices(self, j: int, i: int, code: str):
        p = self.bundle.panel
        pre = p.fields["preclose"][i, j]
        is_st = bool(p.fields["isst"][i, j] > 0.5) if np.isfinite(p.fields["isst"][i, j]) else False
        board = board_of(code)
        return limit_prices(float(pre), self.bundle.date_at(i), board, is_st)

    def _first_bar_index(self, j: int) -> int:
        return int(self._ensure_first_bar()[j])

    def max_affordable_shares(self, price: float, cash: float, lot: int | None = None) -> int:
        """Largest lot multiple affordable including fees at `price`."""
        lot = self.lot if lot is None else int(lot)
        if not np.isfinite(price) or price <= 0 or cash <= 0:
            return 0
        qty = int(math.floor(cash / (price * (1.0 + self.fees.commission_bps / 1e4
                                              + self.fees.transfer_rate)) / lot)) * lot
        while qty > 0:
            amount = qty * price
            f = self.fees.fees("buy", amount, self.date)
            if amount + f["total"] <= cash + 1e-9:
                return qty
            qty -= lot
        return 0

    # ---- generic fills -----------------------------------------------------
    def _fill_stock(self, code: str, side: str, qty: float, i: int, price_field: str,
                    reason: str, note: str = "", target_weight: float | None = None) -> tuple:
        """Attempt a stock fill. Returns (filled_shares, status, reason_code).

        status in {'filled', 'partial', 'blocked'}; reason_code explains blocks.
        """
        j = self.bundle.code_index.get(code)
        if j is None:
            return 0, "blocked", "unknown_code"
        if side == "buy":
            qty = int(qty)  # buys must be whole 100-share lots
        else:
            qty = float(qty)
        if qty <= 0:
            return 0, "blocked", "zero_qty"
        p = self.bundle.panel
        ipo_i = self._first_bar_index(j)
        if side == "buy":
            # listing status is known from metadata even before the first bar
            if i < int(self.bundle.universe.ipo_is[j]):
                return 0, "blocked", "not_listed"
            if i >= int(self.bundle.universe.out_is[j]):
                return 0, "blocked", "not_listed"
        ts = p.fields["tradestatus"][i, j]
        raw = float(p.fields[price_field][i, j]) if price_field in p.fields else np.nan
        if not (np.isfinite(ts) and ts >= 0.5) or not np.isfinite(raw) or raw <= 0:
            return 0, "blocked", "suspended"
        if side == "buy":
            isst = p.fields["isst"][i, j]
            is_st = bool(np.isfinite(isst) and isst > 0.5)
            if is_st and not self.allow_st_buy:
                return 0, "blocked", "st_not_buyable"
            if i == ipo_i and not self.allow_ipo_day_trade:
                return 0, "blocked", "ipo_day"
            up, _ = self._limit_prices(j, i, code)
            if np.isfinite(up) and raw >= up - _PRICE_EPS and i != ipo_i:
                return 0, "blocked", "limit_up"
        else:
            pos = self.positions.get(code)
            if pos is None or pos.kind != "stock" or pos.shares <= 0:
                return 0, "blocked", "no_position"
            qty = min(qty, float(pos.sellable))
            if qty <= 1e-9:
                return 0, "blocked", "t1_locked"
            _, down = self._limit_prices(j, i, code)
            if np.isfinite(down) and raw <= down + _PRICE_EPS and i != ipo_i:
                return 0, "blocked", "limit_down"
        limit_up, limit_down = self._limit_prices(j, i, code)
        px = self._slippage(raw, side)
        if np.isfinite(limit_up) and i != ipo_i:
            px = min(px, float(limit_up))
        if np.isfinite(limit_down) and i != ipo_i:
            px = max(px, float(limit_down))
        if px <= 0:
            return 0, "blocked", "bad_price"
        if side == "buy":
            qty = int(min(qty, self.max_affordable_shares(px, self.cash)))
            if qty <= 0:
                return 0, "blocked", "insufficient_cash"
        amount = float(qty) * px
        f = self.fees.fees(side, amount, self.date)
        slip_cost = abs(px - raw) * float(qty)
        if side == "buy":
            self.cash -= amount + f["total"]
            pos = self.positions.get(code)
            if pos is None:
                pos = Position(code=code, kind="stock", open_date=self.date)
                self.positions[code] = pos
            prev_cost = pos.cost_total
            pos.cost_total = prev_cost + amount + f["total"]
            pos.shares += qty
            pos.avg_cost = pos.cost_total / pos.shares
            pos.last_price = float(self._last_price[j]) if np.isfinite(self._last_price[j]) else px
        else:
            pos = self.positions[code]
            self.cash += amount - f["total"]
            sold_cost = pos.avg_cost * qty
            pos.cost_total = max(0.0, pos.cost_total - sold_cost)
            pos.shares -= qty
            pos.sellable = max(0.0, pos.sellable - qty)
            if pos.shares <= 1e-6:
                del self.positions[code]
            else:
                pos.avg_cost = pos.cost_total / pos.shares
        self._day_fee += f["total"]
        self._fee_total += f["total"]
        self._slippage_total += slip_cost
        self._day_traded += amount
        self._traded_amount += amount
        self.trades.append(dict(
            date=self.date, code=code, kind="stock", side=side, shares=float(qty),
            units=0.0, price=float(px), amount=float(round_cny(amount)),
            commission=f["commission"], stamp=f["stamp"], transfer=f["transfer"],
            fee_total=f["total"], slippage_cost=float(slip_cost), reason=reason,
            target_weight=None if target_weight is None else float(target_weight), note=note,
        ))
        return float(qty), "filled", ""

    def _fill_index(self, code: str, side: str, value: float | None = None,
                    units: float | None = None, i: int = None,
                    price_field: str = "open", reason: str = "", note: str = "",
                    target_weight: float | None = None) -> tuple:
        """Index proxy fill (benchmark / smoke use only; fractional units, no limit)."""
        i = self.i if i is None else i
        px = self._index_value_at(code, price_field, i)
        if not np.isfinite(px) or px <= 0:
            return 0.0, "blocked", "no_index_data"
        if units is None:
            if value is None or value <= 0:
                return 0.0, "blocked", "zero_qty"
            units = float(value) / float(px)
        else:
            units = float(units)
        amount = units * px
        if side == "buy":
            f = self.fees.fees("buy", amount, self.date)
            if amount + f["total"] > self.cash + 1e-9:
                afford = max(0.0, self.cash - self.fees.min_commission) / (px * (1 + self.fees.commission_bps / 1e4))
                units = float(afford)
                amount = units * px
                if units <= 0:
                    return 0.0, "blocked", "insufficient_cash"
                f = self.fees.fees("buy", amount, self.date)
            self.cash -= amount + f["total"]
            pos = self.positions.get(code)
            if pos is None:
                pos = Position(code=code, kind="index", open_date=self.date)
                self.positions[code] = pos
            pos.units += units
            pos.cost_total += amount + f["total"]
            pos.avg_cost = pos.cost_total / pos.units if pos.units else 0.0
        else:
            pos = self.positions.get(code)
            if pos is None or pos.units <= 0:
                return 0.0, "blocked", "no_position"
            units = min(units, pos.units)
            amount = units * px
            f = self.fees.fees("sell", amount, self.date)
            # index proxies are ETF-like: no stamp/transfer, commission only
            f = {"commission": f["commission"], "stamp": 0.0, "transfer": 0.0,
                 "total": f["commission"]}
            self.cash += amount - f["total"]
            sold_cost = pos.avg_cost * units
            pos.units -= units
            pos.cost_total = max(0.0, pos.cost_total - sold_cost)
            if pos.units <= 1e-12:
                del self.positions[code]
            else:
                pos.avg_cost = pos.cost_total / pos.units
        self._day_fee += f["total"]
        self._fee_total += f["total"]
        self._day_traded += amount
        self._traded_amount += amount
        self.trades.append(dict(
            date=self.date, code=code, kind="index", side=side, shares=0,
            units=float(units), price=float(px), amount=float(round_cny(amount)),
            commission=f["commission"], stamp=f.get("stamp", 0.0), transfer=f.get("transfer", 0.0),
            fee_total=f["total"], slippage_cost=0.0, reason=reason,
            target_weight=None if target_weight is None else float(target_weight), note=note,
        ))
        return float(units), "filled", ""

    # ------------------------------------------------- order collection
    def _collect_orders(self, signal_i: int, signal_nav: float, signal_date: str):
        ctx = self.ctx
        if ctx._pending_targets is not None:
            weights = dict(ctx._pending_targets)
            unknown = [c for c in weights if c not in self.bundle.code_index
                       and c not in self.bundle.index_dict]
            for c in unknown:
                self.add_event("unknown_target_code", code=c,
                               note="code not in panel or index data; dropped")
                weights.pop(c, None)
            self.pending_book = _TargetBook(weights=weights, base_nav=float(signal_nav),
                                            created_i=signal_i, signal_date=signal_date)
        elif ctx._pending_partial:
            current = {c: p.value() / signal_nav for c, p in self.positions.items()
                       if signal_nav > 0}
            weights = dict(current)
            for c, w in ctx._pending_partial.items():
                if w <= 0:
                    weights.pop(c, None)
                else:
                    weights[c] = float(w)
            total = sum(weights.values())
            if total > 1.0 + 1e-9:
                scale = 1.0 / total
                weights = {c: w * scale for c, w in weights.items()}
                self.add_event("targets_normalized", note=f"sum was {total:.4f}")
            unknown = [c for c in weights if c not in self.bundle.code_index
                       and c not in self.bundle.index_dict]
            for c in unknown:
                weights.pop(c, None)
            self.pending_book = _TargetBook(weights=weights, base_nav=float(signal_nav),
                                            created_i=signal_i, signal_date=signal_date)
        for spec in ctx._orders:
            code = spec["code"]
            kind = spec["kind"]
            if code not in self.bundle.code_index and code not in self.bundle.index_dict:
                self.add_event("unknown_order_code", code=code, note="code not in data; dropped")
                continue
            o = dict(spec)
            o["side"] = kind
            o["created_i"] = signal_i
            o["created_date"] = signal_date or (str(self.bundle.dates[signal_i]) if signal_i >= 0 else "")
            o["expires_i"] = signal_i + self.max_pending_days
            o["base_nav"] = float(signal_nav)
            o["filled_value"] = 0.0
            o["filled_shares"] = 0
            o["filled_units"] = 0.0
            o["status"] = "pending"
            o["reason"] = ""
            if kind == "buy":
                if o.get("shares") is not None:
                    o["requested_shares"] = int(o["shares"])
                    o["requested"] = int(o["shares"])
                elif o.get("value") is not None:
                    o["requested_value"] = float(o["value"])
                    o["requested"] = float(o["value"])
                else:
                    o["requested_value"] = float(o["weight"]) * float(signal_nav)
                    o["requested"] = float(o["weight"])
            else:
                if o.get("shares") is not None:
                    o["requested"] = int(o["shares"])
                elif o.get("value") is not None:
                    o["requested"] = float(o["value"])
                elif o.get("fraction") is not None:
                    o["requested"] = float(o["fraction"])
                else:
                    o["requested"] = 1.0
            self.pending_orders.append(o)
        ctx._reset_orders()

    # ------------------------------------------------- execution of targets
    def _execute_pending(self, i: int, price_field: str):
        # 1) explicit sells
        self._execute_explicit_orders(i, price_field, sides=("sell",))
        # 2) target-book sells / trims
        if self.pending_book is not None:
            self._execute_target_book(i, price_field, phase="sell")
        # 3) explicit buys
        self._execute_explicit_orders(i, price_field, sides=("buy",))
        # 4) target-book buys
        if self.pending_book is not None:
            self._execute_target_book(i, price_field, phase="buy")
            self._finalize_target_book(i)
        # expire stale explicit orders
        keep = []
        for o in self.pending_orders:
            if o["status"] in ("filled",):
                self._log_order(o)
                continue
            if i > o["expires_i"] or i > self.end_i:
                o["status"] = "expired"
                o["reason"] = o.get("reason") or "max_pending_days"
                self._log_order(o)
                continue
            keep.append(o)
        self.pending_orders = keep

    def _log_order(self, o: dict):
        self.orders.append({
            "created_date": o.get("created_date", ""),
            "filled_date": o.get("filled_date", ""),
            "code": o["code"],
            "side": o["side"],
            "kind": o["kind"],
            "requested": o.get("requested", np.nan),
            "filled": float(o.get("filled_shares", 0) if o["side"] == "sell" else
                            o.get("filled_value", 0.0)),
            "status": o["status"],
            "reason": o["reason"],
            "note": o.get("note", ""),
        })

    def _raw_exec_price(self, code: str, i: int, price_field: str) -> float:
        j = self.bundle.code_index.get(code)
        if j is None:
            return np.nan
        return float(self.bundle.field(price_field)[i, j])

    def _execute_explicit_orders(self, i: int, price_field: str, sides=("sell", "buy")):
        for o in list(self.pending_orders):
            if o["status"] == "filled" or o["side"] not in sides:
                continue
            if self._is_index_code(o["code"]):
                self._execute_explicit_index(o, i, price_field)
            else:
                self._execute_explicit_stock(o, i, price_field)

    # -- stock explicit orders ------------------------------------------------
    def _execute_explicit_stock(self, o: dict, i: int, price_field: str):
        code = o["code"]
        j = self.bundle.code_index.get(code)
        if j is None:
            o["status"], o["reason"] = "filled", "unknown_code"
            return
        pos = self.positions.get(code)
        if o["side"] == "sell":
            held = pos.shares if (pos is not None and pos.kind == "stock") else 0
            if held <= 0:
                o["status"], o["reason"] = "filled", "no_position"
                return
            if "requested_shares" not in o:
                if o.get("shares") is not None:
                    o["requested_shares"] = int(o["shares"])
                elif o.get("fraction") is not None:
                    frac = float(o["fraction"])
                    o["requested_shares"] = float(held) if frac >= 0.999 else int(math.floor(held * frac))
                else:  # value-denominated
                    raw = self._raw_exec_price(code, i, price_field)
                    if not np.isfinite(raw) or raw <= 0:
                        o["reason"] = "suspended"
                        return
                    o["requested_shares"] = int(math.floor(float(o["value"]) / raw))
            rem = float(o["requested_shares"]) - float(o.get("filled_shares", 0))
            if rem <= 1e-9:
                o["status"], o["reason"] = "filled", "done"
                return
            filled, status, reason = self._fill_stock(
                code, "sell", rem, i, price_field, reason="order_sell",
                note=o.get("note", ""))
            if filled > 0:
                o["filled_shares"] = float(o.get("filled_shares", 0)) + float(filled)
                o["filled_date"] = self.date
                if float(o["requested_shares"]) - o["filled_shares"] <= 1e-9:
                    o["status"] = "filled"
                else:
                    o["reason"] = "partial"
            else:
                o["reason"] = reason
            return
        # buy
        if not np.isfinite(self._raw_exec_price(code, i, price_field)):
            o["reason"] = "suspended"
            return
        if "requested_value" in o:
            rem_value = float(o["requested_value"]) - float(o.get("filled_value", 0.0))
            if rem_value <= 0:
                o["status"], o["reason"] = "filled", "done"
                return
            raw = self._raw_exec_price(code, i, price_field)
            req_shares = int(math.floor(rem_value / raw / self.lot)) * self.lot
            if req_shares <= 0:
                o["reason"] = "value_below_one_lot"
                return
        else:
            req_shares = int(o.get("requested_shares", o.get("shares", 0))) - \
                int(o.get("filled_shares", 0))
            if req_shares <= 0:
                o["status"], o["reason"] = "filled", "done"
                return
        filled, status, reason = self._fill_stock(
            code, "buy", req_shares, i, price_field, reason="order_buy", note=o.get("note", ""))
        if filled > 0:
            o["filled_shares"] = int(o.get("filled_shares", 0)) + int(filled)
            o["filled_value"] = float(o.get("filled_value", 0.0)) + float(filled) * float(
                self._raw_exec_price(code, i, price_field))
            o["filled_date"] = self.date
            total_req = int(o.get("requested_shares", req_shares)) if "requested_shares" in o else None
            if total_req is not None and total_req - o["filled_shares"] <= 0:
                o["status"] = "filled"
            elif "requested_value" in o and float(o["requested_value"]) - o["filled_value"] <= max(
                    5.0, 1e-3 * float(o["requested_value"])):
                o["status"] = "filled"
            else:
                o["reason"] = "partial_cash"
        else:
            o["reason"] = reason

    # -- index explicit orders (benchmark proxy) ------------------------------
    def _execute_explicit_index(self, o: dict, i: int, price_field: str):
        code = o["code"]
        px = self._index_value_at(code, price_field, i)
        if not np.isfinite(px) or px <= 0:
            o["reason"] = "no_index_data"
            return
        pos = self.positions.get(code)
        held_units = pos.units if (pos is not None and pos.kind == "index") else 0.0
        if o["side"] == "sell":
            if held_units <= 0:
                o["status"], o["reason"] = "filled", "no_position"
                return
            if "requested_units" not in o:
                if o.get("shares") is not None:
                    o["requested_units"] = float(o["shares"])
                elif o.get("fraction") is not None:
                    o["requested_units"] = held_units * float(o["fraction"])
                elif o.get("value") is not None:
                    o["requested_units"] = float(o["value"]) / px
                else:
                    o["requested_units"] = held_units
            rem = max(0.0, float(o["requested_units"]) - float(o.get("filled_units", 0.0)))
            if rem <= 1e-9:
                o["status"], o["reason"] = "filled", "done"
                return
            units, status, reason = self._fill_index(
                code, "sell", units=min(rem, held_units), i=i, price_field=price_field,
                reason="order_sell", note=o.get("note", ""))
            if units > 0:
                o["filled_units"] = float(o.get("filled_units", 0.0)) + float(units)
                o["filled_date"] = self.date
                if float(o["requested_units"]) - o["filled_units"] <= 1e-9:
                    o["status"] = "filled"
            else:
                o["reason"] = reason
            return
        if "requested_value" not in o:
            if o.get("weight") is not None:
                o["requested_value"] = float(o["weight"]) * float(o.get("base_nav", self.nav))
            elif o.get("value") is not None:
                o["requested_value"] = float(o["value"])
            else:
                o["requested_value"] = float(o.get("shares", 0)) * px
        rem_value = float(o["requested_value"]) - float(o.get("filled_value", 0.0))
        if rem_value <= 0:
            o["status"], o["reason"] = "filled", "done"
            return
        units, status, reason = self._fill_index(
            code, "buy", value=min(rem_value, self.cash), i=i, price_field=price_field,
            reason="order_buy", note=o.get("note", ""))
        if units > 0:
            o["filled_value"] = float(o.get("filled_value", 0.0)) + float(units) * px
            o["filled_date"] = self.date
            if float(o["requested_value"]) - o["filled_value"] <= max(5.0, 1e-3 * float(o["requested_value"])):
                o["status"] = "filled"
            else:
                o["reason"] = "partial_cash"
        else:
            o["reason"] = reason

    # --------------------------------------------------- target book engine
    def _target_value(self, book: _TargetBook, code: str) -> float:
        return float(book.weights.get(code, 0.0)) * book.base_nav

    def _desired_shares(self, code: str, target_value: float, price: float) -> int:
        if target_value <= 0 or not np.isfinite(price) or price <= 0:
            return 0
        return int(math.floor(target_value / price / self.lot)) * self.lot

    def _missing_bar_reason(self, code: str, i: int) -> str:
        """Why a stock has no usable bar today: not listed yet / delisted vs suspended."""
        j = self.bundle.code_index.get(code)
        if j is None:
            return "unknown_code"
        uni = self.bundle.universe
        if i < int(uni.ipo_is[j]) or i >= int(uni.out_is[j]):
            return "not_listed"
        return "suspended"

    def _notify_block(self, book: _TargetBook, code: str, side: str, reason: str):
        """Log the first time a rebalance leg is blocked (limit board / suspension)."""
        if not hasattr(self, "_block_notified"):
            self._block_notified = set()
        key = (book.created_i, code, side, reason)
        if key in self._block_notified:
            return
        self._block_notified.add(key)
        self.add_event("target_blocked", code=code, side=side, reason=reason,
                       signal_date=book.signal_date,
                       target_weight=book.weights.get(code))

    def _execute_target_book(self, i: int, price_field: str, phase: str):
        book = self.pending_book
        assert book is not None
        # index targets first are handled inside phase 'buy'/'sell' too
        if phase == "sell":
            for code in sorted(self.positions.keys()):
                pos = self.positions[code]
                tw = book.weights.get(code, 0.0)
                target_value = tw * book.base_nav
                if pos.kind == "index":
                    px = self._index_value_at(code, price_field, i)
                    if not np.isfinite(px) or px <= 0:
                        continue
                    cur_val = pos.units * px
                    if cur_val <= target_value + max(5.0, self.tolerance_pct * book.base_nav):
                        continue
                    sell_val = cur_val - target_value
                    self._fill_index(code, "sell", value=sell_val, i=i,
                                     price_field=price_field, reason="rebalance")
                    continue
                j = self.bundle.code_index.get(code)
                if j is None:
                    continue
                raw = float(self.bundle.field(price_field)[i, j])
                if pos.shares <= 0:
                    continue
                if not np.isfinite(raw) or raw <= 0:
                    self._notify_block(book, code, "sell",
                                       self._missing_bar_reason(code, i))
                    continue
                if tw <= 0.0:
                    # FIX (run2 engine 1.0.2): full exit. The frozen 1.0.1 engine
                    # applied the one-lot rounding filter below to *every* sell,
                    # so a position of exactly one lot (or an odd-lot remainder)
                    # was never sold and stayed as a permanent residual holding
                    # (e.g. V3 2026-10-19 605100/002142/600060). A full exit must
                    # be allowed to sell odd lots.
                    diff = float(pos.shares)
                    filled, status, reason = self._fill_stock(
                        code, "sell", diff, i, price_field,
                        reason="rebalance_full_exit", target_weight=0.0)
                    if filled <= 0 and status == "blocked":
                        self._notify_block(book, code, "sell", reason)
                    continue
                desired = self._desired_shares(code, target_value, raw)
                diff = pos.shares - max(desired, 0)
                if diff <= 0:
                    continue
                if diff * raw <= max(raw * self.lot, self.tolerance_pct * book.base_nav):
                    continue
                filled, status, reason = self._fill_stock(
                    code, "sell", diff, i, price_field, reason="rebalance", target_weight=tw)
                if filled <= 0 and status == "blocked":
                    self._notify_block(book, code, "sell", reason)
        else:  # buy phase
            # deterministic: largest target weight first, then code
            codes = sorted(book.weights.keys(),
                           key=lambda c: (-book.weights[c], c))
            for code in codes:
                tw = book.weights[code]
                if tw <= 0:
                    continue
                target_value = tw * book.base_nav
                pos = self.positions.get(code)
                if code in self.bundle.index_dict:
                    px = self._index_value_at(code, price_field, i)
                    if not np.isfinite(px) or px <= 0:
                        continue
                    cur_val = pos.units * px if pos is not None else 0.0
                    gap = target_value - cur_val
                    if gap <= max(5.0, self.tolerance_pct * book.base_nav):
                        continue
                    self._fill_index(code, "buy", value=min(gap, self.cash), i=i,
                                     price_field=price_field, reason="rebalance",
                                     target_weight=tw)
                    continue
                j = self.bundle.code_index.get(code)
                if j is None:
                    continue
                raw = float(self.bundle.field(price_field)[i, j])
                if not np.isfinite(raw) or raw <= 0:
                    self._notify_block(book, code, "buy",
                                       self._missing_bar_reason(code, i))
                    continue
                cur_shares = pos.shares if (pos is not None and pos.kind == "stock") else 0
                desired = self._desired_shares(code, target_value, raw)
                diff = desired - cur_shares
                if diff <= 0:
                    continue
                if diff * raw <= max(raw * self.lot, self.tolerance_pct * book.base_nav) and cur_shares > 0:
                    continue
                filled, status, reason = self._fill_stock(
                    code, "buy", diff, i, price_field, reason="rebalance", target_weight=tw)
                if filled <= 0 and status == "blocked":
                    self._notify_block(book, code, "buy", reason)

    def _finalize_target_book(self, i: int):
        book = self.pending_book
        if book is None:
            return
        # achieved when every target is within one lot / tolerance and no unexpected
        # holding remains (rounding into 100-share lots makes exact targets impossible)
        achieved = True
        for code, tw in book.weights.items():
            target_value = tw * book.base_nav
            pos = self.positions.get(code)
            cur = pos.value() if pos is not None else 0.0
            tol = self._book_tolerance(code, book)
            if abs(cur - target_value) > tol:
                achieved = False
                break
        if achieved:
            for code, pos in self.positions.items():
                if code in book.weights:
                    continue
                if abs(pos.value()) > self._book_tolerance(code, book):
                    achieved = False
                    break
        if achieved:
            self.pending_book = None
            return
        if i - book.created_i >= self.max_pending_days:
            missing = []
            for code, tw in book.weights.items():
                pos = self.positions.get(code)
                cur_w = (pos.value() / self.nav) if pos is not None and self.nav > 0 else 0.0
                if abs(cur_w - tw) > self.tolerance_pct:
                    missing.append({"code": code, "target": float(tw), "actual": float(cur_w)})
            self.add_event("target_book_expired", signal_date=book.signal_date,
                           missing=missing[:20], n_missing=len(missing))
            self.pending_book = None

    def _book_tolerance(self, code: str, book: _TargetBook) -> float:
        tol = max(1.0, self.tolerance_pct * book.base_nav)
        j = self.bundle.code_index.get(code)
        if j is not None:
            price = float(self._last_price[j]) if np.isfinite(self._last_price[j]) else 0.0
            tol = max(tol, price * self.lot)
        return tol

    # ------------------------------------------------------------- results
    def holdings_final(self) -> dict:
        nav = self.nav
        out = {}
        for code, pos in self.positions.items():
            d = pos.to_dict(nav)
            j = self.bundle.code_index.get(code)
            if j is not None:
                d["board"] = board_of(code)
                d["isst"] = bool(self.bundle.field("isst")[self.end_i, j] > 0.5)
            out[code] = d
        return out

    def _build_result(self) -> dict:
        nav_df = pd.DataFrame(self.nav_records).set_index("date") if self.nav_records else \
            pd.DataFrame(columns=["nav", "cash", "market_value", "exposure", "ret"])
        trades_df = _rows_to_df(self.trades, TRADE_COLUMNS)
        orders_df = _rows_to_df(self.orders, ORDER_COLUMNS)
        events_df = _rows_to_df(self.events, EVENT_COLUMNS)
        holdings_df = _rows_to_df(self.holding_records, HOLDING_COLUMNS)
        settings = {
            "exec_timing": self.exec_timing,
            "slippage_bps": self.slippage_bps,
            "commission_bps": self.fees.commission_bps,
            "min_commission": self.fees.min_commission,
            "stamp_rule": "sell 0.1% < 2023-08-28, 0.05% >= 2023-08-28",
            "transfer_rate": self.fees.transfer_rate,
            "allow_st_buy": self.allow_st_buy,
            "allow_ipo_day_trade": self.allow_ipo_day_trade,
            "max_pending_days": self.max_pending_days,
            "dividend_mode": self.dividend_mode,
            "rebalance": self.rebalance if isinstance(self.rebalance, (str, int, type(None)))
            else "explicit_dates",
            "rebalance_days": int(len(self.rebalance_set)),
            "capital": self.capital,
            "lot": self.lot,
            "tolerance_pct": self.tolerance_pct,
            "infer_missing_actions": self.infer_missing_actions,
            "regime_labels_used": bool(self.regime_labels),
            "data_version": self.bundle.data_version,
        }
        return {
            "name": self.run_name,
            "start": self.bundle.date_at(self.start_i),
            "end": self.bundle.date_at(self.end_i),
            "capital": self.capital,
            "nav": nav_df,
            "trades": trades_df,
            "orders": orders_df,
            "events": events_df,
            "positions": holdings_df,
            "holdings_final": self.holdings_final(),
            "final_nav": float(self.nav),
            "final_cash": float(self.cash),
            "n_trading_days": int(len(self.nav_records)),
            "total_fees": float(self._fee_total),
            "total_dividends": float(self._div_total),          # implicit (reinvested) dividends
            "total_implicit_dividends": float(self._div_total),
            "total_corporate_action_value": float(self._corp_total),
            "total_slippage_cost": float(self._slippage_total),
            "total_traded_amount": float(self._traded_amount),
            "settings": settings,
            "warnings": list(self.bundle.warnings) + list(self.logs[:200]),
            "data_version": self.bundle.data_version,
        }
