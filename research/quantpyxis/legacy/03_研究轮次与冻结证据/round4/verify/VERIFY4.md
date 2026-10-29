# Round-4 independent verification — VERIFY4.md

Date: 2026-10-20 (verification window 21:39–21:58 Asia/Shanghai)
Verifier: verifier4 (independent of the round-4 authoring process)
Scope: `winrate_backtest_20261020/round4/` (read-only) against the frozen engine
`qrun_1416` (v1.0.1, data manifest `v1_final`, panel 2012-01-04..2026-10-19).
All recomputation ran on `…\dsh-primary-runtime\dependencies\python\python.exe`
(Python 3.12.14, pandas 3.0.1, numpy 2.3.5). Nothing under `qrun_1416/`,
`round4/out/` or the round-4 source files was modified; all outputs are in
`round4/verify/`.

Audited report versions (SHA256):
`ROUND4.md` = `337900a4810e968300f39af67c71e87ad09eae9a284b8c99332c501935b57a19`
(len 14,596 B, mtime 21:46:52);
`CANDIDATE_A_FREEZE.md` = `4128e5018b9796cbc051d648f8a447a0e38bd821ba55c5e527c6fd8e940c1513`.

Tolerances used: ratios / returns / Sharpe — absolute **1e-6**;
NAV levels — absolute **1e-4 CNY**. Where the report quotes fewer decimals, the
comparison uses half a unit of the last quoted digit (4 dp ⇒ 5e-5, 2 dp ⇒ 5e-3).

---

## Verdict summary

| # | Item | Verdict | One-line evidence |
|---|---|---|---|
| 1 | Recompute all metrics from raw nav CSVs | **PASS** | 1,468 comparisons, **0 deviations** beyond tolerance; 61 nav CSVs internally consistent; every claim-table number reproduces at quoted precision |
| 2 | No look-ahead (structural + empirical) | **PASS** | Published nav reproduced to 5.8e-11; truncating the run at 2019-12-31 and at 2024-09-13 leaves NAV **bit-identical** (max diff 0.0) incl. all 1,087 / 1,814 trades and 14,638 position rows |
| 3 | OOS integrity | **FAIL (as a claim) / PASS (as disclosure)** | Final candidates B/C were **provably** tuned after the 2024-09+ segment was seen (phase-F ranking reversal, `run_phaseD.py` docstring, search space absent from train-only phases). `ROUND4.md` §0/§5/§6.2 **explicitly discloses** this. Additional finding (§3.5): candidate **A's selection also used OOS** — b100_n30_lowvol dominates it on train metrics; §1's "训练期最好的 b100_n30_top15" is factually wrong |
| 4 | Overlay negative results | **PASS** | DD-fuse deadlock reproduced exactly (`latest=0.0`, avg exposure 0.0963, permanent cash from 2020-02-04); crash brake −79.98 pp; **but** `out/phase_stress_summary.csv` contains one wrong/stale stress row (finding F1) |
| 5 | Sanity vs round-3 S0 + day counts | **PASS** | V_base 2014-2023 sub-window = round-3 S0 to ≤3e-15 on all metrics; 2,315 trading days (2014-2023) vs **2,989** (full window) |

**Two report-artifact defects (not affecting the headline conclusions but must be
fixed before the CSVs are cited):** F1 stale stress row in
`out/phase_stress_summary.csv`; F2 wrong drawdown peak date in
`CANDIDATE_A_FREEZE.md`. Details in §Findings.

---

## 1. Numeric recomputation from raw NAV CSVs — PASS

### 1.0 Method

Each `out/full_*_nav.csv` was read directly and the metrics recomputed with the
exact formulas used by the round-4 run scripts (verified line-by-line against
`run_phaseB.py` … `run_phaseH`):

* `total_return = nav_last / 100_000 − 1`
* `max_drawdown = min(nav / cummax(nav) − 1)`; peak = last max before the trough
* `cagr = (nav_last/100_000)^(252/len(nav)) − 1`
* `ann_vol = std(nav.pct_change().fillna(0)) × √252`; `sharpe = mean/std × √252`
* yearly return: `nav[year_end] / prev_first − 1` with `prev_first = 100_000` in 2014
* six user-defined segments: inclusive date bounds, return `last/first − 1`,
  drawdown against the **segment-local** running peak (`PB.seg_stats`)

### 1.1 Claimed candidate set (recomputed exactly from the published nav CSVs)

