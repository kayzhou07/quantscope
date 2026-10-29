# FIDELITY_CHECKLIST — platform S0/S1 vs the local port

Verified against the exact code that produced `round3/out/`:
`sha256(s0s1.py) = 3cc3b0c819922132f687f75e0637352c16f5dc045cbc154adca32a4c9d062a93`,
which equals the hash recorded in `out/run.log`. Runner:
`sha256(run_round3.py) = 139a78fa…`, also matching `run.log`.

Sources compared
- Platform S0: `winrate_backtest_20261020/SuperMind_S0_月度基线_胜率诊断_v03.py` (246 lines, VARIANT='R2').
- Platform S1: `winrate_backtest_20261020/SuperMind_S1_整手候选补位_待验证_v03.py` (267 lines). A `difflib`
  diff shows exactly two changes vs S0: the in-line selection/affordability block in `selection()` is
  replaced by `pick_affordable_ranked(...)`, and that function is added (S0:102-108 → S1:101-103,160-183).
- Local port: `winrate_backtest_20261020/round3/s0s1.py` (`snapshot()`, `PlatformPort._pick()`,
  `PlatformPort._rebalance()`), runner `round3/run_round3.py`.
- Engine (READ-ONLY): `qrun_1416/engine/{core,data,metrics}.py`; API doc `engine/API.md`, rules `engine/RULES.md`.
- Handoff: `winrate_backtest_20261020/DeepSeek_胜率诊断下一轮指令_20261020.md`.
- Regime input: `qrun_1416/os/out/regime_v1.csv`.

Verdict vocabulary
- **MATCH** — local reproduces the platform rule as written (engine mechanics allowed if equivalent).
- **INTENTIONAL DEVIATION** — mechanism differs because of the local engine/data; justified/disclosed; effect noted.
- **MISMATCH** — bug or unrequested behaviour change. **None found in the signal rules (§1–§5).**

Run configuration actually used by the runner (`run_round3.py:73-97`):
`start=2014-07-01, end=2023-12-29, capital=100000, warmup_days=340, rebalance="M",
slippage_bps=10.0, commission_bps=2.5, min_commission=5.0, regime_labels=regime_v1.csv,
benchmark=[sh000300,sz399006]`; engine defaults otherwise: `exec_timing="next_open"`,
`dividend_mode="qfq_reinvest"`, `max_pending_days=20`, `allow_st_buy=False`, `infer_missing_actions=True`.

---

## 0. INPUT FIDELITY (independent, done before reading the port)

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 0.1 | Platform frozen `RUN_TEXT` state vs `os/out/regime_v1.csv` on **all** dates (BULL→B, NEUTRAL→N, BEAR→E, CRASH→C) | **MATCH — 0 mismatches / 3,448 dates** | independent script; both series 2012-08-03..2026-10-19 |
| 0.2 | Transition count | 88 frozen tokens = 88 transitions in the CSV (`state != state_prev`, excl. first row) | same script |
| 0.3 | Regime labels are causal (trailing windows, forward state machine, no centring/shift(-k)) | PASS | `os/regime_v1.py:116-177` (see `NO_LOOKAHEAD.md` N4) |
| 0.4 | Label-to-day convention: platform `bisect_right(RUN_DATES, day)-1` (last transition ≤ T) vs local `ctx.regime` = row for T | MATCH given 0.1 | `S0:40-44`, `core.py:321-324` |
| 0.5 | Data snapshot traceable: `manifest_sha256` in run.log = `9294d2b82b235666871340eb93b51c10fc0a449327be0b1d051c83b89e03b7db` | PASS (matches `data/meta/manifest.json`) | `out/run.log` |

## 1. STATE → EXPOSURE

