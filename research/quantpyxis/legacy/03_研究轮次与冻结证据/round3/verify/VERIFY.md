# VERIFY — independent audit of the round-3 local S0/S1 backtest

Auditor: `verifier` (shared task `task-2`). Scope: recompute the headline numbers from raw engine
output only, check the port against the platform sources line by line, and test for look-ahead.
Everything below was produced by the scripts in this directory:

| file | what it does | log |
|---|---|---|
| `verify_metrics.py` | recomputes every metric from `out/DAILY_AUDIT*.csv`, `out/POSITIONS*.csv`, `out/TRADES*.csv`, `out/ORDERS*.csv` (+ supplementary `out/{tag}_nav.csv`), compares with `out/metrics.json` / `out/summary.csv` / `out/yearly.csv` / `out/cost_stress.csv` | `verify_metrics.log` |
| `lookahead_perturb.py` | truncation + future-data perturbation against the engine, plus a calibration run that must reproduce `out/S0_nav.csv` | `lookahead_perturb.log` |
| `risk_flat_check.py` | explains every `RISK_FLAT_AUDIT_*.csv` row from POSITIONS + the raw panel files | `risk_flat_check.log` |
| `FIDELITY_CHECKLIST.md` | platform-rule vs port-rule table with verdicts | — |
| `NO_LOOKAHEAD.md` | structural + empirical no-lookahead argument | — |

Audited snapshot (verified by hash, not by trust): `sha256(s0s1.py)=3cc3b0c8…`, `sha256(run_round3.py)=139a78fa…`,
`sha256(engine/core.py)=b7fc9882…`, `data/manifest.json` sha `9294d2b8…` — all equal to the hashes recorded in
`out/run.log`; the outputs in `out/` are the ones the audited code produced. Sources and `qrun_1416/` were
never modified by this audit (all read-only; the perturbation copies panel rows into RAM).

---

## Verdict table

| # | Check | Verdict | One-line evidence |
|---|---|---|---|
| V1 | Metric block independently recomputed from raw CSVs vs `metrics.json` + `summary.csv` | **PASS** | 144/144 numeric checks pass (NAV atol 1e-4 CNY, ratios atol 1e-6, counts exact) |
| V1b | Internal consistency of the raw outputs (nav = cash+marks, positions ledger, cash/trade ledger, fees, orders) | **PASS** | I1–I7 pass for S0 and S1; largest deviation `nav-(cash+mv)` = 5.8e-11 CNY; cash from trade ledger within 0.027 CNY |
| V1c | `yearly.csv` and `cost_stress.csv` internally consistent with the recomputed series | **PASS** | all 20 year-rows within 1e-6; both cost-stress deltas exact |
| V2 | Signal-rule fidelity vs platform S0/S1 (§1–§5 of `FIDELITY_CHECKLIST.md`) | **PASS** | every rule MATCH; **zero MISMATCH**; empirical `REBALANCES.csv`/`REBALANCE_SELECTIONS.csv` checks agree |
| V3 | No look-ahead (structural + perturbation) | **PASS** | 28/28 equality checks: NAV/cash/positions/trades/orders/rebalance decisions identical through 2019-12-31 under truncation **and** under ×1.7 perturbation of all future price rows; calibration run == official `S0_nav.csv` to 5.8e-11 |
| V4 | Explanation of the 6 S0 (4 S1) risk-flat days | **PASS** | 4 = 600277 suspension (no bar 2019-05-07..05-20, confirmed in the raw panel); 1 = 300015 raw close unchanged; 1 = two holdings' close moves exactly offset (−990/+990 CNY) |
| V5 | Reproduce platform R2 **84.00 % / 11.16 %** | **FAIL** | local S0 76.21 % / −12.65 % (−7.79 pp / −1.49 pp); with the platform's own 20 bp slippage S0_20bp 73.43 % / −11.76 % (−10.57 pp / −0.60 pp). Cause not decomposable locally |
| V6 | Order-of-magnitude sanity vs platform reference statistics (Q0/R0, all-cash days) | **PARTIAL / UNVERIFIED** | non-zero-day winrate 55.10 % vs platform 54.82 % (+0.28 pp) plausibly in line; risk-day 54.83 % vs 46.87 % and all-day 29.42 % vs 23.59 % differ because the comparators are different strategies (monthly R2 vs quarterly R0); all-cash days 1083 vs 1150 also not like-for-like |
| V7 | Deliverable completeness of the handoff (§1–§8) | **PARTIAL (mostly satisfied)** | all files present; §3 covered in aggregate by `extra_diagnostics.json` (per-name tables still missing); §7 lacks S0 with doubled commission+slippage; §1 reproduction FAIL as V5 |
| V8 | Single-variable integrity of S1 (nothing else changed) | **PASS** | identical snapshot per rebalance date (candidate counts identical S0/S1/S1b), identical window/params; only `pick_affordable_ranked` differs |