| Instrument | Days | Total return | Max DD | DD peak → trough | CAGR | Ann vol | Sharpe | Latest seg 2024-09-18..2026-10-19 | Latest DD |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| V_base | 2,989 | +68.6943 % | −14.4823 % | 2025-10-15 → 2026-10-19 | 4.5073 % | 7.4050 % | 0.6325 | −4.1011 % | −14.4823 % |
| b100_n30_top15 | 2,989 | +174.5607 % | −23.9794 % | 2017-11-22 → 2020-03-23 | 8.8883 % | 11.1337 % | 0.8210 | +14.9646 % | −12.8074 % |
| n90_top20_novolcap | 2,989 | +248.1918 % | −30.0192 % | 2018-01-23 → 2020-03-23 | 11.0914 % | 15.3200 % | 0.7638 | +21.9980 % | −19.0404 % |
| n90_top15_novolcap | 2,989 | +285.1088 % | −32.0183 % | 2018-01-23 → 2020-03-23 | 12.0392 % | 15.7882 % | 0.7997 | +21.6244 % | −19.4826 % |
| REF_none_100 | 2,989 | +176.3438 % | −56.1853 % | 2021-02-10 → 2023-11-06 | 8.9477 % | 24.1273 % | 0.4768 | −2.5579 % | −38.4796 % |
| bh_csi300 | 2,989 | +92.3922 % | −46.6954 % | 2015-06-08 → 2016-01-28 | 5.6719 % | 21.4853 % | 0.3657 | +31.6460 % | −17.6619 % |
| bh_chinext | 2,989 | +94.8542 % | −69.6117 % | 2015-06-03 → 2018-10-18 | 5.7853 % | 31.2002 % | 0.3351 | +78.7131 % | −37.1221 % |

All figures match the Claim table handed to this verifier at the quoted
precision (4 dp for returns/DD, 2 dp for Sharpe). Example exact matches:
V_base 0.6869425354662904 / −0.14482345655896744;
b100_n30_top15 1.7456069325168508 / −0.23979364746919452 / 0.821036774237445;
n90_top20_novolcap 2.481918221478975 / −0.3001919975181757 / 0.7637791734611674;
REF_none_100 1.7634384574697894 / −0.5618531309429631;
CSI300 0.9239219982917666 / −0.46695381347864506; ChiNext
0.948542241314176 / −0.6961171173248036.

The six segment returns/DDs and the 13 yearly returns of all seven instruments
also reproduce exactly (V_base bull_2014_2015 +63.2217 %/−5.8481 %, crash
−7.9606 %/−7.9606 %, bear_2018 −0.7819 %/−4.4957 %, bull_2019_2021
+19.2942 %/−12.6545 %, bear_2021_2024 −7.6167 %/−9.4187 %, latest
−4.1011 %/−14.4823 %, …).

### 1.2 Cross-check against every summary artifact (1,468 comparisons, 0 fails)

| Source compared | Rows/tags | Result |
|---|---|---|
| `out/full_summary.csv` (13 phase-B candidates, 12 fields each) | 13 × 12 | 0 deviations |
| `out/full_summary.json` benchmarks block (4 fields) | 2 × 4 | 0 deviations |
| `out/phaseA_summary.csv` / `phaseA2_summary.csv` | 21 / 13 tags | 0 deviations after applying the summaries' own ann_vol/Sharpe convention (note N1) |
| `out/phaseC/D/E/F/G/H_summary.csv` | 11 / 12 / 10 / 5 / 4 / 4 tags | 0 deviations |
| `out/full_segments.csv` (6 segments × 13 tags, ret+dd) | 78 rows | 0 deviations |
| `out/full_yearly.csv` (13 years × 13 tags) | 169 rows | 0 deviations |
| `out/phaseC_segments.csv` / `phaseD_segments.csv` | 66 / 72 rows | 0 deviations |
| `out/ROUND4_FINAL_TABLE.csv` (7 instruments) | 7 | 0 deviations (≤1e-9) |
| `out/ROUND4_SEGMENTS.csv` / `ROUND4_YEARLY.csv` | 42 / 91 | 0 deviations (≤1e-9) |

Observed deviations for all nav-derived metrics are **exactly 0** (the CSVs
carry full float precision and the same formulas are used); NAV level
differences never exceed 1e-4 CNY (the only non-zero values are ~1e-11 CNY
originating in float32/float64 panel arithmetic and disappeared after
re-running; see §2 calibration).

### 1.3 Integrity of the published NAV files

* 61 `out/full_*_nav.csv`: all **2,989 rows**, identical date axis
  **2014-07-01..2026-10-19**, no duplicate dates, monotonically increasing,
  `nav > 0`, and `max|nav − (cash + market_value)| ≤ 1e-6` for every row.
* 34 training-window `out/*_nav.csv`: all **2,315 rows**, exactly
  **2014-07-01..2023-12-29**.
* `*_summary.json` rows equal `*_summary.csv` rows (only fee-field float
  round-trip differences of ≤4e-12 CNY from CSV serialisation).

### 1.4 Notes for item 1

