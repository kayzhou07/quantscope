# DATA & REGIME FIDELITY AUDIT — round-3 S0/R2 handoff

- **Auditor:** data-auditor (task-1)
- **Audit time:** 2026-10-20 (Asia/Shanghai)
- **Scope:** input-data / regime fidelity ONLY. No strategy changes, no backtest runs.
- **Read-only guarantee:** `qrun_1416\` was never written. Every one of the 53 files listed in
  `qrun_1416\data\meta\manifest.json` was re-hashed during this audit (bytes + sha256) and matched
  the manifest; `os\out\regime_v1.csv` was hashed separately. Nothing was modified outside
  `winrate_backtest_20261020\round3\audit\`.
- **Normalised mapping used throughout:** `BULL→B`, `NEUTRAL→N`, `BEAR→E`, `CRASH→C`.
- **Window:** 2014-07-01 .. 2023-12-29 (the S0/R0/R2 backtest window in the handoff).
- **Tooling:** pandas 3.0.1 / numpy 2.3.5; scripts re-runnable (see §8).

## 0. Bottom line

The **frozen regime input is exact**: the `RUN_TEXT` literal embedded in
`SuperMind_S0_月度基线_胜率诊断_v03.py` reproduces `qrun_1416\os\out\regime_v1.csv` on
**3,448 / 3,448 rows, 0 mismatches**, and the regime file date set is exactly the exchange calendar's
open days over its span. The panel/manifest are byte-identical to what the engine recorded. Three
non-blocking data defects were found and quantified: a single non-trading date in the panel
(2021-06-27, 7 orphan rows), a delisting-tail mismatch pattern (all "after out_date" bars sit on that
same bogus date), and the known lossy flags (`isst≡0`, `tradestatus≡1`, `turn≡NaN`) which mean ST
exclusion and suspension flags cannot be rebuilt from the panel. None of these changes the regime
fidelity conclusion; the practical risk is bounded and listed in §7.

## 1. PASS/FAIL table

| # | Claim under audit | Evidence | Verdict |
|---|---|---|---|
| 1 | RUN_TEXT states match `regime_v1.csv` on **all 3,448 rows** | 3,448 compared, 3,448 match, **0 mismatch**; `regime_compare.csv` | **PASS** |
| 2 | Regime CSV has one row per trading day over 2012-08-03..2026-10-19 | CSV 3,448 rows = calendar open days in that span (3,448); date-set equality; 0 gaps | **PASS** |
| 3 | Window 2014-07-01..2023-12-29 has 2,315 trading days | calendar 2,315 open days = regime rows 2,315 | **PASS** |
| 4 | State mix in window | B 374 / N 975 / E 474 / C 492; 59 state runs (58 changes) | **PASS (computed)** |
| 5 | ≥253 trading days of warm-up before 2014-07-01 so 253-row momentum is defined from day 1 | price panel first bar **2012-01-04**; **600** open days from panel start through 2014-06-30 (**459** even if counting only from the 2012-08-03 regime start) | **PASS** |
| 6 | `manifest.json` sha256 = engine-recorded `9294d2b8…e03b7db` | computed `9294d2b82b235666871340eb93b51c10fc0a449327be0b1d051c83b89e03b7db` | **PASS** |
| 7 | Manifest lists 53 files and date_range 2012-01-01..2026-10-19 | 53 entries, all 53 byte+sha256 verified, 472,628,818 bytes exact | **PASS** |
| 8 | Universe is main + ChiNext only, no STAR/688, no BSE | 4,941 codes: main 3,489, chinext 1,452; prefixes only 600/601/603/605 + 000/001/002/003 + 300/301; 0 codes with 688/689/4xx/8xx/9xx | **PASS** |
| 9 | `isst` always 0, `tradestatus` always 1 | 12,438,891 raw bar rows 2012-2026: `isst` unique {0}, `tradestatus` unique {1}, 0 nulls in either | **PASS (and flagged as risk, §7)** |
| 10 | qfq panel is forward-adjusted and anchored at the panel end | 4,590/4,595 codes: qfq/raw = 1.0 on last bar (2026-10-19); median ratio 0.476 on first panel date 2012-01-04 ⇒ end-anchored | **PASS with caveat** (5 codes have a pending ex-date; not PIT) |
| 11 | Panel contains only real trading dates | **FAIL (minor):** panel has 1 non-trading date, **2021-06-27 (Sunday)**, 7 rows (codes 300028/300090/300104/300156/300216/300372/300431); `pctchg=0`, `preclose=close` | **FAIL (minor, impact bounded §7.6)** |
| 12 | IPO / delisting coverage is complete | 4,941/4,941 have `ipo_date`; 2,161 IPOs inside window, all have bars, none before IPO date; 336 delisted (status=0 ≡ `out_date` non-null), 152 with `out_date` inside window | **PASS with caveats** (§7.7–7.9) |
| 13 | Checkpoint eligible-history universe (≥230 valid qfq bars in prior 253 trading days) | 2014-07-01: 1,966 · 2019-01-02: 3,010 · 2023-12-29: 4,359 | **PASS (computed; proxy)** |
| 14 | Missing-bar / suspension rate inside window | 3.687% overall (7,964,340 actual vs 8,269,198 expected listed-code-days); 9.3%→0.2% per-year gradient | **PASS (computed)** |

## 2. Item 1 — Regime fidelity (highest priority)

**Source of truth A** = `RUN_TEXT` string literal at line 11 of
`winrate_backtest_20261020\SuperMind_S0_月度基线_胜率诊断_v03.py` (899 chars, **90 tokens**,
`YYYMMDD` + state letter). A state holds from its own date until the next token date
(`bisect_right` on token dates, i.e. effective-date inclusive — same rule as `state_at()` in S0).
**Source of truth B** = `qrun_1416\os\out\regime_v1.csv` (column `state`, mapped BULL→B etc.;
0 rows had an unmappable state).

| Metric | Value |
|---|---|
| RUN_TEXT tokens | 90 (first `20120803N`, last `20261016E`; token-date span 2012-08-03..2026-10-16) |
| RUN_TEXT literal sha256 | `fa6b69534bb710d83d5361ae42f39c2e3b7da6942c5ab72224dc1d5f67953040` |
| regime_v1.csv rows compared | **3,448** (2012-08-03 .. 2026-10-19, no duplicate dates) |
| **Matches** | **3,448** |
| **Mismatches** | **0** |
| First 20 mismatches | none (empty list) |
| CSV state counts | NEUTRAL 1,601 / BEAR 775 / BULL 550 / CRASH 522 |
| CSV date set vs calendar open days in span | 3,448 = 3,448; **identical sets**, 0 missing / 0 extra days |
| All 90 token dates are calendar trading days present in the CSV | 90 / 90, no duplicate consecutive letters |
| S1 / Q0 / R2 scripts carry the **identical** RUN_TEXT | yes for all three (`S1`, `Q0`, `R2_v02` → True) |
| CSV internal checks | dates monotonic, 0 duplicates; `state_prev` = previous row's state on all 3,447 comparable rows (0 mismatch); 90 state runs = 90 tokens |

⇒ **The handoff claim "the embedded states match the CSV on all 3,448 rows" is CONFIRMED.** The
regime file is also a fault-free daily series over its span (no missing trading day), so
`state_at()` never has to bridge a gap. Note: the CSV's auxiliary columns (`crash_flag`,
`bear_flag`, `bull_flag`) are the regime state machine's *raw trigger* indicators, not state
indicators (e.g. `crash_flag=1` always implies `CRASH` — 0 violations — but 92 `CRASH` rows have
`crash_flag=0` because the state persists after the trigger clears: a hysteresis property, not an
inconsistency). S0 reads only the frozen `RUN_TEXT`/`state`, never these flags, so they have no
fidelity impact on the backtest. Artifacts: `audit\regime_compare.csv`
(`date,run_letter,csv_state,csv_letter,match`), `audit\regime_audit.json`,
`audit\regime_internal.json`.

## 3. Item 2 — State distribution inside the backtest window (2014-07-01..2023-12-29)

2,315 trading days, 58 in-window regime changes ⇒ **59 distinct state runs**.

| State | Letter | Days | Share | S0/R2 exposure |
|---|---|---|---|---|
| BULL | B | **374** | 16.2% | **0.60** |
| NEUTRAL | N | **975** | 42.1% | **0.25** |
| BEAR | E | **474** | 20.5% | **0.00** |
| CRASH | C | **492** | 21.3% | **0.00** |
| — | E+C combined | **966** | 41.7% | **0.00** |

Days mapped to exposure tier: **0.60 → 374**, **0.25 → 975**, **0.00 → 966** (= 2,315).
Window trading days by year: 2014 126, 2015 244, 2016 244, 2017 244, 2018 243, 2019 244,
2020 243, 2021 243, 2022 242, 2023 242. All 58 in-window token dates fall on 58 of them; the run
open on 2014-07-01 is `E` carried over from the 2014-01-15 token.

Every state change in the window (start date incl. carried-over start → letter / exposure, held
through the date shown; machine-readable full list: `audit\regime_audit.json → window.blocks`):

| # | From | State | Exp | Through |
|---|---|---|---|---|
| 1 | 2014-07-01 | E | 0.00 | 2014-08-01 |
| 2 | 2014-08-01 | N | 0.25 | 2014-09-18 |
| 3 | 2014-09-18 | B | 0.60 | 2014-12-26 |
| 4 | 2014-12-26 | N | 0.25 | 2015-01-27 |
| 5 | 2015-01-27 | B | 0.60 | 2015-06-19 |
| 6 | 2015-06-19 | C | 0.00 | 2016-07-13 |
| 7 | 2016-07-13 | E | 0.00 | 2016-08-19 |
| 8 | 2016-08-19 | N | 0.25 | 2016-09-20 |
| 9 | 2016-09-20 | E | 0.00 | 2016-10-24 |
| 10 | 2016-10-24 | N | 0.25 | 2017-03-09 |
| 11 | 2017-03-09 | B | 0.60 | 2017-04-06 |
| 12 | 2017-04-06 | N | 0.25 | 2017-08-22 |
| 13 | 2017-08-22 | B | 0.60 | 2017-11-02 |
| 14 | 2017-11-02 | N | 0.25 | 2018-04-20 |
| 15 | 2018-04-20 | E | 0.00 | 2018-05-14 |
| 16 | 2018-05-14 | N | 0.25 | 2018-07-02 |
| 17 | 2018-07-02 | E | 0.00 | 2018-08-06 |
| 18 | 2018-08-06 | C | 0.00 | 2018-08-13 |
| 19 | 2018-08-13 | E | 0.00 | 2018-08-15 |
| 20 | 2018-08-15 | C | 0.00 | 2018-08-27 |
| 21 | 2018-08-27 | E | 0.00 | 2018-09-06 |
| 22 | 2018-09-06 | C | 0.00 | 2018-09-26 |
| 23 | 2018-09-26 | E | 0.00 | 2018-10-08 |
| 24 | 2018-10-08 | C | 0.00 | 2019-02-15 |
| 25 | 2019-02-15 | E | 0.00 | 2019-02-28 |
| 26 | 2019-02-28 | N | 0.25 | 2019-05-13 |
| 27 | 2019-05-13 | C | 0.00 | 2019-06-10 |
| 28 | 2019-06-10 | N | 0.25 | 2019-09-10 |
| 29 | 2019-09-10 | B | 0.60 | 2019-10-09 |
| 30 | 2019-10-09 | N | 0.25 | 2019-10-17 |
| 31 | 2019-10-17 | B | 0.60 | 2019-10-24 |
| 32 | 2019-10-24 | N | 0.25 | 2019-12-20 |
| 33 | 2019-12-20 | B | 0.60 | 2020-02-03 |
| 34 | 2020-02-03 | C | 0.00 | 2020-02-10 |
| 35 | 2020-02-10 | N | 0.25 | 2020-02-24 |
| 36 | 2020-02-24 | B | 0.60 | 2020-03-20 |
| 37 | 2020-03-20 | E | 0.00 | 2020-03-23 |
| 38 | 2020-03-23 | C | 0.00 | 2020-04-03 |
| 39 | 2020-04-03 | E | 0.00 | 2020-04-20 |
| 40 | 2020-04-20 | N | 0.25 | 2020-06-05 |
| 41 | 2020-06-05 | B | 0.60 | 2020-09-16 |
| 42 | 2020-09-16 | N | 0.25 | 2021-05-31 |
| 43 | 2021-05-31 | B | 0.60 | 2021-06-18 |
| 44 | 2021-06-18 | N | 0.25 | 2022-01-18 |
| 45 | 2022-01-18 | E | 0.00 | 2022-03-15 |
| 46 | 2022-03-15 | C | 0.00 | 2022-03-22 |
| 47 | 2022-03-22 | E | 0.00 | 2022-03-30 |
| 48 | 2022-03-30 | C | 0.00 | 2022-04-19 |
| 49 | 2022-04-19 | E | 0.00 | 2022-04-21 |
| 50 | 2022-04-21 | C | 0.00 | 2022-06-06 |
| 51 | 2022-06-06 | E | 0.00 | 2022-09-30 |
| 52 | 2022-09-30 | C | 0.00 | 2022-12-05 |
| 53 | 2022-12-05 | E | 0.00 | 2023-01-20 |
| 54 | 2023-01-20 | N | 0.25 | 2023-03-13 |
| 55 | 2023-03-13 | E | 0.00 | 2023-04-07 |
| 56 | 2023-04-07 | N | 0.25 | 2023-04-27 |
| 57 | 2023-04-27 | E | 0.00 | 2023-08-03 |
| 58 | 2023-08-03 | N | 0.25 | 2023-08-17 |
| 59 | 2023-08-17 | E | 0.00 | 2023-12-29 (window end) |

Observation for the Lead: the window opens in `E` (0% exposure) and 41.7% of all window days are
zero-exposure days (`E` or `C`), by construction this drives a large part of S0's cash-flat day
count (Q0 reported 1,150 pure-cash flat days out of 2,315 — same order of magnitude; not
re-derived here).

## 4. Item 3 — Panel coverage 2014-07-01..2023-12-29

Machine-readable: `audit\panel_coverage.json`. Panel used = `data\panel\bars_qfq_{year}.csv.gz`
(price panel; raw `bars_{year}.csv.gz` used for cross-checks).

### 4.1 Trading days and codes

| Metric | Value |
|---|---|
| Calendar open days in window | **2,315** |
| Distinct dates in qfq panel inside window | **2,316** (= 2,315 calendar days + bogus 2021-06-27) |
| Codes with ≥1 qfq bar in window | **4,681** |
| … by board | main **3,322**, chinext **1,359** |
| Codes present in panel anywhere 2012-2023 | 4,690 |
| Universe codes active at some point in window | 4,682 |
| Active-in-window codes with no bar at all | 1: `601268` (*ST二重; last bar 2014-04-28, suspended through its 2015-05-21 delisting — suspension-encoded absence, not a hole) |
| Bars in window (rows) | 7,964,340; all with close > 0 (0 non-positive closes); 7,964,333 on calendar days + 7 on the bogus date |
| Daily bar count (calendar days) | min 1,340 (2015-07-09, 2015 crash suspension wave), median 3,574, max 4,524 (2023-12-27) |
| Bars per code in window (median across the 4,681 codes) | 2,067 (of 2,315 potential days) |

### 4.2 Board breakdown and code-prefix validation (from `data\meta\universe.csv`, 4,941 codes)

| board | codes | prefixes present |
|---|---|---|
| main | 3,489 | 600 (875), 601 (233), 603 (625), 605 (113), 000 (506), 001 (122), 002 (973), 003 (42) |
| chinext | 1,452 | 300 (978), 301 (474) |

- **No STAR codes** (no 688/689): verified 0. **No BSE codes** (no 4xx/8xx/9xx): verified 0.
- Every code is 6 digits; every panel code exists in the universe (0 unknown), and every
  (board, prefix) pair is one of the expected ones (0 unexpected prefixes).
- ⇒ The panel's symbol space matches the S0 platform filter exactly (`600/601/603/605`,
  `000/001/002/003/300/301`). It is deliberately **not** the full A-share market (STAR/BSE out of
  scope), which is consistent by design, not a defect.

### 4.3 IPO / out_date coverage

| Metric | Value |
|---|---|
| `ipo_date` non-null / null | **4,941 / 0** (min 1990-12-10, max 2026-10-15) |
| Codes with IPO inside window | **2,161** |
| … with bars | **2,161** (0 without bars); codes whose first bar precedes the IPO date: **0** |
| `out_date` non-null (= status 0) | **336** (4,605 status=1); status-0 ⇔ out_date non-null fully consistent |
| Delisted with out_date inside window | **152**; 151 have bars in window, 1 (`601268`) is suspended for the entire window |
| Delisted **name count** | **336** delisted / 4,605 active in universe |
| Last bar vs out_date (152 in-window delistings) | equal **0**, before **144**, after **7**, none-in-window 1 |
| "before" gap (out_date − last bar, trading days) | n=144; median **4**, p90 33, max **270** (`600485`, last bar 2020-04-20 vs out_date 2021-06-01), 24 codes > 20 days |
| "after" cases | all **7** are exactly the bogus 2021-06-27 single-bar rows (e.g. `300372` last bar 2021-06-27 vs out_date 2017-08-28) |

Interpretation: the panel's documented convention is `tradestatus=1` everywhere and
**suspension = missing row**, and `out_date` is not necessarily the last traded day — long
pre-delisting suspensions are expected for *ST names. The "before" pattern is therefore consistent
with the convention; it cannot be cross-checked against an independent source in this environment
(§7.7 UNVERIFIED). The "after" pattern is fully explained by the single bogus date.

### 4.4 Missing-bar (suspension) rate in window

Denominator = listed-code-days computed from `universe.ipo_date` / `out_date`
(active ⇔ ipo_date ≤ d < out_date, alternate inclusive-out_date count also computed); numerator =
actual qfq rows. All 2,315 window days.

| | Actual bars | Expected listed-code-days | Missing rate |
|---|---|---|---|
| Window total | **7,964,340** | **8,269,198** | **3.687%** |
| alt. inclusive out_date | 7,964,340 | 8,269,350 | 3.688% |

| Year (window part) | Trading days | Actual bars | Expected | Missing rate |
|---|---|---|---|---|
| 2014 (H2) | 126 | 291,758 | 321,751 | **9.32%** |
| 2015 | 244 | 569,943 | 665,666 | **14.38%** |
| 2016 | 244 | 641,721 | 704,633 | **8.93%** |
| 2017 | 244 | 743,483 | 799,552 | **7.01%** |
| 2018 | 243 | 817,231 | 857,283 | **4.67%** |
| 2019 | 244 | 880,569 | 885,957 | **0.61%** |
| 2020 | 243 | 913,206 | 919,208 | **0.65%** |
| 2021 | 243 | 986,444 | 989,912 | **0.35%** |
| 2022 | 242 | 1,040,394 | 1,043,312 | **0.28%** |
| 2023 | 242 | 1,079,591 | 1,081,924 | **0.22%** |

The 2015 spike is the July 2015 suspension wave (2015-07-09 had only 1,340 of ~3,600 listed names
trading). The high 2014–2018 level reflects long restructuring suspensions and delisted names whose
bars stop before `out_date`; 2019+ is near-complete at 0.2–0.7%. This is real market suspension
behaviour, not a panel defect, but it is the reason "cash days" cannot be attributed to regime alone.

### 4.5 Checkpoint eligible-history sizes (≥230 valid qfq bars in prior 253 trading days ≈ S0's
`x.notna().sum()<230: continue` history gate; qfq `close>0` counts as valid)

| Checkpoint | Prior-253-day window | any valid bar | **≥230 bars** | ≥253 bars | ≥230 & bar at checkpoint | ≥230 & bar@cp & raw close ≥2 |
|---|---|---|---|---|---|---|
| 2014-07-01 | 2013-06-19..2014-07-01 | 2,520 | **1,966** | 1,218 | 1,898 | 1,887 |
| 2019-01-02 | 2017-12-19..2019-01-02 | 3,571 | **3,010** | 2,469 | 3,007 | 2,977 |
| 2023-12-29 | 2022-12-16..2023-12-29 | 4,573 | **4,359** | 4,114 | 4,353 | 4,294 |

These are an upper bound for S0's feature universe: S0 additionally applies ST/pause exclusion
(unavailable — §7.1/7.2), 60-day turnover ≥ 2e8 (only `amount` proxy available — §7.3), trend and
vol ≤ 0.55 filters (strategy-side, out of scope here).

## 5. Item 4 — Warm-up (253 prior rows)

| Metric | Value |
|---|---|
| Regime start (RUN_TEXT / regime CSV) | 2012-08-03 |
| **Actual price-panel first bar** | **2012-01-04** (manifest date_range starts 2012-01-01) |
| Calendar open days from panel start through 2014-06-30 | **600** |
| Calendar open days from regime start 2012-08-03 through 2014-06-30 | **459** |
| Requirement | ≥ 253 ⇒ **PASS** with large margin (2.4×) |

⇒ Even under the most unfavourable reading of the claim (`panel starts 2012-08-03`), there are 459
trading days before the first backtest day, so `cal[-253]` / 12-1 momentum is well-defined from
2014-07-01 for every fully-listed name. (Precision note: the handoff's "panel starts 2012-08-03" is
really the *regime* start; the price panel starts **2012-01-04**.)

## 6. Item 5 — Manifest and hashes

| Check | Result |
|---|---|
| `qrun_1416\data\meta\manifest.json` sha256 | `9294d2b82b235666871340eb93b51c10fc0a449327be0b1d051c83b89e03b7db` — **matches** engine-recorded value |
| Manifest `dataset` / `version` / `generated_at` | "qrun_1416 A-share daily panel (main board + ChiNext)" / `v1_final` / 2026-10-20T14:11:19 |
| Manifest `counts.files` | **53**; file entries in manifest: **53** |
| Manifest `counts.total_bytes` vs disk | 472,628,818 = **472,628,818** |
| All 53 listed files: bytes + sha256 re-verified | **53/53 match, 0 missing, 0 size mismatch, 0 hash mismatch** |
| Manifest `date_range` | `["2012-01-01", "2026-10-19"]` — as claimed; actual first/last bar 2012-01-04 / 2026-10-19 |
| `os\out\regime_v1.csv` sha256 | `8990cf75782cd51c5c847380228771c0c0730721b0c7fc374af4f68bd69567d5` |
| `meta\calendar.csv` sha256 | `dd422059e3b43aee7a5a03995c658a9a50641ca0148075032dea09bcffd4ec08` |
| `meta\universe.csv` sha256 | `1e3f9b560b1a480baf5bd2d666a7d6950f1feb1b484c29c235780f7a2e09155e` |
| `meta\codes.csv` sha256 | `18e184736d18deba4d39d389b4fb2d661ddb3e089da8788b3f8eda942d063af7` |
| raw/qfq panel sha256 (each year I read) | recorded in `panel_coverage.json → sha256_of_files_read` (27 panel files read, all match manifest entries) |
| `data\panel\bars_qfq_2014.csv.gz` (example) | `47c932ad8a5b7036e09f220354406f48caba9b041e4f9185c21de2279b6c7a8e` |
| `data\panel\bars_2014.csv.gz` (example) | `8ce68d58dc356702a745ec0cfc16042d3cf63266d5d5d29fc62b772acbd41532` |

Raw vs qfq structural alignment: for every year 2013–2023 the raw and qfq files have **identical row
counts, identical code sets and identical date sets** (verified per year; e.g. 2014: 570,193 rows,
2,593 codes, 245 dates in both). Total raw rows 2012–2026: **12,438,891**.

## 7. Item 6 — Residual risks (only items I verified; UNVERIFIED marked)

1. **`isst` is constant 0** — verified over all 12,438,891 raw rows (single value {0}, 0 nulls).
   Real ST/*ST flags are NOT in the panel, while S0 uses the platform's real `is_st` to exclude ST
   names. A local engine cannot reproduce that exclusion from this panel. Impact: the local eligible
   set can be larger/smaller than the platform's on ST days; any local-vs-platform difference in
   selection cannot be blamed on the regime input. (ST approximation by `universe.name`
   containing "ST" is possible but has not been validated here.)
2. **`tradestatus` is constant 1** — verified (single value {1}, 0 nulls). Suspension is encoded
   ONLY by a missing row. An engine must treat a missing bar as non-tradable/suspended; in
   particular an exit target on a suspended holding cannot fill, and `get_price(..., is_paused)`
   semantics on the platform cannot be reproduced from a column.
3. **`turn` is entirely NaN** — verified 0 non-null of 12,438,891 rows (manifest: `turn` unavailable).
   S0's liquidity gate `turnover.reindex(cal[-60:]).mean() >= 2e8` has no local counterpart in the
   same units; `amount` (real Sina CNY turnover value, 100% non-null in spot checks) is the natural
   proxy but the proxy↔platform equivalence is **UNVERIFIED**. Also 36 rows have volume=0 **and**
   amount=0 with valid OHLC (manifest QA; 2019/2020 examples) — these would pass a price filter but
   are untradeable no-trade rows.
4. **qfq is anchored to the panel end, not point-in-time** — verified: qfq/raw = 1.0 on the last bar
   for 4,590/4,595 codes, median 0.476 on 2012-01-04. Consequences: (a) qfq levels are not what a
   PIT investor saw and must never be used as affordability/limit prices (S0 correctly uses raw
   close for `rawclose>=2`); (b) returns/features inside the window are dividend-adjusted
   (total-return-like), so ex-dividend price drops are removed; (c) **5 codes** (`600380`, `601598`,
   `601898`, `603207`, `603699`) do not have qfq/raw = 1 on the final bar (0.9296, 0.9797, 0.9880,
   0.9827, 0.9891 on 2026-10-19), i.e. their factor file still applies an adjustment at/after the
   final bar (most likely an ex-date dated 2026-10-20, the day after the last bar — cause inferred,
   not independently confirmed). The factor is constant across the 2014–2023 backtest window, so
   window returns are unaffected; only the "raw = qfq at panel end" property fails for these 5.
5. **Pre-existing qfq-factor QA (cited, not re-derived):** the Lead's `qa\qfq_jump_classification.json`
   records **938 unmatched qfq ratio jumps**, of which **587 unexplained**, **125 > 1%** magnitude
   (sample `|ratio_chg|` median 0.67%, max 2.35%). These are per-stock, small, and would show up as
   small distortions in individual momentum values. Independent re-derivation of the qfq factor
   construction was **not** performed here → treat as open.
6. **One bogus panel date: 2021-06-27 (Sunday, not in calendar/trading days), 7 rows** — codes
   `300028 300090 300104 300156 300216 300372 300431`, each exactly one row, `pctchg=0`,
   `preclose=close`, prices 0.12–1.48 CNY (delisted ChiNext names). Verified: it is the only
   non-calendar panel date in the whole 2012-2026 panel (`calendar open days without panel rows` =
   only 2026-10-20, i.e. today's incomplete bar). Impact: a **calendar-driven** engine never sees it
   (recommended); a **panel-date-union-driven** engine processes one extra day with 7 ineligible
   low-priced delisted names, plus these 7 codes get a "bar after out_date". Magnitude ≤ 7 rows in
   7.96M (≈0.00009%). Root cause UNVERIFIED (looks like orphaned delisting-day rows from the raw
   source, all stamped with the same non-trading date).
7. **Delisting tails** — 336 delisted names; of the 152 delisted inside the window 144 have their
   last bar *before* `out_date` (median 4 trading days, 24 names > 20 days, max 270). Consistent with
   the suspension=missing-row convention (*ST names often suspend long ahead of delisting), but
   whether every gap is genuine suspension vs. source truncation is **UNVERIFIED** (no independent
   reference available here; both raw and qfq come from the same upstream Sina pull). Engines must
   not assume "tradable until out_date".
8. **`601268` (*ST二重)** is listed 2010-02-02, delisted 2015-05-21, last panel bar 2014-04-28 →
   zero bars in the window; counted in the expected-listed denominator (one of the sources of the
   2014–2015 missing rate). Correct behaviour would be suspension/cash, not a data hole.
9. **Delisted names with no panel rows at all:** manifest QA says `delisted_with_data` = 262 of 336,
   i.e. 74 delisted codes have zero rows. All 152 codes delisted *inside* the window do have data
   (151 with window bars + `601268` with pre-window bars), so the 74 zero-data names are delisted
   outside the window (336 − 152 = 184 outside) and cannot contaminate the window missing-rate
   denominator.
10. **Universe truncation is intentional:** no STAR (688/689) and no BSE codes exist in the panel;
    the panel reproduces exactly the S0 platform symbol filter. Results are therefore "main board +
    ChiNext" only — a scope statement, not a defect.
11. **Cross-source price validation is inherited, not re-run:** the Lead's frozen QA
    (`qa\cross_validation_vs_baostock.csv.gz`, `qa\cross_validation_vs_tencent.csv`) covers subsets:
    baostock 32 codes / 3,735 rows (2015-01-05..2015-06-30): close diff exactly 0; preclose differs
    on 2 rows by 0.2; volume max abs diff 96 shares; amount max abs diff 512 CNY (rounding);
    Tencent 443 codes / 1,478,089 rows: close p99 abs diff 0.0 with 284 rows > 0.02, volume ratio
    ≈100× (units/lots). I summarised these files; I did **not** re-fetch external data.
12. Composite: if the round-3 local engine builds days/targets from the panel or from
    `regime_v1.csv` correctly, the regime input adds **no** fidelity error (0/3,448). Remaining
    local-vs-platform divergence sources are the metadata gaps in (1)(2)(3) and the strategy
    mechanics, not the data date grid.

## 8. Reproduction

```powershell
$env:PYTHONIOENCODING="utf-8"
$PY='C:\Users\汤禹恒\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
& $PY 'winrate_backtest_20261020\round3\audit\regime_audit.py'   # ~3 s   -> regime_compare.csv, regime_audit.json
& $PY 'winrate_backtest_20261020\round3\audit\regime_internal_checks.py' # ~1 s -> regime_internal.json
& $PY 'winrate_backtest_20261020\round3\audit\panel_audit.py'    # ~4 min -> panel_coverage.json (hashes all 53 files)
& $PY 'winrate_backtest_20261020\round3\audit\diag_anomalies.py' # optional, anomaly drill-down -> diag_delisted_tails.csv
```

## 9. Audit artifacts (all under `winrate_backtest_20261020\round3\audit\`)

| File | Content |
|---|---|
| `regime_compare.csv` | 3,448 rows: `date, run_letter, csv_state, csv_letter, match` (all True) |
| `regime_audit.json` | full regime audit incl. 59 in-window state runs, calendar cross-check, token checks, cross-script RUN_TEXT equality |
| `regime_internal.json` | regime CSV internal consistency (`state_prev` vs prior state, monotonic dates, auxiliary trigger flags vs state) |
| `panel_coverage.json` | machine-readable coverage, checkpoints, missing-bar rates, board/prefix, IPO/out, delisting tails, date anomalies, manifest verification, sha256 of every file read, residual-risk facts |
| `DATA_FIDELITY.md` | this report |
| `regime_audit.py`, `regime_internal_checks.py`, `panel_audit.py`, `diag_anomalies.py`, `probe_panel.py` | audit code (read-only against `qrun_1416`) |
| `diag_delisted_tails.csv` | per-delisted-code last bar vs out_date drill-down |
