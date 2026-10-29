# Market-regime rule set (FROZEN — v1, written 2026-10-20 before any backtest result was inspected)

Owner: engine-dev. Implemented by `engine/regime.py`. **These thresholds are a priori and
must not be tuned against strategy performance.** Changing them invalidates comparisons and
requires a new version file + Lead sign-off.

## Purpose

Provide a daily market-state label (`bull` / `bear` / `neutral`) that a strategy may use to
switch between a bull-market operating system and a bear-market operating system. The label
attached to date **T** is computed exclusively from data available at the close of **T-1**
and is therefore tradable at T's open. No future information is used anywhere.

## Inputs (all from the published data contract, data <= T-1)

Index: `sh000300` by default (fallback `sh000001` if the former is not published), daily close.

| component | definition |
|---|---|
| trend | +1 if close > MA120 **and** MA20 > MA60; -1 if close < MA120 **and** MA20 < MA60; else 0 |
| slope | 20-day change of MA60: +1 if > +2.0%; -1 if < -2.0%; else 0 |
| dd250 | drawdown from the trailing 250-day high: +1 if > -10%; -1 if < -25%; else 0 |
| vol_regime | percentile rank (0-100) of 20-day realised vol within its trailing 250-day window: +1 if < 40; -1 if > 85; else 0 |
| breadth | % of listed, non-suspended stocks whose qfq close is above their MA60: +1 if > 60%; -1 if < 40%; else 0 |
| nhnl | (new 250-day high count + 1) / (new 250-day low count + 1): +1 if > 1.5; -1 if < 0.667; else 0 |
| turnover | z-score of market turnover (cross-sectional mean of `turn`) vs trailing 250 days: +1 if z > 1.5 **and** index 20-day return > 0; -1 if z < -1.5 **and** index 20-day return < 0; else 0 |

Score = sum of the available components. Full rule score range is [-7, +7].

## Classification (frozen)

Full rule (breadth + turnover components available):
* `bull`   if score >= +3
* `bear`   if score <= -3
* `neutral` otherwise
* crash override: index 20-day return <= -12% → `bear` regardless of score (a priori crash filter).

Index-only variant (used only when the stock panel is not yet published; flagged in the CSV
with `variant = "index_only"`): components trend, slope, dd250, vol_regime (score range
[-4, +4]):
* `bull` if score >= +2, `bear` if score <= -2, else `neutral`; same crash override.

First 250 trading days of loaded history have insufficient lookback → label `unknown`
(excluded from regime performance stats; a strategy sees `ctx.regime is None`).

## Output (`engine/out/regime_labels.csv`)

`date, regime, variant, score, trend, slope, dd250, vol_regime, breadth, nhnl, turnover`
plus the raw indicator columns (MA levels/slopes, drawdown, ret20/60, realised vol and its
percentile, breadth percentages, new-high/low counts, turnover z, equal-weight index level).

## Implementation notes / limitations (disclosed, not tuned)

* Prices are forward-adjusted (qfq) for all return computations; raw prices are not used.
* Breadth treats a stock as "above MA" only when both the price and the MA are available
  (minimum 60/250 valid observations); suspended stocks with no bar are excluded.
* New-high/low counts use 250-day extremes of the qfq close among stocks with 250 valid
  observations; the smoothing (+1)/(+1) avoids division by zero on quiet days.
* Turnover is the equal-weighted mean of `turn` over tradable names, so it proxies market
  participation rather than free-float turnover exactly.
* The label is a *state*, not a forecast; it can whipsaw at neutral boundaries. Smoothing
  was deliberately not added (a priori simplicity; no result-driven tuning).
* Regime labels are for analysis / OS switching only. The engine never trades on them
  automatically.