* **N1 (definitional, not a data error):** `phaseA_summary.csv` /
  `phaseA2_summary.csv` took `ann_vol`/`sharpe` from
  `result["metrics"]`, whose daily return is
  `nav.pct_change(fill_method=None).dropna()` (n−1 returns), while
  phaseB…H summaries (and the round-3 `metrics.json`) use
  `nav.pct_change().fillna(0)` (n returns, first day = 0). The NAV paths are
  identical; the two conventions differ by ≤5.4e-5 in vol and ≤2.3e-4 in
  Sharpe (e.g. phaseA V_base: 0.07462225/0.86419328 vs 0.07460617/0.86400614).
  Both are internally consistent; do not compare A/A2 vol/Sharpe with B–H
  vol/Sharpe without noting the convention.
* **N2 (description):** the hand-off brief described candidate **B**
  (`n90_top20_novolcap`) as having "immediate state-change rebalance". The
  code does **not** do that: `overlays.py::on_bar` rebalances off month-end
  only when |Δexposure| > 15 pp, and B=1.00 ↔ N=0.90 is 10 pp (see §2.5:
  90/90 rebalances are `scheduled` month-ends, 0 `overlay` rebalances,
  0 >5 pp jumps across 27 B↔N transitions). Only **E/C risk-off exits are
  immediate** (T+1 open) and re-entry is month-end. The current `ROUND4.md`
  §5 wording — "月末调仓（E/C 当日清仓）" — is accurate; the earlier hand-off
  wording was not. Numbers are unaffected.

---

## 2. No-look-ahead — PASS

### 2.1 Structural review of `variants.py` / `overlays.py`

* Every data access is a `ctx` accessor: `ctx.history_matrix`, `ctx.latest`,
  `ctx.universe`, `ctx.index_history`, plus current-bar state (`ctx.positions`,
  `ctx.nav`, `ctx.cash`, `ctx.date`, `ctx.day`, `ctx.is_rebalance_day`) and
  `ctx.regime`. There is **no** file/CSV/SQL/network access in the strategy
  code and no use of the data bundle directly.
* Engine truncation (verified in `qrun_1416/engine/core.py`):
  `history_matrix` slices `lo = max(0, i-window+1) .. i`; `latest` returns row
  `i`; `index_history` returns the index series up to today;
  `universe(as_of=None)` uses today's row; `ctx.regime` is a dict lookup for
  **today's** date only. All reads are rows ≤ T.
* Signal/execution ordering: strategy code only acts through
  `ctx.set_targets`/`ctx.sell` inside `on_bar` / `on_rebalance`. The engine
  executes pending orders at the day's **open** (step 4) and only afterwards
  calls `on_bar`/`on_rebalance` (step 6); `exec_timing="next_open"` is the
  frozen default. A signal formed at D's close can therefore only fill at
  D+1's open — no intraday/tomorrow data accessor exists in the API.
* Regime labels: round 4 passes `qrun_1416/os/out/regime_v1.csv` (frozen,
  file predates round 4: mtime 2026-10-20 13:55:47, not rewritten by any
  round-4 script). Its own docstring/`regime_v1.py` computes the label for
  date T on **T's close**, and the portfolio action executes at T+1's open —
  consistent with the frozen platform rules. The file has 3,448 rows
  (2012-08-03..2026-10-19): NEUTRAL 1,601 / BEAR 775 / BULL 550 / CRASH 522.
* Static write-target check of all round-4 scripts: every write goes to
  `round4/out/…`; nothing writes to `qrun_1416/` or rewrites source.
* Overlays: dd/peak use `ctx.nav` of today; crash brake uses today's index
  close/5-day return; both only reduce exposure and are executed T+1 open.

### 2.2 Calibration of the empirical harness

Re-ran the phase-G final candidate exactly as `run_phaseG.py` does
(`OverlayVariant`, state_mode=frozen, top_n=20, buffer=20, max_vol=None,
B=1.00/N=0.90, 10 bp slippage, 2.5 bp commission, 100k, monthly, warmup 340):

* vs `out/full_n90_top20_novolcap_nav.csv`: max |Δ| per column
  **nav 5.8e-11 CNY**, cash 2.9e-11, market_value 5.8e-11, exposure 1.1e-16,
  ret 9.9e-17, n_positions 0, fees 5.7e-14, turnover 2.2e-16.
* metrics: total +248.19182215 %, maxDD −30.01919975 %, Sharpe 0.763779 — all
  equal to `phaseG_summary.csv` to 1e-9.

The phase-H candidate C has **no saved run script** (`run_phaseH.py` does not
exist). Its config was recovered from the phase-H script spec/tag
(`frozen, top_n=15, buffer=20, max_vol=None, B=1.00/N=0.90`) and reproduced:
vs `out/full_n90_top15_novolcap_nav.csv` max |Δ| **5.8e-11 CNY**; total
+285.10882308 %, maxDD −32.01830384 %, Sharpe 0.799677 — equal to
`phaseH_summary.csv`. So the *outputs* are verified; the *generator script* is
missing (see §6 evidence gaps).

### 2.3 Empirical truncation test — data-end invariance

Same config, run to a truncated end date; compare the common prefix with the
full run. If any signal read future data, the prefix would change.

