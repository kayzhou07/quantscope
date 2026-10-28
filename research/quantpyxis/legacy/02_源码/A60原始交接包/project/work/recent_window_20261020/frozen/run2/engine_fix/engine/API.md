# qrun_1416 engine API (frozen v1.0.1)

Owner: **engine-dev** (`engine/`). Research strategies live in `os/` (Lead-owned) and
call this API. Everything here is pure pandas/numpy; no network, no randomness.

```python
import sys
sys.path.insert(0, r"...\qrun_1416")          # repo root (qrun_1416)
from engine.api import run_backtest, BuyHold, EqualWeight, MomentumTopN, Cash, print_summary
```

---

## 1. Single entry point

```python
result = run_backtest(
    strategy,                       # Strategy instance | Strategy subclass | dict | shortcut str
    start="2014-01-01",             # 'YYYY-MM-DD' (None -> 2014-01-01)
    end=None,                       # None -> last published trading day
    capital=100_000.0,              # CNY; no leverage; cash earns no interest
    data_dir=None,                  # default <repo>/data
    *,
    rebalance="M",                  # 'D'|'W'|'M'|'Q'|'A'|int K|list[date]|None
    benchmark="sh000300",           # str | list[str] | None (buy&hold index NAV + always 'cash')
    regime_labels=None,             # None | dict | Series | DataFrame | CSV path ('auto' = engine out/)
    slippage_bps=10.0,              # per-side slippage on the execution price
    commission_bps=2.5,             # commission rate, min 5 CNY per side
    min_commission=5.0,
    allow_st_buy=False,             # ST names not buyable by default (sells always OK)
    allow_ipo_day_trade=False,      # IPO first-day bar not traded by default
    max_pending_days=20,            # blocked orders / target book carry window
    exec_timing="next_open",        # "next_open" (default) | "next_close"
    dividend_mode="qfq_reinvest",   # primary total-return method; "none" = price-only
    dividend_min_pct=0.003,         # min size of a detected corporate action (fraction of price)
    warmup_days=320,                # extra history rows loaded before `start` (no leakage)
    require_panel=True,             # False -> index-only mode (smoke test before stock panel lands)
    infer_missing_actions=True,     # data-defect fallback, see §3 (default = reported results)
    cache_dir=None,                 # default <repo>/tmp/engine-dev/cache (mmap panel cache)
    name=None,                      # result name (defaults to strategy.name)
    verbose=False,
    output_json=None,               # path -> dump JSON result
)
```

`run_many({"bull": stratA, "bear": stratB}, start, end, **kwargs) -> dict[str, result]`
runs several named strategies over the same window;
`save_results_json(results, path)` / `save_result_json(result, path)` export JSON.

`data_status(data_dir=None)` returns which contract files are published (never raises).
Missing data raises `engine.data.DataNotReadyError` with the missing years/files listed.

### Strategy protocol

```python
from engine.core import Strategy

class MyStrategy(Strategy):
    name = "my_strategy"     # used in result["name"]
    warmup = 250             # informational: bars of history expected
    rebalance = "M"          # optional default; the run_backtest(rebalance=...) wins

    def on_init(self, ctx): ...
    def on_bar(self, ctx): ...        # every trading day, at the close
    def on_rebalance(self, ctx): ...  # engine-scheduled rebalance days, after on_bar
```