| # | Rule | Platform | Local (s0s1.py) | Verdict |
|---|---|---|---|---|
| 1.1 | BULL → 0.60 | `S0:57` | `EXPOSURE={"B":0.60}` (`:169`), `_exposure` (`:197-198`) | **MATCH** |
| 1.2 | NEUTRAL → 0.25 | `S0:57` | `"N":0.25` | **MATCH** |
| 1.3 | BEAR → 0.0 | `S0:57` | `"E":0.0` | **MATCH** |
| 1.4 | CRASH → 0.0, no separate rule in R2 | `S0:57,238` | `"C":0.0` | **MATCH** |
| 1.5 | state of day T (T-close label) drives a T+1-open order | `S0:228-243` | `ctx.regime` at T close; engine fills T+1 open (`core.py:834-835`) | **MATCH** |
| 1.6 | exposure is a weight fraction; weights sum to exposure, cash residual | `S0:115` | `w = exposure/len(selected)` (`:359-361`) | **MATCH** |

## 2. CALENDAR / REBALANCE TIMING

| # | Rule | Platform | Local | Verdict |
|---|---|---|---|---|
| 2.1 | rebalance signal on the **last trading day of the month** | `S0:230-234` (`now.month!=nxt.month or year differs`) | engine `rebalance="M"`: `i` is a rebalance day iff `pkeys[i] != pkeys[i+1]` (`core.py:615-631`), `on_rebalance` after `on_bar` | **MATCH** — empirically all 66 scheduled dates per tag are month-end trading days (`REBALANCES.csv` cross-checked against the 2,315-day DAILY_AUDIT calendar) |
| 2.2 | T+1 open fill | `S0:17` `enable_open_bar()` | engine default `exec_timing="next_open"` | **MATCH** |
| 2.3 | first-day initial target (executed day 2 open), skipped when day 1 is E/C | `S0:235,242-243` + precedence of `S0:238` | `on_bar`: `if not self._initialised: if state in ("B","N"): _rebalance(...,"initial")` (`:226-230`) | **MATCH structurally; first day 2014-07-01 label = E, so no initial book — same as the platform would do** (`REBALANCES.csv` starts at 2014-08-29) |
| 2.4 | no trading outside month-end unless state is E/C | `S0:238-243` | E/C branch (daily) + `on_rebalance` (monthly) only | **MATCH** — `REBALANCES.csv` has exactly 66 `scheduled` rows/tag and no other reason |
| 2.5 | pending book retried daily, expires after ~20 sessions | `S0:169-172,209-213` | engine `max_pending_days=20` (`core.py:1844-1853`); 2 `target_book_expired` events per tag | **MATCH** (engine expiry condition `i-created_i>=20` vs platform `age>20`; one-trading-day edge only) |
| 2.6 | newer month-end target replaces pending book | `S0:120` | `ctx.set_targets` replaces the pending book (API.md) | **MATCH** |
| 2.7 | warm-up: 260-session window with ≥253 sessions | `S0:59-60` | bundle loads 340 warm-up rows before 2014-07-01 (from 2013-01-29); first signal day has ≥340 rows | **MATCH** |

## 3. UNIVERSE / SCREENING