| Truncated end | Common rows | NAV/cash/market_value/exposure/turnover max |Δ| | Trades through cutoff | Position-snapshot rows | Verdict |
|---|---:|---:|---|---:|---|
| 2019-12-31 | 1,345 | **0.0** (all 12 columns) | 1,087 vs 1,087, identical | 14,638 vs 14,638, identical | **PASS** |
| 2024-09-13 | 2,487 | **0.0** (all 12 columns) | 1,814 vs 1,814, identical | — | **PASS** |

The second cutoff is the start of the "latest segment": the full run's entire
2021-02..2024-09 and 2024-09..2026-10 path is unaffected by knowledge of the
future rows. Also `phaseA`'s separate 2014-2023 V_base run and the
full-run V_base prefix are **bit-identical** (2,315/2,315 rows), i.e. the
engine + strategy are end-date invariant (no look-ahead through the data end).

### 2.4 Fill timing

* 2,493 stock trades of the full n90_top20 run: expected price
  `round(open × (1 ± 10 bp), 2)`. **99.96 %** are at the trade-date open
  within 20 bp; **0 unexplained**; 1 trade has an unknown/no-open code entry
  (delisted-panel residue, not a timing issue).
* Refined on the n90_top15 run (1,933 trades): 99.90 % exactly
  `round(open×(1±10bp),2)`, 0.05 % (1) limit-price-clamped within 2 % of the
  open, **0 unexplained**. No fill uses the close or a later bar.
* First trade 2014-09-01 (first month-end signal after the 2014-07-01 start),
  last trade 2026-10-19.

### 2.5 Schedule / state mechanics (n90_top15 candidate C, full window)

* 90 rebalance signals, **all** `reason="scheduled"`, **all** on the last
  trading day of their month; 0 `overlay`/off-month rebalances.
* 27 B↔N regime transitions: **0** transitions show a >5 pp exposure jump on a
  non-rebalance day (B↔N is 10 pp, below the 15 pp overlay arm) — i.e. no
  immediate state-change rebalance for B/N.
* 12 B/N→E/C risk-off events while holding: 9 are fully flat at the next
  close; 3 retain a small residue (4.1 %, 6.3 %, 5.4 %) which is explained by
  `target_blocked` carried sells — suspended (000518 2015-06, 600277 2019-05,
  601198 2026-09) and limit-down (600277 2019-05-21/22) events, exactly the
  documented engine carry rules. No timing anomaly.

---

## 3. OOS integrity — FAIL as a claim, PASS as a disclosure

### 3.1 "Final candidates were selected only from 2014-2023 evidence" — **FAIL (provable contamination)**

File order and code prove that the final candidates (B and C) were iterated
**after** the 2024-09+ segment had been seen:

| Time | Artifact | Meaning |
|---|---|---|
| 21:24:47–21:28:36 | `run_phaseA.py`, `run_phaseA2.py`, phaseA/A2 summaries | train-only search (2014-2023) |
| 21:28:49–21:30:46 | `run_phaseB.py`, `full_summary.csv` | **first** full-window run; 2024-09+ seen for the first time |
| 21:31:26–21:32:37 | `overlays.py`, `run_phaseC.py`, phaseC | candidate/fuse runs *after* OOS seen |
| 21:32:52 | `run_phaseD.py` | docstring: **"Motivated by the 2024-09+ miss: the frozen machine stayed at N (30%) through most of the 2024-2025 bull and did not re-enter between month-ends."** |
| 21:35–21:39 | `run_phaseE/F/G/H` | final width/score/cost/exposure micro-tuning, all full-window |

Decisive quantitative evidence — **phase-F selection reversal**. Phase F ran
five full-window candidates and carried `n100_top20_novolcap` forward; on the
train window alone that is *not* the winner:

| phase-F candidate | train 2014-2023 total | train DD | train Sharpe | full-window total | full DD | full Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| n100_top15 | **+236.68 %** | −32.71 % | **0.889** | +259.18 % | −32.71 % | 0.737 |
| n100_top20 | +174.21 % | −28.66 % | 0.780 | +180.01 % | −30.85 % | 0.630 |
| n100_top25 | +163.25 % | −24.64 % | 0.762 | +172.27 % | −30.33 % | 0.628 |
| **n100_top20_novolcap (chosen)** | +201.73 % | −31.68 % | 0.828 | **+261.82 %** | −31.68 % | 0.744 |
| n100_top20_lowvol | +163.86 % | −34.09 % | 0.805 | +124.56 % | −34.09 % | 0.552 |

On 2014-2023 data the best return and best Sharpe are `n100_top15`; the
candidate carried to phase G/H is the one that wins **only when 2024-09+ is
included**. That is tuning on the OOS segment.

Additional evidence:

* The train-only search space (`run_phaseA/2.py`) never tested N > 0.30 at
  top_n > 15, nor "no 55 % stock-vol cap" with N ≥ 0.30 — the exact axes of
  the final B/C candidates. Those axes appear for the first time in phase E/F/G/H.
