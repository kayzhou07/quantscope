# NO_LOOKAHEAD — audit of the local round-3 S0/S1 backtest

Scope: the local port `winrate_backtest_20261020/round3/s0s1.py` and the frozen engine
`qrun_1416/engine/core.py` it runs on. Question: can any decision taken at the close of day T
depend on data published after T's close?

Verdict summary

| Check | Verdict | Evidence |
|---|---|---|
| N1 signal/execution timing (T close signal → T+1 open fill) | PASS (structural) | `core.py:818-856` |
| N2 every ctx accessor truncated at `i` (today) | PASS (structural) | `core.py:679-817` |
| N3 strategy code only touches truncated accessors; index arithmetic ends at today | PASS (code audit) | `s0s1.py` (see §3) |
| N4 regime label for T is causal (trailing windows only) | PASS (structural) | `os/regime_v1.py:116-177` |
| N5 regime label sequence equals the platform's frozen `RUN_TEXT` | PASS — 0/3448 mismatches | `FIDELITY_CHECKLIST.md` §0 |
| N6 corporate-action factors use only information available at T's open | PASS (structural) | `core.py:900-1100`, `API.md` §3 |
| N7 hard daily identity check `NAV = cash + marks` and P&L decomposition | PASS (structural) | `core.py:1197-1210` |
| N8 empirical perturbation: truncated-window run reproduces the full-window run through the cutoff | see §5 | to be filled |

---

## 1. Signal / execution timing (N1)

`Backtest.run()` (`core.py:818-856`) executes this fixed order for every trading day `i`:

```
824  for i in range(start_i, end_i + 1):
827      _begin_day(i)
831      _process_corporate_actions(i)
832      _process_delistings(i)
833      _release_t1(i)
834-835  _execute_pending(i, "open")      # orders queued at i-1 fill at i's OPEN
836      _mark_to_market(i)              # close marks
841      nav = _nav()
846      _record_nav(i)
848-851  on_bar(ctx) / on_rebalance(ctx)  # strategy sees day i close
852      _collect_orders(i, signal_nav=nav, signal_date=date)  # queued for i+1
```

Consequences:

* An order produced at the close of T can only be executed by the `_execute_pending(i+1, "open")`
  call of the *next* iteration. There is no code path that fills an order on the same day it is
  created (`ctx.set_targets`, `ctx.buy`, `ctx.sell` only append to `_orders` /
  `_pending_targets`, collected in `_collect_orders`, `core.py:852`).
* `signal_nav` is pinned to day T's close NAV (`core.py:852`) so the target book cannot react to
  T+1 prices when sizing.
* The only pre-open work on day i is corporate actions / delisting / T+1 unlock. All of them are
  keyed on data published for day i's open (reference `preclose[i]`, `out_date`), not on the
  close of i or later.

## 2. Context accessors are truncated at `i` (N2)

| accessor | implementation | truncation |
|---|---|---|
| `ctx.history_matrix(field, window)` | `lo = max(0, i-window+1); mat = fields[field][lo:i+1]` (`core.py:679-687`) | rows ≤ today, never `i+1` |
| `ctx.latest(field)` | `fields[field][i]` (`core.py:701-706`) | today |
| `ctx.history(...)` | wraps `history_matrix` (`core.py:358-366`) | rows ≤ today |
| `ctx.dates(window)` | `bundle.dates[:i+1]` (`core.py:351-356`) | today last |
| `ctx.bar(code)` | `fields[name][i, j]` (`core.py:780-793`) | today |
| `ctx.universe(...)` | `listed_mask[i]`, `tradestatus[i]`, `isst[i]`, `qfq[i-min+1:i+1]` (`core.py:742-758`) | today |
| `ctx.top_by(...)` | `fields[lo:i+1].nanmean` with `lo=max(0,i-lookback+1)` (`core.py:760-778`) | today |
| `ctx.index_history/index_latest` | `arr[lo : i+1]`, `arr[i]` (`core.py:795-815`) | today |
| `ctx.nav/cash/positions/weights` | engine state after today's close marks (`core.py:326-348`) | today |
| `ctx.regime` | `regime_labels[date]` (`core.py:321-324`) | today's label only |
| `ctx.i` | global row index; `ctx.stock_codes()` returns only the code axis, no values | — |
| `ctx.regime`/`ctx.is_delisted` | `is_delisted` is `i >= out_is` (true only from the delisting day onward, `core.py:734-740`) | not a future peek |

Nothing in `Context` exposes a panel array, the bundle, or a date beyond `ctx.date`. A strategy
could only look ahead by writing its own data loader (it cannot: `s0s1.py` has no I/O imports
other than pandas/numpy/sys/pathlib and does not read the panel or `regime_v1.csv` — the runner
passes pre-computed labels in).

## 3. Strategy code audit (`s0s1.py`) (N3)

Imports: `math, sys, pathlib, numpy, pandas, engine.core.Strategy` — no `engine.data`, no file
reads. All data comes from `ctx`.

Data-touching lines and their latest row:

* `ctx.history_matrix("qfq_close", 260)` → `m`, 260 rows ending today (`s0s1.py:112`). All
  subsequent indexing is on this matrix: `m[-1]` (today), `df.iloc[-60:]`, `iloc[-120:]`,
  `iloc[-61:]`, `iloc[-22]`, `iloc[-253]`, `iloc[-127]` — the most negative offset is −253,
  i.e. 252 sessions before today, inside the window. Worst case if fewer than 253 rows were
  loaded is an `IndexError`, not a silent future read.