**No material discrepancy was found in the reported numbers; the material finding is that the run does
not reproduce the platform's R2 headline (V5).**

---

## V1 — independent metrics (recomputed, then compared)

Definitions used (platform `S0:124-162`): `pnl_t = nav_t − nav_{t−1}` with `nav_0 := 100000`;
`positive ⇔ pnl > +1e-6`; `flat` otherwise; `risk_day ⇔ positions>0 today or at prior close`
(exit day counts); `pure_cash_flat = ¬risk_day ∧ flat`; max drawdown `min(nav/cummax(nav)−1)` with
peak = argmax before the trough. Tolerances: NAV/cash/fees 1e-4 CNY absolute, return/drawdown/ratios
1e-6 absolute, day and trade counts exact.

| metric | S0 (independent) | S0 (`metrics.json`) | S1 (independent) | S1 (`metrics.json`) |
|---|---|---|---|---|
| trading days | 2,315 | 2,315 | 2,315 | 2,315 |
| final NAV (CNY) | 176,210.5123 | 176,210.5123 | 171,793.5131 | 171,793.5131 |
| total return | 0.7621051227 | 0.7621051227 | 0.7179351309 | 0.7179351309 |
| max drawdown | −0.1265452086 | −0.1265452086 | −0.1330651611 | −0.1330651611 |
| dd peak → trough | 2019-11-19 → 2020-03-19 | same | 2017-11-22 → 2020-03-23 | same |
| positive / negative / flat days | 681 / 555 / 1,079 | same | 676 / 577 / 1,062 | same |
| risk days / risk-positive | 1,242 / 681 | same | 1,257 / 676 | same |
| all-day winrate | 0.2941684665 | same | 0.2920086393 | same |
| risk-day winrate | 0.5483091787 | same | 0.5377883850 | same |
| non-zero-day winrate | 0.5509708738 | same | 0.5395051875 | same |
| pure-cash-flat days | 1,073 | same | 1,058 | same |
| mean win day / loss day (CNY) | 635.5180 / −642.4815 | same | 557.2503 / −528.4362 | same |
| daily profit factor | 1.213727925 | same | 1.235459827 | same |
| avg exposure | 0.1655629236 | same | 0.1591644065 | same |
| all-cash days | 1,083 | same | 1,067 | same |
| avg / max positions | 3.5317 / 13 | same | 5.3797 / 13 | same |
| trades | 688 | 688 | 1,002 | 1,002 |
| total fees (CNY) | 5,659.74 | 5,659.74 | 6,889.60 | 6,889.60 |
| risk-flat days | 6 | 6 | 4 | 4 |
| CAGR | 0.0636086602 | same | 0.0606735268 | same |

Same table reproduces exactly from `out/summary.csv` (10 further keys per variant, all PASS).
Supplementary variants, independently recomputed from `{tag}_nav.csv` + `{tag}_trades.csv`
(agree with `metrics.json` to <1e-9):

| variant | total return | max DD | trades | fees |
|---|---|---|---|---|
| S0_20bp (2× platform slippage) | 0.7343333927 | −0.1175827338 | 681 | 5,566.80 |
| S1b (backfill with S0 affordability) | 0.774949 | −0.135793 | 988 | 6,822.69 |
| S1_stress (2× commission + 2× slippage) | 0.689437 | −0.132548 | 999 | 6,867.13 |

Yearly returns (independent; `yearly.csv` matches to <1e-6):