* Each phase-E/F/G/H script prints the `2024-09+` return and `oosDD` column in
  its selection table (`run_phaseD.py` lines 115-118, `run_phaseE.py` lines
  81-84, `run_phaseF.py` lines 74-77, `run_phaseG.py` lines 76-79), so the
  numbers the author used to rank candidates included the OOS segment.
* Phase G then chose N = 0.90 (vs 0.80/1.00) and phase H chose top_n = 15
  (vs 20) using that same full-window table. (These two remain winners on
  train-only metrics as well, so the *specific* contamination is proven for
  phase F; phases G/H are contaminated by construction because their search
  space only exists post-OOS.)
* Caveat: `run_phaseH.py` was never saved, so the exact phase-H code cannot be
  inspected; its outputs are verified but the generator is missing.

### 3.2 "Could 2024-09+ have influenced a rule before it was reported?" — **YES, and the report says so**

`ROUND4.md` §0: "**Phase C 之后的调整是在看到该 OOS 结果之后做的，因此不再具备
样本外性质**"; §5: "B/C 两档的 N=90%、去波动上限，是在看到 2024-09 以后结果之后才
定下来的（Phase C-H），其 +22% 不是样本外证据"; §6.2 repeats it. This matches
the code/timestamps exactly. **Disclosure = PASS**; the report is honest.

Consequently, any use of **+22.00 % / +21.62 %** (B/C latest segment) or
"+248 %/+285 % full-window" as out-of-sample confirmation would be invalid.
The report does not make that claim; the verdict here is that it must stay
that way in any downstream/CEO summary.

### 3.3 The "+14.96 %" observation is a valid first-look OOS result — with a selection caveat

All 13 phase-B candidates were compared train-run-NAV vs full-run 2014-2023
prefix: **13/13 bit-identical** (2,315/2,315 rows each), i.e. phase B changed
no configuration before first seeing 2024-09+ data. The **run** `b100_n30_top15`
was pre-specified from phase A2 (21:27:20, before phase B ran), so its
2024-09-18..2026-10-19 return **+14.9646 %** (CSI300 +31.65 %, ChiNext
+78.71 % in the same window) is a valid first-look OOS observation for a
pre-registered configuration — and it is a **loss vs both benchmarks**, as the
report states.

However, `ROUND4.md` §1 describes it as "**训练期最好的** b100_n30_top15".
That is factually wrong: among the 13 phase-B candidates, the train-window
best "line-in" candidate is **b100_n30_lowvol** (+156.05 % total, Sharpe 1.01,
DD −24.65 %), which beats b100_n30_top15 (+139.17 %, Sharpe 0.91,
DD −23.98 %); `ROUND4.md` §2 itself lists lowvol as +156.05 %/1.01, and §4.5
rejects lowvol partly because "**2024-09 以后没有优势，全窗口收益不及 base
分数**". See §3.5 — candidate A's designation therefore also used OOS data.

### 3.4 What the verdict means

* If item 3 is read as "were B/C selected using only 2014-2023 evidence?" →
  **FAIL — provably not.**
* If read as "does the round-4 report claim clean OOS for B/C?" → **PASS** —
  it explicitly disclaims it.
* Read as "is candidate A a clean OOS pick?" → **QUALIFIED FAIL**: the run is
  pre-registered and its +14.96 % is a valid OOS observation, but A's
  **selection as main candidate** used OOS data (§3.5), and §1's
  "训练期最好" characterisation is inaccurate.
* The only first-look OOS number that may be quoted is A's **+14.96 %**, with
  the §3.5 caveat.

### 3.5 Candidate A's selection also used OOS information — additional finding

`ROUND4.md` §4.5 justifies dropping the low-vol score with "2024-09 以后没有
优势，全窗口收益不及 base 分数" — i.e. **post-OOS** performance. The numbers
make the same point: on the train window (2014-07-01..2023-12-29, the declared
selection window), the low-vol candidate dominates the chosen candidate A:

| Phase-B candidate (all pre-registered from phase A/A2) | Train total | Train DD | Train Sharpe | Full total | Full DD | OOS 2024-09+ |
|---|---:|---:|---:|---:|---:|---:|
| b100_n30_lowvol | **+156.05 %** | −24.65 % | **1.014** | +166.82 % | −24.65 % | +4.28 % |
| **b100_n30_top15 (= A)** | +139.17 % | −23.98 % | 0.912 | **+174.56 %** | −23.98 % | **+14.96 %** |

A train-only selection rule (higher return/Sharpe at essentially the same
DD, both inside the 30 % line) would have picked **b100_n30_lowvol**, not
b100_n30_top15. Candidate A was chosen after the OOS segment was seen, so at
the *selection* level A is post-hoc too; §1's "训练期最好的 b100_n30_top15"
is wrong. The phase-B **batch** remains a valid one-step OOS test (the 13
configs were fixed before the full-window run); the **crown** is not.