* Signals are computed from data **up to and including** day T's close; orders execute at
  day T+1's open (`exec_timing="next_open"`, the frozen default). `"next_close"` is the
  documented alternative timing (orders from T execute at T+1's close).
* `on_init` runs once before the first day; `ctx.set_targets(...)` called there executes on
  the first day's open.
* A dict spec is also accepted:
  `{"name": "x", "warmup": 0, "on_init": fn, "on_bar": fn, "on_rebalance": fn}`.
* Shortcut strings: `"cash"`, `"buyhold:sh000300"`, `"equalweight:50"`, `"momentum:20"`.

### Order semantics

```python
ctx.set_targets({"600000": 0.10, "000001": 0.05}, normalize=False, note="")
```
* Target weights are fractions of the **signal-day NAV**. Weights must be `>= 0`,
  sum `<= 1.0` (cash is the residual; no leverage is allowed; `normalize=True`
  rescales a sum > 1 down to 1).
* The book **replaces** all previous targets: held names absent from the dict are sold.
  It is retried daily until filled or `max_pending_days` elapses (suspension / limit
  board carry), then expires with a disclosed event.
* Fills use 100-share lots; buys round down to the largest affordable lot multiple;
  sells may be odd lots (corporate-action remainders, full exits).
* A new `set_targets` on a later day replaces the pending book (normal rebalance).

Explicit single-name orders (executed once, carried while blocked):
```python
ctx.buy(code, shares=1000)            # exact shares (multiple of 100)
ctx.buy(code, value=10_000)           # CNY value
ctx.buy(code, weight=0.05)            # fraction of signal-day NAV
ctx.sell(code, fraction=0.5)          # fraction of the current holding
ctx.sell(code, shares=500)
ctx.close(code); ctx.close_all()
ctx.cancel_pending()                  # drop queued orders and the pending target book
```

---

## 2. Context (`ctx`) reference — all helpers are truncated at the current bar

No-lookahead guarantee: every accessor only exposes rows `<= ctx.i` (today). A strategy
cannot obtain tomorrow's data through the Context. Day T+1's open is not known when the
signal is produced.

| member | returns / meaning |
|---|---|
| `ctx.date` | `'YYYY-MM-DD'` of the current bar |
| `ctx.date_ts` | `pandas.Timestamp` |
| `ctx.day` | 0-based index since `start` |
| `ctx.i` | global row index in the loaded panel |
| `ctx.start_date`, `ctx.end_date` | backtest window |
| `ctx.is_rebalance_day` | bool for today |
| `ctx.regime` | frozen market-regime label actionable today (dict from `regime_labels`), else `None` |
| `ctx.cash`, `ctx.nav` | current cash and NAV (close marks) |
| `ctx.positions` | `{code: Position}`; `Position` has `code, kind, shares, sellable, cost_total, avg_cost, last_price, open_date, hold_days, value(), to_dict(nav)` |
| `ctx.position(code)` | `Position | None` |
| `ctx.weights` | `{code: value/nav}` |
| `ctx.dates(window=None)` | list of trading dates known so far |
| `ctx.history(codes=None, field="close_qfq", window=60)` | `DataFrame` (window × codes), rows ≤ today |
| `ctx.history_matrix(field="close_qfq", window=60, codes=None)` | `(window × n) ndarray` (fast path) |
| `ctx.latest(field="close_qfq", codes=None)` | `Series` of today's values |
| `ctx.stock_codes()` | all panel codes (`ndarray`) |
| `ctx.universe(tradable=True, exclude_st=True, min_history=0, as_of=None)` | list of codes listed & tradable & non-ST |
| `ctx.is_tradable(code)`, `ctx.is_st(code)`, `ctx.is_delisted(code)` | booleans |
| `ctx.bar(code, date=None)` | dict of latest raw/qfq fields or `None` |
| `ctx.top_by(field="amount", top_n=50, lookback=20)` | liquidity-style top-N codes (no lookahead) |
| `ctx.index(index_code="sh000300", window=None, field="close")` | `Series` up to today |
| `ctx.index_history(window=250, index_code="sh000300", field="close")` | same |
| `ctx.index_latest(index_code="sh000300", field="close")` | float |
| `ctx.index_returns(window=250, index_code="sh000300")` | daily `Series` |
| `ctx.state` | free-form dict for strategy state |
| `ctx.log(msg)`, `ctx.add_event(kind, code=None, **detail)` | diagnostics into the result |

Panel fields available in `history` / `history_matrix` / `latest` / `bar`:
`open, high, low, close, preclose, volume, amount, turn, pctchg, isst, tradestatus`
(raw) and `qfq_open, qfq_close, qfq_high, qfq_low` (forward-adjusted).  Both spellings
are accepted for the adjusted series (`qfq_close` == `close_qfq`, etc.).  `NaN` = no bar
(suspended / not listed). Use **raw** fields for limit/lot decisions and **qfq** fields
for signal/return computations (engine does this internally).

Indices published with the contract: `sh000001, sh000300, sz399006, sh000905, sh000852,
sh000985, sz399001, sz399005` (whichever data-runner publishes).

### Data-runner contract limitations (disclosed, not engine bugs)

The published dataset is `manifest v1_final` (`data/meta/manifest.json`, generated
2026-10-20 14:04:34, 53 files with per-file sha256; panel 2012-01-04..2026-10-19,
12,438,891 rows, 4,867 codes).  `result["data_version"]` embeds the **manifest sha256**
(via `manifest_sha256` / `manifest_version` / `manifest_generated_at` / `manifest_files`)
so every backtest can be traced to this exact data snapshot even if the files are later
republished.  See also `data/qa/DATA_QA.md`.  Known properties, warned about at load time:

* `pctchg` is a **decimal fraction** (`0.0152` = +1.52%), not a percentage (baostock
  reports `1.52`); multiply by 100 for display, or use `qfq_close` returns instead.
* `isst` is always 0 → ST names are not identifiable from prices, so the "ST not
  buyable" hook cannot trigger.  Disclose in reports; do not claim ST filtering.
* `turn` is empty → the regime turnover component is inert (score contribution 0); use
  the `amount` field for liquidity ranking (`ctx.top_by("amount", ...)`).
* `amount` is **real CNY turnover value** from Sina (not a proxy); `volume` is shares.
* `tradestatus` = 1 on every published row (only traded days are published) → suspension
  is represented by *missing rows*; the engine handles missing bars as suspension carry.
  Note: 36 rows have `volume = 0` / `amount = 0` but `tradestatus = 1` (true
  suspension-style rows); the frozen engine treats them as tradable (impact
  36 stock-days out of 12.4M — documented, not silently patched).
* `preclose` is **mixed**: the exchange ex-rights reference price on ex-dates, previous
  available close carried otherwise (first row per code empty) → the engine's precise
  corporate-action path (`m = prev_close / preclose`) is authoritative; the qfq ratio is a
  cross-check and fallback.
* qfq files contain only 5 price columns (`code,date,open,close,high,low`); volume/amount
  must come from the raw files.
* IPO-day special bands are not modelled; IPO-day trading is blocked by default
  (`allow_ipo_day_trade=False`).  The broader "new listings have no price limit for their
  first few trading days" window is also not wired into limit checks yet (a parameter
  `ipo_no_limit_bars=5` is reserved for that); effect is conservative (some orders may be
  blocked/carried in a name's first week).
* 74 of the 4,941 universe codes (delisted) have no captured bars → the panel axis has
  4,867 codes; 3 codes in the loaded window have no bars at all.
* The calendar's last open day is 2026-10-20 while index/bars end 2026-10-19 (run to
  2026-10-19).