| year | S0 | S1 |
|---|---|---|
| 2014 (from 07-01) | +6.0920 % | +7.7252 % |
| 2015 | +39.5356 % | +38.2378 % |
| 2016 | −0.8595 % | −1.4713 % |
| 2017 | +8.3793 % | +10.4493 % |
| 2018 | −0.7819 % | −3.4200 % |
| 2019 | +2.3568 % | +0.3160 % |
| 2020 | +6.8818 % | +4.0841 % |
| 2021 | +0.9698 % | +3.4447 % |
| 2022 | −1.0008 % | −1.4283 % |
| 2023 | +1.0235 % | +1.3093 % |

Ledger consistency (V1b): `nav == cash + market_value` max error 5.8e-11 CNY; Σ POSITIONS value ==
`market_value` 1.5e-11; POSITIONS count == `n_positions` exactly on 2,315 days; cash reconstructed
from TRADES cash-flows within 0.027 CNY end-to-end; Σ daily fees == Σ trade `fee_total` exactly;
orders ledger statuses only `filled|blocked`, no negative fills.

Benchmarks and execution diagnostics (independent cross-checks):
out/{tag}_nav.csv reproduces the benchmark NAV series, and from it I recompute sh000300 +58.5131 %
and sz399006 +33.9745 % over the same window (identical for S0 and S1). Context for interpretation:
S0 76.21 % is +17.7 pp over the CSI300 with only 16.6 % average exposure, but the benchmark is a
price index and the strategy is full-cash 46.8 % of the time, so it is not a risk-adjusted
comparison. The runner extra file out/extra_diagnostics.json reports execution aggregates that match
my independent counts exactly: S0 chosen/filled 434/433, S1 660/657 (= the 1 and 3 selected-but-not-held
cases I find from POSITIONS); S1 fill rate 99.55 %, mean per-name budget utilisation 83.7 % (88.5 %
for S0); and - a genuine platform-rule property worth carrying into the report - **4 of S0 next-open
buys (0 of S1) were affordable at the signal close but not at the next open**, i.e. the platform T-close
affordability test is not a guarantee for the T+1 fill.

S0 vs S1 (both independently recomputed): return −4.4170 pp, max DD 0.6520 pp worse, trades +314,
fees +1,229.86 CNY (+21.7 %), avg exposure −0.64 pp. The decomposition control S1b (S0's affordability
test + S1's backfill) earns 77.49 %, so within the local engine the **backfill is mildly positive**
(+1.28 pp vs S0) while the **fee-inclusive stricter one-lot test is what costs ~5.7 pp**
(S1 71.79 % vs S1b 77.49 %). This is a result, not a defect; it bears on whether S1 should be promoted.

## V2 — fidelity

Full rule-by-rule table in `FIDELITY_CHECKLIST.md`. Outcome: §1–§5 all **MATCH** (exposure map;
last-trading-day month-end; day-2 initial with the E/C-first-day exception; E/C daily exit; prefix
filter; ≥230/260; 12-1 & 6-1 offsets; 61-return sample vol; 55 % cap; raw ≥2; 60-session mean amount
≥2e8; P>MA60>MA120; score weights; ranks over the broad feature population then `ok`; candidate
tie-break; top-20 buffer; top-10 truncation; **S0 truncate-then-drop with no backfill**; **S1
full-ranking feasibility with the exact `×1.001 + max(5,·) + 1e-5` one-lot cost, original-rank top-20
buffer and backfill to 10**; weights `exposure/n`). No MISMATCH in the signal rules.

Empirical cross-checks on the outputs: 66 rebalance dates per variant, all of them month-end trading
days and the first one 2014-08-29 (day 1 is an E/C day, so no initial book — same as the platform);
`n_selected == n_truncated − n_unaffordable` on all 66 S0 rows and `n_selected<10` on 54 of them
(mean 5.81), while S1 selects 10 on all 66; the recorded `affordable` flag equals
`raw·100 ≤ exposure·nav/10·1.02` on all 1,320 selection rows; every S1 chosen row passes the
fee-inclusive formula; 433 of 434 S0 chosen signals are held at the next close (1 blocked/cash case); 11 residual positions on 9 days come from blocked exits;
dropped-unaffordable names are held on 0/226 next-day snapshots.

Documented deviations (all intentional, none hidden): different data vendor/universe (§3.1), ST screen
inert because the panel's `isst` is always 0 (§3.3), 10 bp vs the platform's 20 bp slippage on the two
headline runs (§6.3; `S0_20bp` covers 20 bp), qfq-reinvest dividends instead of cash dividends (§6.9),
no 10 % volume cap (§6.4), historically correct stamp-tax switch (§6.5), and the explicit-sell exit
workaround for the engine's one-lot book tolerance (§6.8 — necessary; without it the portfolio
accumulates dust positions).