Full train/DD/Sharpe ranking of the 13 phase-B candidates (from the same
published NAVs) — `out/`-derived, see `v6_train_rank.log`:

| rank (train total) | tag | train total | train DD | train Sharpe | OOS 2024-09+ |
|---:|---|---:|---:|---:|---:|
| 1 | REF_none_100 (reference) | +163.50 % | −56.19 % | 0.551 | −2.56 % |
| 2 | b100_n30_lowvol | +156.05 % | −24.65 % | 1.014 | +4.28 % |
| 3 | REF_ma200_100 (reference) | +142.50 % | −45.10 % | 0.614 | −12.27 % |
| 4 | b100_n30_top15 | +139.17 % | −23.98 % | 0.912 | +14.96 % |
| 5 | b100_n30 | +136.74 % | −21.76 % | 0.878 | +0.61 % |

---

## 4. Overlay negative results — PASS (plus artifact defect F1)

### 4.1 Account drawdown fuse `top15_fuse_10_15` (re-run once)

Config exactly as `run_phaseC.py` (`T15` = frozen, top15, B=1.00/N=0.30,
dd_fuse=(0.10, 0.15)). Re-run vs published
`out/full_top15_fuse_10_15_nav.csv`: max |Δnav| = **5.82e-11 CNY**.

| Metric | Value |
|---|---:|
| total return / maxDD / Sharpe | +60.8506 % / −15.9417 % / 0.5874 |
| avg exposure / all-cash days | **0.096300** / 2,175 |
| latest segment 2024-09-18..2026-10-19 ret | **0.0 exactly** |
| latest segment maxDD | **0.0 exactly** |
| last day on which NAV changed at all | **2020-02-04** |
| distinct NAV levels after 2020-02-04 | **1** (permanent cash) |
| exposure over the last 502 days | mean 0.000000, max 0.000000 |
| account DD from peak after 2020-02-04 | max **15.94 %** — arms the 15 % flat rule forever |

So the reported deadlock mechanism is confirmed: the fuse flattens the
account, the NAV never rises again, the drawdown-from-peak therefore never
falls below 15 %, and there is no release condition → permanent cash. The
report's "avg exposure ~0.10 / latest 0" is exact.

### 4.2 Crash brake hurts — confirmed

`top15_crash` (+index ≤ −5 % or 5-day ≤ −8 % → flat 10 sessions) vs the same
strategy without the brake (`b100_n30_top15`):

| Variant | Total | MaxDD | Sharpe | Latest segment |
|---|---:|---:|---:|---:|
| top15_crash | +94.5798 % | −26.4344 % | 0.5998 | −6.9976 % |
| b100_n30_top15 (no brake) | +174.5607 % | −23.9794 % | 0.8210 | +14.9646 % |
| Δ | **−79.98 pp** | −2.46 pp worse | −0.22 | −21.96 pp |

Confirms `ROUND4.md` §4.4. Also verified: fast re-entry (`top15_fast`
+110.39 %/−26.83 %) is worse than the monthly version (+174.56 %/−23.98 %) —
§4.2. Top5 concentration (+42.64 %/−17.57 % train) — §4.5.

### 4.3 Cost-stress numbers: report correct, one summary CSV wrong — **F1**

Re-ran both final candidates with 20 bp slippage + 5 bp commission:

| Run | Total | MaxDD | Sharpe | Fees (CNY) |
|---|---:|---:|---:|---:|
| b100_n30_top15 stress (fresh run) | **+149.6728 %** | **−25.2261 %** | **0.746868** | **14,351.62** |
| `out/phase_stress_b100n30_top15.csv` x2cost | +149.6728 % | −25.2261 % | 0.746868 | 14,351.62 |
| `out/phase_stress_summary.csv` row `stress_b100n30_top15` | **+93.8588 %** | **−29.3642 %** | **0.565323** | **15,839.78** |
| n90_top20_novolcap stress (fresh run) | +214.2199 % | −30.7031 % | 0.706526 | 25,332.77 |
| `out/phase_stress_summary.csv` row `stress_n90_top20_novolcap` | +214.2199 % | −30.7031 % | 0.706526 | 25,332.77 |

`ROUND4.md` §4.6 and `CANDIDATE_A_FREEZE.md` quote the **correct** values
(+149.67 %/−25.23 %/0.75, fees 14,352). The `phase_stress_summary.csv` row is
wrong/stale and was not reproducible with the stated config under any of the
configurations this verifier tried (top10, V_base, novolcap, buffer5, b100_n60
variants — none match). The origin of that row is **UNVERIFIED**; the file
must be fixed or the row deleted before it is cited.

---

## 5. Sanity vs round-3 S0 + day counts — PASS

V_base = round-3 S0/R2 rules. Computed on the **2014-07-01..2023-12-29
sub-window of the full-window run** (`out/full_V_base_nav.csv`):