| # | Rule | Platform | Local | Verdict |
|---|---|---|---|---|
| 3.1 | universe = listed stocks on signal day | `S0:61` `get_all_securities('stock',day)` | `ctx.universe(tradable=True, exclude_st=True)` (`:106`): panel 4,867 codes with `universe.csv` in/out dates, a bar today, tradestatus=1 | **INTENTIONAL DEVIATION (data)** — different vendor/PIT pool; size differences change percentile ranks (a first-order driver of the S0-vs-R2 level difference, see `VERIFY.md` §5). Not quantifiable locally. |
| 3.2 | board prefix filter: SH 600/601/603/605, SZ 000/001/002/003/300/301 | `S0:62` | `board_ok()` (`:83-85`) with the identical tuples (`:79-80`) | **MATCH** |
| 3.3 | exclude ST on signal day | `S0:80-81` | `exclude_st=True` — but `isst` is **always 0** in the published panel (`API.md` §Data-runner limitations) | **INTENTIONAL DEVIATION (data)** — ST screen inert locally. Disclosed; not a port bug. |
| 3.4 | exclude paused; require a bar on the signal day | `S0:74,80-81` | `tradable=True` requires finite close/tradestatus today; `last_bar` on qfq (`:122`) | **MATCH** |
| 3.5 | ≥230 of 260 sessions have a (positive) qfq close | `S0:75-76` | `nvalid = isfinite(...).sum(axis=0) >= 230` (`:120,138`) | **MATCH** (platform additionally masks non-positive closes to NaN; not material for this panel's candidates) |
| 3.6 | after ffill, require non-NaN at `-253` and `-127` | `S0:77-78` | `base &= isfinite(x_m253) & isfinite(x_m127)` (`:136-140`) | **MATCH** |
| 3.7 | qfq closes for signals, raw closes for price/limit decisions | `S0:66-67` | `history_matrix("qfq_close",260)` for signals, `ctx.latest("close")` for raw price (`:112,131`) | **MATCH** |
| 3.8 | 12-1 = `x[-22]/x[-253]-1`, 6-1 = `x[-22]/x[-127]-1` | `S0:90` | `df.iloc[-22]/df.iloc[-253]-1`, `df.iloc[-22]/df.iloc[-127]-1` (`:128-129`) on the 260-row window | **MATCH** |
| 3.9 | 60d annualised vol = sample std of last 61 returns × √252 | `S0:82-83` | `ret.iloc[-61:].std()*sqrt(252)` (`:126-127`), pandas default ddof=1 | **MATCH** |
| 3.10 | vol cap 0.55 (R2/R3; removed only in R4) | `S0:88` | `max_vol=0.55`, `ok &= rvol<=max_vol` (`:147`) | **MATCH** |
| 3.11 | raw close ≥ 2.00 | `S0:85,88` | `min_price=2.0`, `raw>=min_price` (`:146-147`) | **MATCH** |
| 3.12 | 60-session mean traded value ≥ 2e8 CNY (SuperMind `turnover` = local `amount`) | `S0:86` | `amt60 = history_matrix("amount",60).mean(axis=0)`, `>=2e8` (`:132-134,144`) | **MATCH** |
| 3.13 | trend `P > MA60 > MA120` (MA on ffilled qfq) | `S0:87` | `(close>ma60)&(ma60>ma120)` with `iloc[-60:]`/`iloc[-120:]` means (`:124-125,145`) | **MATCH** |
| 3.14 | eligibility distinct from feature availability | `S0:88-90` | separate `base` / `ok` masks (`:138-147`) | **MATCH** |

## 4. SCORING / RANKING / SELECTION

| # | Rule | Platform | Local | Verdict |
|---|---|---|---|---|
| 4.1 | `RAS = (x[-22]/x[-253]-1)/vol` | `S0:90` | `ras=(mom12/rvol)` (`:149`) | **MATCH** |
| 4.2 | `score = .5·rank(RAS) + .3·rank(M6) + .2·rank(1/VOL)` | `S0:99` | `:156` | **MATCH** |
| 4.3 | ranks computed over the broad feature population (all rows passing the history gate, **including `ok=False`**), then restricted by `ok` | `S0:94-100` | `ras/m6/inv = ... .where(base)` then `rank(pct=True, na_option="keep")` over the base population, then `sel = score[ok]` (`:149-157`) | **MATCH** — the easy-to-miss detail, and it is correct |
| 4.4 | percentile ranks with pandas default (`average` ties) | `S0:97-99` | `rank(pct=True)` (`:153-154`) | **MATCH** |
| 4.5 | candidate order: descending score, ties broken by ascending symbol | `S0:101` | `sorted(sel.index, key=lambda c: (-score, str(c)))` (`:161`) | **MATCH** (local codes are the same 6-digit numbers; the `.SH/.SZ` suffix cannot reorder two 6-digit codes) |
| 4.6 | retention buffer: held names with candidate rank < 20 first (by rank, ≤10), then fill from the ranking | `S0:102-106` | `held=[c for c in ctx.positions if c in cands]` sorted by rank; `[c for c in held if rank[c]<HOLD_BUFFER][:10]`; then fill (`:244-253`); `HOLD_BUFFER=20` (`:76`) | **MATCH** |
| 4.7 | at most 10 names | `S0:103-106` | `top_n=10` (`:75`) | **MATCH** |
| 4.8 | affordability NAV = portfolio total value at signal close | `S0:107` | `nav = float(ctx.nav)` (`:309,342`) | **MATCH** |
| 4.9 | target weights = `exposure / len(selected)` | `S0:115` | `:359` | **MATCH** — empirical: 439/444 (S0) and 661/668 (S1) executed position weights within one lot + 50 bp of `exposure/n_selected`; S0's 434 "chosen" signals → 433 held at the next close |

## 5. AFFORDABILITY (the only S0↔S1 difference)

| # | Rule | Platform S0 | Local | Verdict |
|---|---|---|---|---|
| 5.1 | S0: truncate to ≤10 **first**, then drop one-lots above `exposure*nav/10*1.02`, **no backfill** | `S0:108` | buffer+fill to 10 first, then `kept` filtered by `p*100 <= budget*1.02`; no re-fill (`:259-282`) | **MATCH** — `REBALANCES.csv`: `n_selected == n_truncated − n_unaffordable` on all 66 rows; `n_selected<10` on 54/66 dates (mean 5.81), `n_truncated` capped at 10 |
| 5.2 | S0: surviving names up-weighted to `exposure/len(selected)` | `S0:115` | `:359` | **MATCH** |
| 5.3 | S1: affordability over the **full** ranking, budget = `nav*exposure/10`; need = `raw*100*1.001 + max(5, raw*100*1.001*0.00025) + raw*100*1.001*0.00001` | `S1:160-174` | `feasible()` (`:284-291`) identical formula, `budget=exposure*nav/top_n` (`:246`) | **MATCH** — `REBALANCE_SELECTIONS.csv`: all 660 S1 chosen rows satisfy the formula; recorded `one_lot_cost` matches recomputation to <1e-6 |
| 5.4 | S1: retention buffer still on the **original top-20 rank**, feasible names only, then backfill to 10 | `S1:176-182` | `:297-302` | **MATCH** — S1 selects exactly 10 on all 66 dates, incl. the 54 dates where S0 had to drop names (`REBALANCES.csv`) |
| 5.5 | S1: weights `exposure/len(selected)` | `S1:110` | `:359` | **MATCH** |
| 5.6 | S1 changes nothing else | S0↔S1 diff | same `snapshot`, same buffer, same state/exit code; only `_pick` branches on `mode` | **MATCH** — `n_candidates` identical for S0/S1/S1b on every date (one shared snapshot); 66 rebalance dates each |
| 5.7 | affordability is evaluated at the **signal close** raw price (not T+1 open) | `S0:108`, `S1:167-171` | uses `feats.raw` = T-close raw close (`:131`) | **MATCH** |

## 6. EXECUTION / COSTS / ACCOUNTING (engine-level)

| # | Item | Platform | Local | Verdict |
|---|---|---|---|---|
| 6.1 | order size | `qty=max(0,int((nav*w−5)/(open*1.001*100))*100)` from the next open (`S0:174-184`) | target value `w*signal_nav`, `floor(value/raw_open/100)*100`, then cash-capped with real fees (`core.py:1710-1713,1329-1331`) | **INTENTIONAL DEVIATION (engine)** — entry sizing differs by up to one lot; empirical residue: S0 1/434 and S1 3/660 selected names not held at the next close (blocked or cash), 11 residual positions on 9 days (blocked exits) |
| 6.2 | commission 0.00025/side, min 5 CNY | `S0:18` | engine defaults `commission_bps=2.5, min_commission=5.0` | **MATCH** |
| 6.3 | slippage | `S0:19` `PriceSlippage(.002)` = **20 bp/side** | primary runs use `slippage_bps=10.0`; the runner also runs `S0_20bp` at 20 bp | **INTENTIONAL DEVIATION (documented)** for the headline S0/S1 numbers: 10 bp vs 20 bp; the platform-comparable variant is `S0_20bp` (73.43 % vs S0 76.21 %) |
| 6.4 | volume limit 10 % of bar volume per side | `S0:20` | engine has no volume cap | **INTENTIONAL DEVIATION (engine limitation, disclosed)** — orders are ~1–6 k CNY on names with ≥2e8 60-day mean traded value, so binding is unlikely but not proven zero |
| 6.5 | stamp tax | script sets only `PerShare`; platform default (constant sell tax) | engine: 0.1 % before 2023-08-28, 0.05 % after (`core.py:139-141`) | **INTENTIONAL DEVIATION (engine)** — local is historically correct; platform comparison absorbs it. Effect on S0 = 0.05 % × sell turnover in 2023-08-28..2023-12-29 only (≤ ~0.01 pp) |
| 6.6 | transfer fee | platform default | engine 1e-5 both sides (`core.py:148`) | **UNVERIFIED** (platform default not observable from the script; engine value is documented) |
| 6.7 | limit-up/limit-down blocks; ST not buyable; T+1 | `S0:191-195` | engine enforces ±10 % / ChiNext ±20 % after 2020-08-24, IPO-day block, T+1 (`core.py:1297-1320`) | **MATCH in intent / INTENTIONAL DEVIATION in detail** (board-limit rules modelled per exchange history, 32/34 `target_blocked` events) |
| 6.8 | exit of names outside the target | `order_target(s,0)` inside the book (`S0:207`) | explicit `ctx.sell(c, fraction=1.0)` + empty-book `set_targets` (`:200-215`), executed in the same T+1 open before book buys (`core.py:1509-1519`) | **INTENTIONAL DEVIATION (engine workaround, documented in code)** — necessary because the engine book tolerates one-lot residuals; without it dust holdings accumulate (the Lead first observed 30 positions against a 10-name cap) |
| 6.9 | dividends / splits | platform cash-dividend account + adjusted shares | engine `dividend_mode="qfq_reinvest"`: total return via qfq factor, dividends implicitly reinvested (`API.md` §3); 32 `corporate_action` adjusts in S0, 54 in S1 | **INTENTIONAL DEVIATION (engine)** — small positive compounding bias vs a cash-dividend account; disclosed |
| 6.10 | NAV = cash + marks; cash = available+frozen | `S0:217-218` | engine NAV identity + hard daily P&L decomposition (`core.py:1197-1210`); independently re-verified by `verify_metrics.py` I1/I4/I5 | **MATCH** |
| 6.11 | delisting liquidation | platform rules | engine forced liquidation at `out_date` | **MATCH in intent** |
| 6.12 | suspension: cannot buy/sell a paused name; position keeps last close | `S0:191-195` | engine carries blocked orders, marks at last close (`API.md`) | **MATCH** — explains all 4 suspension risk-flat days (600277, see `VERIFY.md` §4) |
| 6.13 | buy-side cash reduction loop in 100-lot steps | `S0:199-206` | engine `max_affordable_shares` (commission+transfer, real rounding) | **MATCH in intent / minor rounding differences** |

## 7. DIAGNOSTICS REQUIRED BY THE HANDOFF

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 7.1 | DAILY_AUDIT: pnl=nav−prev, ±1e-6 thresholds | **MATCH** | independent recompute reproduces every day count exactly (`verify_metrics.log`: S1 676/577/1062; S0 681/555/1079) |
| 7.2 | risk day = positions today or at prior close (exit day counts) | **MATCH** | `risk_days` 1257 (S1) / 1242 (S0) reproduced from DAILY_AUDIT and independently from POSITIONS |
| 7.3 | winrate_all / winrate_risk / non-zero winrate / flat & pure-cash-flat counts | **MATCH** | 144/144 checks in `verify_metrics.log` |
| 7.4 | mean win/loss day, daily profit factor | **MATCH** | same |
| 7.5 | `RISK_FLAT_AUDIT` rows with nav/cash/holdings/pending book | **MATCH** | 6 S0 / 4 S1 rows; each independently explained (`VERIFY.md` §4) |
| 7.6 | max drawdown on close NAV with peak/trough | **MATCH** | S0 −12.6545 % (peak 2019-11-19 → trough 2020-03-19); S1 −13.3065 % (2017-11-22 → 2020-03-23) reproduced |
| 7.7 | benchmark set, not used for decisions | **MATCH** | `benchmark=["sh000300","sz399006"]` reporting only |

## 8. HANDOFF REQUIREMENTS

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 8.1 | reproduce platform R2 84.00 % / 11.16 % on the full NAV series | **FAIL / NOT REPRODUCED** | local S0 = 76.2105 % / −12.6545 %; with the platform's own 20 bp slippage, S0_20bp = 73.4333 % / −11.7583 %. Gaps: −7.79 pp return, −1.49 pp drawdown (10 bp) and −10.57 pp / −0.60 pp (20 bp). Not explainable by cost settings; cause not decomposable locally (see `VERIFY.md` §5). |
| 8.2 | S1 run under identical conditions, one variable | **MATCH** | identical runner/window/params; only `mode` differs; `n_candidates` identical per date |
| 8.3 | per-name audit of S1 lot screening and backfill (rejected names, predicted fees, next-open affordability, actual shares, capital utilisation) | **MOSTLY SATISFIED** | `REBALANCE_SELECTIONS.csv` records rank, raw price, one-lot value, estimated buy cost and the decision for all 1,320 S0+S1 selections (226 dropped in S0); `TRADES.csv` has actual fills/fees; `extra_diagnostics.json` adds fill rate (99.55 % S1), mean per-name budget utilisation (83.7 % S1 / 88.5 % S0), next-open-unaffordable buys (S0 4/322, S1 0/468) and the dropped-name price distribution (mean/median 207.95/99.48 CNY). **Not tabulated**: per-name next-open affordability, per-name capital utilisation, and the list of S1 names that displaced S0 names (derivable by diffing S0/S1, not pre-computed). |
| 8.4 | cost pressure: original vs doubled commission/slippage | **PARTIAL** | `cost_stress.csv` has S0→S0_20bp (2× slippage only) and S1→S1_stress (2× commission + 2× slippage). S0 with 2× commission is **not** run. Deltas internally consistent (checked). |
| 8.5 | no parameter search; no new candidates beyond S0/S1 | **MATCH with note** | variants = S0, S1, S1b (labelled diagnostic decomposition of S1), S0_20bp and S1_stress (cost pressure). No parameter grid; the handoff allows one extra single-variable candidate registration, and S1b is not presented as a candidate. |

---

## Summary

* Signal-rule fidelity (§1–§5): **all MATCH, zero MISMATCH**. The subtle items that are right:
  ranks over the broad feature population then `ok` filter (4.3), truncate-then-affordability with
  no backfill and up-weighting (5.1/5.2), full-ranking affordability with the fee-inclusive one-lot
  estimate and original-rank top-20 buffer in S1 (5.3/5.4), last-trading-day month-end, day-2 initial
  and E/C daily exit, and the affordability check at the T-close raw price.
* Engine/data deviations (§3.1/3.3, §6): all disclosed in the code/runner; the material ones are the
  different data vendor/universe (unquantifiable locally), ST screen inert, 10 bp vs 20 bp slippage on
  the headline runs, qfq-reinvest dividends, no volume cap, and the explicit-sell exit workaround.
* Handoff gaps: **8.1 not reproduced** (material); 8.3 and 8.4 are partial deliverables.