## V3 — no look-ahead

Structural argument with line references in `NO_LOOKAHEAD.md` §1–§4: orders made at T's close can only
fill at T+1's open (`core.py:818-856`); every `ctx` accessor slices `[:i+1]` (`core.py:679-817`);
the port touches only `ctx` (no panel/file I/O); the regime label for T is causal and its sequence
equals the platform's frozen `RUN_TEXT` on all 3,448 dates.

Empirical test (`lookahead_perturb.log`, **28/28 PASS**): with the deliverable window and the exact
runner configuration, (a) truncating the run at 2019-12-31 and (b) multiplying every panel row after
2019-12-31 by 1.7/×3/×2 (prices/amount/volume, consistently) both leave the first 1,345 sessions
**bit-identical** — NAV, cash, market value, exposure, 5,931 daily position rows, 463 trades, 195
orders, 41 rebalance decisions (max abs diff 0.0, atol 1e-9). Sanity: run C's NAV diverges after the
cutoff on all 1,644 days, so the perturbation is real. The reference run itself reproduces
`out/S0_nav.csv` to 5.8e-11 CNY and `out/S0_trades.csv` row-for-row, so the test applies to the
delivered run, not to a lookalike.

## V4 — risk-flat days (handoff §2)

`risk_flat_check.py`, **PASS, 6/6 S0 days and the 4 S1 days explained**:

| day | classification | independent finding |
|---|---|---|
| 2019-05-15, 16, 17, 20 (S0 and S1) | `all_held_suspended` | only holding 600277, 700 shares; the raw panel has **no bar** for 600277 on 2019-05-07..2019-05-20 (last 05-06, next 05-21) → engine suspension carry, NAV frozen at 159,390.0187 |
| 2019-11-08 (S0) | `prices_all_unchanged` | only holding 300015, 1,000 shares; raw close 41.4000015 on both 11-07 and 11-08 → zero mark-to-market change, no trade |
| 2021-02-05 (S0) | `offsetting_moves` | 002493 500 sh × (−1.98) = −990.00 CNY; 600926 1,500 sh × (+0.66) = +990.00 CNY; net 0.000000 |

No unexplained risk-flat day. (The 600277 episode is also the reason 16 E/C-state days carry a
position: the daily exit order was blocked by the suspension, and the sell was limited to the first
tradable sessions.) The handoff's *quarterly* 169 "risk zero-return days" cannot be reproduced from a
monthly run and is correctly not claimed.

## V5 — platform reproduction: **FAIL**