| Metric | This recomputation | round-3 `out/metrics.json` S0 | Δ |
|---|---:|---:|---:|
| total_return | 0.7621051226621636 | 0.7621051226621636 | 0.0 |
| max_drawdown | −0.12654520858479956 | −0.12654520858479956 | 0.0 |
| cagr | 0.06360866020136124 | 0.06360866020136124 | 0.0 |
| ann_vol | 0.07460617194730887 | 0.07460617194730894 | 6.9e-17 |
| sharpe | 0.864006137492913 | 0.8640061374929157 | 2.7e-15 |
| avg_exposure | 0.16556292357183008 | 0.1655629235718301 | 2.8e-17 |
| trading_days | **2,315** | **2,315** | 0 |

Additionally the 2,315-row sub-window of the full run is bit-identical to
`out/V_base_nav.csv` (the separate phase-A 2014-2023 run) — end-date
invariance again.

**Exact trading-day counts:** 2,315 for 2014-07-01..2023-12-29 (matches the
flag in the brief) and **2,989** for the full window 2014-07-01..2026-10-19
(61 published nav CSVs, all 2,989 rows).

---

## 6. Report-number consistency (469 checks; 467 pass)

Method: every numeric table in `ROUND4.md` and `CANDIDATE_A_FREEZE.md` was
compared with values recomputed from the published nav CSVs at the precision
the report quotes (± half unit of the last digit), and the consolidated
`out/ROUND4_*` tables at 1e-9.

* §1 first-OOS table (9 instruments × 5 fields): **45/45**; plus 1 phase-B
  train→full config check: 1/1.
* §2 train-window table (15 variants × 3 fields): **45/45**.
* §3 six-segment table (5 × 6): **30/30**; yearly table (5 × 13): **65/65**.
* §4.1 DD-budget statement: 1/1; §4.2 fast re-entry: 2/2; §4.3 fuse: 2/2;
  §4.4 crash brake: 2/2; §4.6 cost stress: **9/9 numeric** + the
  internal-consistency check against `phase_stress_summary.csv` fails (**F1**).
* §5 final-candidate table (3 × 4): **12/12**.
* `ROUND4_FINAL_TABLE.csv` (63), `ROUND4_SEGMENTS.csv` (84),
  `ROUND4_YEARLY.csv` (91): **238/238** at 1e-9.
* `CANDIDATE_A_FREEZE.md` §2: **8/9**; fail = **F2**. §1 rule description is
  consistent with the code (month-end rebalance, E/C same-day liquidation).
* §7 SHA256 list: **7/7 exact**; `regime_v1.csv` SHA256 =
  `8990cf75782cd51c5c847380228771c0c0730721b0c7fc374af4f68bd69567d5`
  (= `8990cf75…67d5` as claimed) with 3,448 rows as claimed.
* Run counts: A 21 / A2 13 / B 13 (+2 benchmarks) / C 11 / D 12 / E 10 / F 5 /
  G 4 / H 4 = **93 strategy runs** (+2 benchmarks, +2 stress) — consistent
  with "93 条本地回测".

### Findings

**F1 — MAJOR (data artifact):** `out/phase_stress_summary.csv`, row
`stress_b100n30_top15`, reports +93.8588 % / −29.3642 % / 0.565323 / fees
15,839.78. This contradicts (a) the reproducible value for the stated
config (+149.6728 % / −25.2261 % / 0.746868 / fees 14,351.62), (b) its
companion `out/phase_stress_b100n30_top15.csv` (x2cost row), and (c) the
report §4.6 / freeze-file numbers (which are correct). Origin of the stale
row: **UNVERIFIED** (no matching config found among tested variants). Fix:
recompute or delete the row; do not cite this CSV as-is.

**F2 — MINOR (documentation):** `CANDIDATE_A_FREEZE.md` §2 says the max
drawdown is "峰 2020-03-23 前高，谷 2020-03-23" — impossible (peak = trough).
Actual for b100_n30_top15: peak **2017-11-22**, trough **2020-03-23**,
DD −23.9794 %. The `ROUND4_FINAL_TABLE.csv` `dd_trough` column is correct.

**F3 — DESCRIPTION (already corrected in current report):** the hand-off
brief described candidate B as "immediate state-change rebalance"; the code
performs month-end rebalance only (B↔N = 10 pp < 15 pp overlay arm) with
immediate E/C liquidation. Current `ROUND4.md` §5 and
`CANDIDATE_A_FREEZE.md` §1 are correct; no number changes.

**F4 — EVIDENCE GAP (not a number failure):** `run_phaseH.py` was never saved,
so candidate C's exact generator code cannot be inspected; its config was
recovered and its nav reproduced to 5.8e-11, so the outputs are verified but
the provenance audit is weaker. Likewise the cost-stress runner was not saved
(which is why F1 could not be traced). Also the freeze-file FIFO win-rate /
profit-factor claim ("约 53 % / 约 1.9") was not recomputed because round-4
does not persist trades; that claim is **UNVERIFIED**.