* Data QA flags ~825 rows as qfq-factor/baostock-event inconsistency candidates near
  ex-dates; the engine's qfq-ratio branch applies whatever total-return adjustment the
  qfq series encodes, so held names stay consistent with qfq (the residual one-day gap
  cases are listed in `engine/out/REAL_DATA_INTEGRATION.md`).

---

## 3. Frozen execution / cost rules

Order of events inside each trading day:

1. corporate actions (qfq-implied, see below)
2. delisting forced liquidations
3. T+1 unlock
4. execute pending orders at the day's open (sells first, then buys, deterministic order)
5. mark positions to close, compute NAV, check `NAV = cash + Σ marks` **and** a full
   daily P&L decomposition (raises on violation)
6. call `on_bar` / `on_rebalance`
7. collect the new target book / orders for the next execution slot

* **Capital / leverage**: 100,000 CNY default, long only, cash never negative, no interest.
* **Lots / T+1**: buys are whole 100-share lots; shares bought on day D are sellable from
  D+1 (orders are structurally next-open, so a T+1 breach can only come from a bug; the
  kernel rejects it explicitly and records a `t1_locked` block).
* **Limits** (from raw `preclose`, 2-decimal rounding): main board ±10%, ST ±5%;
  ChiNext ±10% before 2020-08-24 and ±20% from 2020-08-24; after the reform ChiNext ST
  names also move ±20%; pre-reform ChiNext ST ≈ ±5%. Buys are blocked when the execution
  price is at limit-up, sells when at limit-down; blocked legs are carried.
  IPO first-day special bands are ignored (`allow_ipo_day_trade=False` by default and
  the day is disclosed in an event) — documented limitation.