| series | total return | max drawdown |
|---|---|---|
| platform R2 reference (handoff / `metrics.json`) | **84.00 %** | **11.16 %** |
| local S0 (10 bp slippage) | 76.2105 % | 12.6545 % |
| gap | **−7.79 pp** | **1.49 pp worse** |
| local S0_20bp (platform's 20 bp slippage) | 73.4333 % | 11.7583 % |
| gap | **−10.57 pp** | 0.60 pp worse |

This is the material failing check. It cannot be blamed on costs: matching the platform's 20 bp
slippage makes the return *lower* (73.43 %), i.e. the platform's higher 84.00 % was earned despite
heavier assumed costs, so the gap lives in the holdings/signal path. The plausible drivers are exactly
the disclosed engine/data deviations — different data vendor and PIT universe (percentile ranks are
computed across a different population, which changes the selected names), cash-dividend vs
qfq-reinvest accounting, no volume cap, different board-limit modelling and different entry sizing.
None of these can be isolated without running the same rules on the platform itself, so the **cause is
UNVERIFIED**; what *is* verified is that the local run implements the platform rules faithfully
(V2). Per the handoff (§1: "若不能复现，先解释差异，不晋级候选") the S0/S1 local result must not be
presented as a reproduction of R2, and the candidate should not be promoted on this evidence.

## V6 — order-of-magnitude sanity (do not force agreement)

* `winrate_nonzero` S0 55.10 % vs platform Q0 54.82 % (+0.28 pp) — same ballpark, but Q0 is the
  **quarterly** R0 variant, so this is indicative only. **UNVERIFIED** as a direct comparison.
* `winrate_risk` 54.83 % vs Q0's 46.87 % and `winrate_all` 29.42 % vs 23.59 %: the local monthly
  strategy is in the market less often and exits faster, which mechanically lifts both ratios; not
  evidence for or against correctness. **UNVERIFIED.**
* all-cash days S0 1,083 (S1 1,067) vs platform Q0's 1,150 of 2,315: same order, again a different
  variant. The local decomposition is fully accounted for: 950 `state_forbidden` (E/C) + ~117
  `off_window_no_entry` (B/N day with no position before the next month-end rebalance) + rounding.
* The distribution of the local result is plausible for the exposure actually taken: avg exposure is
  only 16.6 % (S0) because the platform's `exposure·nav/10` one-lot budget, applied to 100 k capital
  and 100-share lots, leaves much of the intended 25 %/60 % uninvested; 46.8 % of days are pure cash.
  That is a property of the **platform rule**, not a porting bug (S0's 54 of 66 rebalances hold fewer
  than 10 names for exactly this reason).

## V7 — deliverable completeness

Present and consistent: `DAILY_AUDIT*.csv`, `POSITIONS*.csv`, `TRADES*.csv`, `ORDERS*.csv`,
`CASH_REASONS.csv` (2,315 rows = one mutually exclusive reason per day; `state_forbidden` only on E/C
days with zero holdings — checked), `RISK_FLAT_AUDIT_S0.csv` (6) / `_S1` (4), `REBALANCES.csv`,
`REBALANCE_SELECTIONS.csv`, `metrics.json`, `summary.csv`, `yearly.csv`, `segments.csv`, `rolling.csv`,
`cost_stress.csv`, `run.log`, `{tag}_nav/trades/orders/events/result`.

Gaps versus the handoff wording:
* 3 (per-name screening audit): **MOSTLY SATISFIED.** REBALANCE_SELECTIONS.csv has the signal-close estimate and decision for
  all 1,320 S0+S1 selections; out/extra_diagnostics.json adds the aggregate the handoff asks for
  (fill rate, per-name budget utilisation, count of next-open-unaffordable buys: S0 4/322, S1 0/468;
  S0 226 dropped names, mean/median price 207.95/99.48 CNY); TRADES.csv has the actual fills/fees.
  Still not tabulated: per-name next-open affordability, per-name capital utilisation, and the list of
  S1 names that displaced S0 names (derivable by diffing the selection sets, not pre-computed).
* §7 asks for cost pressure with "doubled commission/slippage". `S0_20bp` doubles slippage only and
  `S1_stress` doubles both; S0 with doubled commission is missing → **PARTIAL**.
* §1 reproduction: **FAIL** (V5).
* §4 "次胜率" trade-pairing reconciliation against the platform: the local FIFO block exists in
  `metrics.json` but the platform number cannot be recomputed locally → **UNVERIFIED** (not a defect).

## What is UNVERIFIED (stated, not hidden)

1. The cause of the −7.79 pp / −10.57 pp gap to platform R2 (cannot run the platform's data/engine here).
2. Platform transfer-fee and stamp-tax defaults (not visible in the platform scripts); the comparison
   absorbs them.
3. That the 10 % volume cap never binds (no volume cap in the local engine; order sizes suggest it is
   rare, not proven zero).
4. The platform "次胜率" (next-day win rate) pairing — local FIFO numbers exist, external ones do not.
5. Direct comparability of the Q0/R0 counts used as order-of-magnitude references (different variants).
6. Data-vintage revisions by the original vendor for rows before the audit date (manifest hash pins the
   snapshot used, nothing more).

## Reproduce

```
$env:PYTHONIOENCODING="utf-8"
$py = "C:\Users\汤禹恒\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py winrate_backtest_20261020\round3\verify\verify_metrics.py      # 144/144 PASS, exit 0
& $py winrate_backtest_20261020\round3\verify\lookahead_perturb.py   # 28/28 PASS, exit 0
& $py winrate_backtest_20261020\round3\verify\risk_flat_check.py     # 6/6 explained, exit 0
```