* `ctx.history_matrix("amount", 60)` → last 60 rows (`s0s1.py:132`).
* `ctx.latest("close")` → today's raw close (`s0s1.py:131`).
* `ctx.universe(tradable=True, exclude_st=True)` → today's tradable, non-ST set
  (`s0s1.py:106`).
* `ctx.bar(c)` for the daily audit (`s0s1.py:337`) → today.
* `ctx.positions`, `ctx.nav`, `ctx.cash`, `ctx.date`, `ctx.regime` → today's state.
* `ctx.set_targets`, `ctx.sell`, `ctx.add_event` → queued for T+1.

`df.pct_change()` (`s0s1.py:126`) is applied to a column-wise `ffill`ed copy of the 260-row
matrix; first row becomes NaN (the platform's `pct_change(fill_method=None)` behaviour is
identical on an already-ffilled series).

`pandas.rank(pct=True)` (`s0s1.py:153-156`) is computed inside the snapshot at day T over the
day-T feature population only — there is no full-sample precomputation anywhere in the file.

The only pre-backtest inputs are the constant board-prefix tuples (`s0s1.py:79-80`) and the
exposure map (`s0s1.py:169`); the regime label dict is supplied by the runner from
`os/out/regime_v1.csv` and is keyed by date (see N4).

## 4. Regime input causality (N4/N5)

`os/regime_v1.py:102-189`:

* all indicator blocks are trailing: `rolling(w, min_periods=...)`, `shift(k)`, `pct_change()`
  (`regime_v1.py:116-135`);
* the state machine (`regime_v1.py:146-177`) is a forward loop over t: at t it reads only
  `flags.iloc[t]` and its own carry state (`cur`, `pend`, `pend_n`). The 5-day confirmation
  counter only delays switches, it never pulls a future flag forward;
* the label written for date T is the state after processing T, i.e. computed from data ≤ T's
  close, which is exactly what a T-close decision may use if the order fills at T+1's open.

`os/regime_v1.py` docstring states the label is "the state evaluated on T's close"; the
platform's `state_at()` picks the label with the last transition date ≤ T from its own frozen
`RUN_TEXT`. Independent comparison over all 3,448 dates: **0 mismatches** (mapping
BULL→B, NEUTRAL→N, BEAR→E, CRASH→C), see `FIDELITY_CHECKLIST.md` §0. So the state used by the
local run is on exactly the same days the platform used. (Note: the frozen `RUN_TEXT` is an
*input* to the platform, so both sides share whatever causality the state machine has; N4
argues that causality is sound.)

## 5. Perturbation test (N8) — RUN, PASS

Script: erify/lookahead_perturb.py; log: erify/lookahead_perturb.log.
Engine config identical to the official runner (same window 2014-07-01..2023-12-29, capital 100k,

ebalance=M, slippage_bps=10, commission 2.5 bp/min 5, dividend_mode=qfq_reinvest,
exec_timing=next_open, same regime labels).

Three runs on the same data bundle:

* **A** — reference: deliverable window 2014-07-01..2023-12-29.
* **B** — truncation: identical strategy, run stops at **T\* = 2019-12-31** (same bundle, end_i
  truncated, no second engine cache).
* **C** — future-data perturbation: full window, but every panel row after T\* is multiplied by a
  consistent factor (open/high/low/close/preclose and the four qfq price fields ×1.7, mount ×3,
  olume ×2). Factors are applied to price *levels* consistently, so the engine sees no fake
  corporate action; this is the same style of perturbation as engine/tests/test_no_lookahead.py
  (which multiplies future fixture rows by 1.7). The copied arrays live in RAM; the qrun_1416 cache
  files are never written.

Calibration / harness trust check: run A reproduces the official out/S0_nav.csv to
max |diff| = 5.8e-11 CNY on nav (cash 1.5e-11, market value 1.5e-11, exposure 8.3e-17) and
out/S0_trades.csv row-for-row (688 trades, every column equal within 1e-9 / string-equal after
normalising pandas' empty-string-vs-NaN CSV round-trip). So the perturbation harness is the same
engine configuration the deliverable used, not a lookalike.

Result — **28/28 checks PASS**:

| comparison | nav/cash/mv/exposure | daily position ledger | trade ledger | order ledger | rebalance decisions |
|---|---|---|---|---|---|
| A vs B (truncated run) | identical, max abs diff **0.0** (atol 1e-9) | 5,931 rows equal | 463 rows equal | 195 rows equal | 41/41 equal |
| A vs C (future rows perturbed) | identical, max abs diff **0.0** (atol 1e-9) | 5,931 rows equal | 463 rows equal | 195 rows equal | 41/41 equal |

Sanity: after T\* the two NAV paths do diverge (1644 days all-different), so the perturbation is
effective and run C really did see modified future data.

### 5.1 What this proves and what it does not

* Proves: over the first 1,345 sessions, **nothing computed so far depends on rows after 2019-12-31** —
  not the signals, not the fills, not the position marks, not the cash ledger. If the strategy or the
  engine had read a future row or computed a full-sample statistic, A and B/C would differ before T\*.
* Proves (calibration): run A == the official run, so the result applies to the delivered numbers.
* Does not prove: anything about a hypothetical future data source the strategy might read directly
  (it does not — §3 shows the module has no I/O), nor about corporate-action factors for rows before
  T\* that the data vendor might itself have revised ex post (a data-vintage property, outside the
  engine's control; disclosed as manifest_sha256 for traceability).

Combined with §2 (every ctx accessor slices [:i+1]) and §3 (the port touches only ctx), the
no-lookahead guarantee is established structurally **and** empirically.