* **Suspension**: missing bar or `tradestatus == 0` → order carried (never guessed);
  positions keep the last known close (suspension carry).
* **Delisting**: once `universe.out_date` is reached for a held name, the engine
  force-liquidates at the day's open if it trades, else at the last known close, charges
  normal sell fees, records a `delisting_forced_liquidation` trade + event.
* **ST**: not buyable by default (`allow_st_buy=True` overrides); selling is allowed.
* **Fees** (`engine.core.FeeModel`): commission 2.5bp, minimum 5 CNY, both sides;
  stamp tax on sells 0.1% before 2023-08-28 and 0.05% from 2023-08-28; transfer fee
  0.001% both sides (documented simplification: applied symmetrically to SH and SZ per
  the 2022 rule); optional per-side slippage 10bp (default; `slippage_bps=0` disables).
* **Corporate actions — primary valuation is qfq total-return based**
  (`dividend_mode="qfq_reinvest"`): dividends are **not** credited as cash; the holding is
  multiplied on the ex-date by the total-return factor, i.e. dividends are implicitly
  reinvested at the ex-date price (documented approximation; direction: tiny amount of
  extra compounding vs a cash-dividend account).
  Two detection paths, in priority order:
  1. provider `preclose` is the ex-rights reference price on ex-dates (the frozen v1
     dataset does this): `m = raw_close[T-1] / preclose[T]`, cross-checked against the
     qfq-implied factor (mismatch > 2% raises a `corporate_action_warning` event);
  2. otherwise the qfq panel is used: `m = (qfq_close[T]/qfq_close[T-1]) /
     (raw_close[T]/raw_close[T-1])` with a rounding-noise threshold.
  Actions smaller than ~0.2-0.3% of price are ignored (rounding noise); multiples outside
  `[0.2, 3.0]` are not modelled and are logged.  Adjustment rows appear in `trades` with
  `side="adjust"` and zero cost so FIFO reconciliation stays exact.
  *Data-defect fallback* (`infer_missing_actions=True`, the default used for all reported
  results): if a held stock shows a raw drop beyond any legal board limit that neither the
  reference `preclose` nor the qfq factor captured (a permanently missing 送股/转增 factor),
  the engine applies a value-preserving multiplier `m = close[T-1]/close[T]` (drop-only,
  `1.1 <= m <= 3.0`, requires the new level to persist, skips a new listing's first week)
  and logs a `corporate_action` event with `source="inferred_missing_action"`.  On the
  2012-2026 panel this affects only ~20 rare stock-days; pass `infer_missing_actions=False`
  to run the strict frozen rule set without the fallback (documented switch for future
  work — reported results used the default).
* **Index proxy positions** (only for built-in benchmark/smoke strategies such as
  `BuyHold("sh000300")`): fractional units marked at the index close, no stamp tax, no
  dividends (price index) — never used as a cash substitute in research strategies.

---

## 4. Result dict

```python
{
 "name","start","end","capital",
 "nav": DataFrame[date, nav, cash, market_value, exposure, ret, n_positions,
                  fees, dividends, traded_amount, turnover, regime,
                  bench_<index>...],
 "trades": DataFrame[date, code, kind, side(buy/sell/adjust), shares, units, price,
                     amount, commission, stamp, transfer, fee_total, slippage_cost,
                     reason, target_weight, note],
 "orders": DataFrame[created_date, filled_date, code, side, kind, requested, filled,
                     status, reason, note]           # explicit orders, incl. blocked/carried
 "events": DataFrame[date, type, code, note, ...]    # corporate actions, delisting,
                     # target_blocked, target_book_expired, unknown codes, warnings
 "positions": DataFrame[date, code, kind, shares, units, price, value, weight, cost_total],
 "holdings_final": {code: {shares, value, weight, avg_cost, unrealized_pnl, board, isst,...}},
 "metrics": {...}, "monthly": DataFrame[year, month, ret, nav_end],
 "monthly_matrix": DataFrame (year × Jan..Dec),
 "exposure": {...}, "tail": {...}, "fifo": {...},
 "benchmarks": {code: NAV Series, "cash": Series},
 "benchmark_compare": DataFrame[...], "regime_segments": DataFrame[...],
 "summary": {...}, "settings": {...}, "data_version": {...}, "meta": {...},
 "warnings": [str],
}
```

### Metrics block (engine/metrics.py)

`total_return, cagr (252-day basis), ann_vol, sharpe (rf=0), sortino, calmar,
max_drawdown, maxdd_peak_date, maxdd_trough_date, maxdd_recovery_date,
maxdd_duration_days, longest_drawdown_days, underwater_days, trading_days, calendar_days,
avg_exposure, median_exposure, end_exposure, max_exposure, all_cash_days, days_in_market,
avg_positions, turnover_total, turnover_annual, trade_count, buy_count, sell_count,
trade_days, total_traded_amount, avg_trade_amount, total_fees, fee_pct_of_capital,
implicit_dividends, total_slippage_cost, fifo_realized_pnl, fifo_win_count,
fifo_loss_count, fifo_win_rate, fifo_avg_win, fifo_avg_loss, fifo_profit_factor,
fifo_open_units, fifo_open_cost, fifo_unrealized_pnl, fifo_adjust_events,
monthly_win_rate, best_month, worst_month, n_months,
pnl_realized_plus_unrealized, pnl_actual, pnl_reconciliation_gap`  ← the last gap must be
~0 (FIFO reconciliation of total P&L).

`tail`: worst/best day+date, worst/best week, worst/best month, VaR/CVaR 95 daily, skew,
kurtosis, pct_positive_days. `exposure`: mean/median/end/max, all-cash days, avg positions.

`benchmark_compare` rows: `benchmark, benchmark_total_return, benchmark_cagr,
benchmark_ann_vol, benchmark_max_drawdown, strategy_total_return, excess_total_return,
beta, annualized_alpha, corr` (cash included).

`regime_segments` (requires `regime_labels`): per regime `days, start, end,
segment_return, avg_daily_ret, ann_vol, sharpe, max_drawdown, avg_exposure,
pct_positive_days, trade_count, traded_amount`.

`print_summary(result)` prints the headline numbers (used by `run_api_smoke.py`).

### JSON export

```python
save_result_json(result, "out/run.json")                       # summary + metrics + monthly
save_result_json(result, "out/run.json", include_nav=True, include_trades=True)
save_results_json({"bull": r1, "bear": r2}, "out/runs.json")
```

---

## 5. Built-in strategies (benchmarks / smoke tests)

```python
BuyHold("sh000300")            # index proxy buy&hold, or a stock code e.g. BuyHold("600000")
EqualWeight(codes=None, top_n=50, lookback=20, rebalance="M")
MomentumTopN(lookback=120, top_n=20, rebalance="M", skip=0, liquidity_top=800)
Cash()                         # 100% cash
```

---

## 6. Data layer, cache and performance

`engine.data.DataBundle.load(data_dir, start, end, warmup_days=320, require_panel=True,
cache_dir=...)` pivots the long-format contract files (`bars_{year}.csv.gz[.csv]`,
`bars_qfq_{year}.csv.gz[.csv]`) into memory-mapped `(T × N)` float32 matrices, one per
field.  The cache lives under `tmp/engine-dev/cache/panel_<signature>/` and is rebuilt
automatically when the published files change (signature = data dir + field set + years +
date axis + panel file list with sizes/mtimes).

**Published cache (manifest v1_final, 2026-10-20):**
`qrun_1416/tmp/engine-dev/cache/panel_93d955cb9ca97d12/` — signature `93d955cb9ca97d12`,
960.5 MB, **3,448 trading days (2012-08-03 .. 2026-10-19) × 4,867 codes**, warm mmap load
0.2-0.3 s.

**Warmup sharing (important for shared caches):** the cache row axis is fixed, but a cache
whose date axis is a *superset* of the requested window is reused by row-slicing.  So a
cache built with `warmup_days=340` also serves runs with the default `warmup_days=320`
(and any smaller warmup) without rebuilding:
`run_backtest(strat, start="2014-01-01", end="2026-10-19", warmup_days=340)` is the
canonical call that hits the published cache; omit `warmup_days` (320) and it still hits it
via the superset path.  A bundle with `start="2012-08-01", warmup_days=0` requests a
different row axis (first date 2012-08-01) and would build a second cache.
`result["data_version"]` records `manifest_sha256` (sha256 of `data/meta/manifest.json`)
plus `manifest_version` / `manifest_generated_at` / `manifest_files`, so every result is
traceable to the exact published data snapshot even if files are later republished.

Precompute (one-time; already done for the shared cache):

```
python engine/precompute.py --data-dir data --start 2014-01-01 --end 2026-10-19 --warmup-days 340
```

A 13-year daily run over the full universe (≈3,100 dates × ≈4,900 names) finishes well
inside 60 s once the cache exists (measured: MomentumTopN full-universe 13-year run
**11.2 s**, peak RSS 2.21 GB).  Strategy-side work should use `ctx.history_matrix` for
vectorised cross-sections; avoid per-day allocation of DataFrames.  Memory stays under
~2.5 GB for the whole 2012-2026 panel (float32 memmaps).

Fixture data for engine tests lives in `engine/tests/fixtures/` and can also be used for
API experiments (`.csv` accepted as well as `.csv.gz`).

---

## 7. Regime labels (no-lookahead state machine)

```
python -m engine.regime --data-dir data --start 2014-01-01 --end 2026-10-19 \
    --out engine/out/regime_labels.csv
```

```python
from engine.regime import build_regime_labels, load_regime_labels
labels = build_regime_labels(data_dir="data", start="2014-01-01", end="2026-10-19")
# DataFrame[date, regime (bull|bear|neutral), score, components...]
result = run_backtest(strat, ..., regime_labels="engine/out/regime_labels.csv")
```

Each label for date T is computed **only from data up to T-1's close** and is therefore
actionable at T's open; `ctx.regime` returns that label. The frozen rule set lives in
`engine/RULES.md` — it was written before any performance was inspected and must not be
tuned against backtest results.

---

## 8. Tests

```
python -m unittest discover -s engine/tests -v
```

Plain `unittest` (no pytest). The suite covers NAV identity, 100-share lots, T+1, limit
blocks, suspension carry, fee maths across the stamp-tax switch, dividend/split total
return, no-lookahead perturbation, full-cash runs, FIFO, delisting, metrics on crafted
series and the public API.