**F5 — OOS (disclosed, procedural):** candidates B and C are in-sample on the
2024-09+ segment (provable; see §3.1). The report discloses it (§0/§5/§6.2);
this is a finding only if B/C are ever presented as OOS evidence.

**F6 — OOS at the candidate-A level (material, not disclosed):** among the 13
pre-registered phase-B candidates the train-window best line-in candidate is
`b100_n30_lowvol` (+156.05 %, DD −24.65 %, Sharpe 1.01) — better than the
chosen A `b100_n30_top15` (+139.17 %, DD −23.98 %, Sharpe 0.91) on the
declared 2014-2023 selection window. `ROUND4.md` §4.5 rejects lowvol using
post-2024-09 evidence ("2024-09 以后没有优势"), so **A's designation as main
candidate also used OOS data**, and §1's phrase "训练期最好的 b100_n30_top15"
is factually incorrect (its own §2 table contradicts it). The **+14.96 %**
number remains a valid first-look OOS observation for a pre-registered run,
but candidate A must not be described as a train-only-selected, clean-OOS
pick; at minimum §1/§5 need re-wording and the "only clean OOS evidence"
statement needs the §3.5 qualification.

---

## 7. Verdict table (final)

| Item | Verdict | Must-fix / caveat |
|---|---|---|
| 1. Raw-NAV recomputation & summary comparison | **PASS** | none for numbers; note N1 (vol/Sharpe convention between A/A2 and B–H) |
| 2. No look-ahead | **PASS** | none; T+1 open fills verified 99.9 %+ by price, truncation invariance exact |
| 3. OOS integrity | **FAIL as "selected on train only"; PASS as disclosure** | never quote B/C latest-segment +22.00 %/+21.62 % as OOS; A's +14.96 % is a valid first-look OOS observation, but A's selection also used OOS (F6) — qualify §1/§5 |
| 4. Overlay negative results | **PASS (deadlock exact, crash brake hurts)** | F1: fix `phase_stress_summary.csv`; report values are correct |
| 5. V_base vs round-3 S0 + day counts | **PASS** | none (2,315 / 2,989 days) |
| Report numeric consistency | 467/469 | F1, F2 |
| Hashes / run counts / data artefacts | **PASS** | all 7 script hashes + regime hash exact; 61 nav CSVs sane |

**Bottom line:** every number in the round-4 report that this verifier could
recompute is correct (headline returns/DD/Sharpe/segments/yearly, overlays,
cost stress), and the no-look-ahead property holds exactly under truncation.
The artefact defects are an inconsistent stress-summary CSV row (F1) and a
wrong DD peak date in the freeze file (F2). The substantive OOS issue is
broader than the report's own disclosure: B/C are in-sample (disclosed), and
**A's selection as main candidate also used post-OOS evidence** (F6, not
disclosed) — the +14.96 % run is valid first-look OOS data, but the "train-only
selected clean OOS candidate" framing is not supported by the numbers.

---

## 8. Reproduction: scripts and logs in `round4/verify/`

```
<py> = C:\Users\汤禹恒\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
```

| Script | Covers | Output | Runtime |
|---|---|---|---|
| `v1_recompute.py` | item 1 + item 5: recompute from all 61 full + 34 train nav CSVs; compare to every summary/segment/yearly CSV and the claim table | `v1_recompute.log`, `v1_recompute.json` | ~10 s |
| `v2_lookahead.py` | item 2: structural + calibration + truncation (2019-12-31, 2024-09-13) + fill timing | `v2_lookahead.log`, `v2_lookahead.json` | ~40 s |
| `v2b_state_timing.py` | item 2b: phase-H candidate calibration, refined fill timing, rebalance-schedule/state-change mechanics | `v2b_state_timing.log`, `.json` | ~15 s |
| `v2b_riskoff_probe.log` | item 2b: `target_blocked` carried-sell evidence for the 3 residual risk-off holdings | log | ~10 s |
| `v3_oos_report.py` | item 3 + report consistency: timeline, train→full config check, train-vs-full ranking, 469 report-number checks, hashes, run counts | `v3_oos_report.log`, `.json` | ~5 s |
| `v4_overlay.py` | item 4: DD-fuse re-run, crash brake, both cost-stress re-runs | `v4_overlay.log`, `.json` | ~60 s |
| `v4b_stress_probe*.log` | F1 origin probes (top10, V_base, novolcap, buffer5, b100_n60) — none match the stale row | logs | ~90 s total |
| `v5_integrity.py` | NAV-file integrity, JSON=CSV agreement, regime file, write-target static check | `v5_integrity.log` | ~5 s |
| `v6_train_rank.log` | train-window ranking of the 13 phase-B candidates (F6 evidence) | log | ~2 s |
| `*.stdout.log` | raw stdout of each run (including stderr) | — | — |

EOF

