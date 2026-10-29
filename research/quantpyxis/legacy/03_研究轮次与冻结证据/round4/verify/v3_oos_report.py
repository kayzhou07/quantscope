# -*- coding: utf-8 -*-
"""Round-4 verification item 3 (OOS integrity) + full report-number consistency audit.

Also cross-checks out/ROUND4_FINAL_TABLE.csv, ROUND4_SEGMENTS.csv, ROUND4_YEARLY.csv,
ROUND4.md numeric tables, CANDIDATE_A_FREEZE.md, and the §7 SHA256 list.
Read-only over round4/ and round4/out; writes verify/ only.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R4 = HERE.parent
WS = R4.parent.parent
OUT = R4 / "out"
VER = HERE
CAP = 100_000.0
TRAIN_END = "2023-12-29"
LATEST = ("2024-09-18", "2026-10-19")
SEGS = [
    ("bull_2014_2015", "2014-07-01", "2015-06-12"),
    ("crash_2015_2016", "2015-06-15", "2016-01-28"),
    ("bear_2018", "2018-01-02", "2018-12-31"),
    ("bull_2019_2021", "2019-01-02", "2021-02-10"),
    ("bear_2021_2024", "2021-02-18", "2024-09-13"),
    ("bull_2024_now", "2024-09-18", "2026-10-19"),
]


def load_nav(tag, full=True):
    p = OUT / (f"full_{tag}_nav.csv" if full else f"{tag}_nav.csv")
    df = pd.read_csv(p, dtype={"date": str})
    df.index = df["date"].astype(str)
    return df


def mstats(n: pd.Series) -> dict:
    r = n.pct_change().fillna(0.0)
    dd = n / n.cummax() - 1.0
    return dict(total_return=float(n.iloc[-1] / CAP - 1),
                max_drawdown=float(dd.min()),
                dd_trough_date=str(dd.idxmin()),
                dd_peak_date=str(n.loc[:dd.idxmin()].idxmax()),
                cagr=float((n.iloc[-1] / CAP) ** (252.0 / len(n)) - 1),
                ann_vol=float(r.std() * math.sqrt(252.0)),
                sharpe=float(r.mean() / r.std() * math.sqrt(252.0)),
                days=int(len(n)))


def seg(n: pd.Series, s: str, e: str) -> dict:
    sub = n.loc[(n.index >= s) & (n.index <= e)]
    if len(sub) < 2:
        return {}
    return dict(ret=float(sub.iloc[-1] / sub.iloc[0] - 1),
                dd=float((sub / sub.cummax() - 1).min()))


def yearly(n: pd.Series) -> dict:
    d = pd.DataFrame({"nav": n})
    d["prev"] = d["nav"].shift(1)
    d.iloc[0, d.columns.get_loc("prev")] = CAP
    d["year"] = [x[:4] for x in d.index]
    return {y: float(g["nav"].iloc[-1] / g["prev"].iloc[0] - 1) for y, g in d.groupby("year")}


class Checker:
    def __init__(self):
        self.rows = []
        self.fails = []

    def chk(self, scope, item, rec, rep, tol):
        diff = None if (rec is None or rep is None) else abs(float(rec) - float(rep))
        ok = diff is not None and diff <= tol
        row = dict(scope=scope, item=item, recomputed=rec, reported=rep,
                   abs_diff=diff, tol=tol, ok=bool(ok))
        self.rows.append(row)
        if not ok:
            self.fails.append(row)
        return ok

    def pp(self, scope, item, rec_frac, rep_pp, nd=2):
        """report value is in percentage points with nd decimals; rec is a fraction"""
        tol = 0.5 * 10 ** (-nd)
        return self.chk(scope, item, None if rec_frac is None else rec_frac * 100.0,
                        rep_pp, tol)

    def sh(self, scope, item, rec, rep, nd=2):
        return self.chk(scope, item, rec, rep, 0.5 * 10 ** (-nd))


def main():
    log = []

    def P(*a):
        s = " ".join(str(x) for x in a)
        log.append(s)
        print(s)

    C = Checker()

    P("=" * 100)
    P("ITEM 3: OOS integrity audit (selection evidence) + report number consistency")
    P("=" * 100)

    # ---------------------------------------------------------- evidence files
    P("\n[A] FILE TIMELINE (generation order; mtime)")
    files = ["variants.py", "run_phaseA.py", "run_phaseA2.py", "run_phaseB.py", "overlays.py",
             "run_phaseC.py", "run_phaseD.py", "run_phaseE.py", "run_phaseF.py",
             "run_phaseG.py"]
    for f in files:
        st = (R4 / f).stat()
        P(f"  {datetime.fromtimestamp(st.st_mtime):%H:%M:%S}  {f}")
    for f in ["full_summary.csv", "phaseA_summary.csv", "phaseA2_summary.csv",
              "phaseC_summary.csv", "phaseD_summary.csv", "phaseE_summary.csv",
              "phaseF_summary.csv", "phaseG_summary.csv", "phaseH_summary.csv",
              "phase_stress_summary.csv", "ROUND4_FINAL_TABLE.csv"]:
        p = OUT / f
        st = p.stat()
        P(f"  {datetime.fromtimestamp(st.st_mtime):%H:%M:%S}  out/{f}")
    for f in ["ROUND4.md", "CANDIDATE_A_FREEZE.md"]:
        p = R4 / f
        if p.exists():
            st = p.stat()
            P(f"  {datetime.fromtimestamp(st.st_mtime):%H:%M:%S}  {f}")

    # ------------------------------------------------- train->full config check
    P("\n[B] PHASE-B CANDIDATES CARRY TRAIN-PHASE CONFIGS UNCHANGED?")
    P("    (compare each 2014-2023 train run NAV prefix with the full-window run prefix)")
    MAP = {
        "V_base": "V_base", "b80_n30": "exp_b80_n30", "b100_n25": "exp_b100_n25",
        "b100_n30": "exp_b100_n30", "b100_n30_vt20": "b100n30_vt20",
        "b100_n30_vt15": "b100n30_vt15", "b80_n30_vt18": "b80n30_vt18",
        "b100_n30_lowvol": "b100n30_lowvol", "b100_n30_top15": "b100n30_top15",
        "b100_n30_capma200": "b100n30_capma200", "base_vt18": "base_vt18",
        "REF_none_100": "state_none_100", "REF_ma200_100": "state_ma200_100",
    }
    all_same = True
    for full_tag, train_tag in MAP.items():
        tr = load_nav(train_tag, full=False)
        fu = load_nav(full_tag, full=True)
        pre = fu[fu.index <= TRAIN_END]
        same = len(tr) == len(pre) and bool(np.allclose(
            tr["nav"].astype(float).to_numpy(), pre["nav"].astype(float).to_numpy(),
            rtol=0, atol=1e-9))
        all_same &= same
        P(f"  {full_tag:20s} train={len(tr):4d} full-prefix={len(pre):4d} identical={same}")
    P(f"  ALL phase-B candidates unchanged from train-phase config: {all_same}")
    C.chk("phaseB", "train configs unchanged", 1.0 if all_same else 0.0, 1.0, 0)

    # --------------------------------------- train-only search space of A/A2
    P("\n[C] WAS THE FINAL CANDIDATE SET PART OF THE 2014-2023 SEARCH SPACE?")
    P("  phaseA specs: exposure N in {0.25,0.30,0.50}, top_n in {5,10,15}, buffer {1,5,20},")
    P("      max_vol {0.55,None}, score {base,equal,lowvol}, afford {drop,backfill,fees},")
    P("      state {frozen,none,ma200,dual_ma} (run_phaseA.py lines 35-58)")
    P("  phaseA2 specs: index_cap ma200, vol_target .15/.18/.20, novolcap, lowvol, buffer5,")
    P("      backfill, top15 (run_phaseA2.py lines 24-38)")
    P("  => the train-only search never tested N=0.90, B=1.0 with N>0.30 at top_n>15,")
    P("     nor 'no 55% stock-vol cap' combined with N>=0.30/top_n>=15.")
    P("  phaseF/G/H (final candidates) introduced exactly those axes, AFTER phaseB output:")
    pf = pd.read_csv(OUT / "phaseF_summary.csv")
    pg = pd.read_csv(OUT / "phaseG_summary.csv")
    ph = pd.read_csv(OUT / "phaseH_summary.csv")
    P(f"  phaseF tags: {list(pf['tag'])}")
    P(f"  phaseG tags: {list(pg['tag'])}")
    P(f"  phaseH tags: {list(ph['tag'])}")
    P("  all three phases ran the FULL window and their summaries/prints include the")
    P("  2024-09+ segment and 'oosDD' columns -> the selection table contained OOS data.")

    # --------------------------------- train vs full ranking for final phases
    P("\n[D] TRAIN-ONLY vs FULL-WINDOW RANKING OF THE FINAL-PHASE CANDIDATES")
    P("    (train = 2014-07-01..2023-12-29 NAV prefix of the same full-window run)")
    for phase, tags, chosen in (
            ("phaseF", ["n100_top15", "n100_top20", "n100_top25",
                        "n100_top20_novolcap", "n100_top20_lowvol"], "n100_top20_novolcap"),
            ("phaseG", ["n80_top20_novolcap", "n80_top20",
                        "n100_top20_novolcap_crash", "n90_top20_novolcap"],
             "n90_top20_novolcap"),
            ("phaseH", ["n85_top20_novolcap", "n90_top20",
                        "n90_top15_novolcap", "n85_top15_novolcap"],
             "n90_top15_novolcap")):
        P(f"\n  {phase}: chosen = {chosen}")
        rows = []
        for t in tags:
            n = load_nav(t)["nav"].astype(float)
            tn = n.loc[:TRAIN_END]
            mt, mf = mstats(tn), mstats(n)
            rows.append(dict(tag=t, train_ret=mt["total_return"], train_dd=mt["max_drawdown"],
                             train_sharpe=mt["sharpe"], full_ret=mf["total_return"],
                             full_dd=mf["max_drawdown"], full_sharpe=mf["sharpe"]))
        df = pd.DataFrame(rows)
        P("    " + df.to_string(index=False).replace("\n", "\n    "))
        best_train = df.loc[df["train_ret"].idxmax(), "tag"]
        best_full = df.loc[df["full_ret"].idxmax(), "tag"]
        best_train_sharpe = df.loc[df["train_sharpe"].idxmax(), "tag"]
        P(f"    best train return: {best_train}; best train sharpe: {best_train_sharpe};"
          f" best full-window return: {best_full}; chosen: {chosen}")

    # ------------------------------------------ decisive: DD-budget classification
    P("\n[E] ARE THE 30%-DD-BUDGET CLASSIFICATIONS OF B/C TRAIN- OR FULL-WINDOW?")
    for t in ("n90_top20_novolcap", "n90_top15_novolcap", "b100_n30_top15"):
        n = load_nav(t)["nav"].astype(float)
        mt, mf = mstats(n.loc[:TRAIN_END]), mstats(n)
        P(f"  {t:22s} train DD={mt['max_drawdown']:.6f}  full DD={mf['max_drawdown']:.6f}")
    P("  ROUND4.md §5 classifies B (-30.02%) as '贴线' and C (-32.02%) as '越线' using the")
    P("  FULL-window drawdown (both include 2024-09+).  A train-only classification differs")
    P("  whenever the train-window DD is on the other side of -30% (see numbers above).")

    # ----------------------------------------- whether OOS could influence rules
    P("\n[F] COULD 2024-09+ HAVE INFLUENCED A RULE BEFORE IT WAS REPORTED?")
    P("  - run_phaseD.py docstring (file at 21:32:52):")
    P("      'Motivated by the 2024-09+ miss: the frozen machine stayed at N (30%) through")
    P("       most of the 2024-2025 bull and did not re-enter between month-ends.'")
    P("      => the rule-iteration after Phase B is explicitly motivated by the OOS segment.")
    P("  - phaseE/F/G/H specs (21:35:14..21:39:27) only exist to tune around the")
    P("    full-window winners; phaseG varies N=0.80/0.90/1.00, phaseH varies top_n 15/20.")
    P("  - ROUND4.md §0/§5/§6.2 explicitly discloses: 'Phase C 之后的调整是在看到该 OOS")
    P("    结果之后做的，因此不再具备样本外性质'.")
    P("  => PROVABLE CONTAMINATION: yes for candidates B and C (and for every phase C-H")
    P("     variant).  NOT contaminating for the Phase-B-only OOS claim (+14.96% for")
    P("     b100_n30_top15), whose config predates any 2024+ data being seen ([B] above).")

    # ================================================== report number consistency
    P("\n" + "=" * 100)
    P("PART 2: REPORT NUMBER CONSISTENCY (ROUND4.md, CANDIDATE_A_FREEZE.md, out tables)")
    P("=" * 100)

    # ------------------------------------------------ ROUND4.md §1
    P("\n[G] ROUND4.md §1 (first-OOS table, phase B only)")
    S1 = [  # tag, total pp, ddp p, sharpe, latest pp, latest dd pp
        ("V_base", 68.69, -14.48, 0.63, -4.10, -14.48),
        ("b100_n30", 137.85, -21.76, 0.68, 0.61, -18.11),
        ("b100_n30_top15", 174.56, -23.98, 0.82, 14.96, -12.81),
        ("b100_n30_lowvol", 166.82, -24.65, 0.83, 4.28, -16.14),
        ("b100_n30_vt20", 123.44, -18.59, 0.68, 1.35, -16.74),
        ("b80_n30", 105.44, -17.33, 0.68, -2.58, -15.46),
        ("REF_none_100", 176.34, -56.19, 0.48, -2.56, -38.48),
        ("bh_csi300", 92.39, -46.70, 0.37, 31.65, -17.66),
        ("bh_chinext", 94.85, -69.61, 0.34, 78.71, -37.12),
    ]
    for tag, tr_, dd_, sh_, lr_, ldd_ in S1:
        n = load_nav(tag)["nav"].astype(float)
        m = mstats(n)
        lt = seg(n, *LATEST)
        sc = f"S1:{tag}"
        C.pp(sc, "total_return", m["total_return"], tr_)
        C.pp(sc, "max_drawdown", m["max_drawdown"], dd_)
        C.sh(sc, "sharpe", m["sharpe"], sh_)
        C.pp(sc, "latest_ret", lt["ret"], lr_)
        C.pp(sc, "latest_dd", lt["dd"], ldd_)
    P(f"  checks so far: {len(C.rows)} fails: {len(C.fails)}")

    # ------------------------------------------------ ROUND4.md §2 (train)
    P("\n[H] ROUND4.md §2 (train-window single-variable table)")
    # phaseA / phaseA2 summary rows; engine metrics convention for vol/sharpe
    A = pd.read_csv(OUT / "phaseA_summary.csv")
    A2 = pd.read_csv(OUT / "phaseA2_summary.csv")
    S2 = [
        ("S0 baseline", "V_base", A), ("B100/N25", "exp_b100_n25", A),
        ("B100/N30", "exp_b100_n30", A), ("B100/N50", "exp_b100_n50", A),
        ("B80/N30", "exp_b80_n30", A), ("top15", "top15", A), ("top5", "top5", A),
        ("buffer5", "buffer5", A), ("quarterly", "quarterly", A),
        ("novolcap", "novolcap", A), ("lowvol", "score_lowvol", A),
        ("B100/N30 lowvol", "b100n30_lowvol", A2),
        ("B100/N30 top15", "b100n30_top15", A2),
        ("none_100", "state_none_100", A), ("none_50", "state_none_50", A),
    ]
    REP2 = {
        "V_base": (76.21, -12.65, 0.86), "exp_b100_n25": (128.71, -21.38, 0.86),
        "exp_b100_n30": (136.74, -21.76, 0.88), "exp_b100_n50": (126.02, -25.56, 0.77),
        "exp_b80_n30": (111.25, -17.33, 0.90), "top15": (74.74, -16.28, 0.90),
        "top5": (42.64, -17.57, 0.52), "buffer5": (75.53, -12.17, 0.87),
        "quarterly": (64.17, -14.84, 0.77), "novolcap": (72.70, -10.71, 0.84),
        "score_lowvol": (80.98, -15.34, 1.02), "b100n30_lowvol": (156.05, -24.65, 1.01),
        "b100n30_top15": (139.17, -23.98, 0.91), "state_none_100": (163.50, -56.19, 0.55),
        "state_none_50": (65.17, -31.19, 0.52),
    }
    for label, tag, df_ in S2:
        row = df_[df_["tag"] == tag].iloc[0]
        tr_, dd_, sh_ = REP2[tag]
        sc = f"S2:{label}"
        C.pp(sc, "total_return", row["total_return"], tr_)
        C.pp(sc, "max_drawdown", row["max_drawdown"], dd_)
        C.sh(sc, "sharpe", row["sharpe"], sh_)
    P(f"  checks so far: {len(C.rows)} fails: {len(C.fails)}")

    # ------------------------------------------------ ROUND4.md §3 segments
    P("\n[I] ROUND4.md §3 six segments + yearly table")
    REP3 = {  # tag -> [seg pp in order]
        "V_base": [63.2, -8.0, -0.8, 19.3, -7.6, -4.1],
        "b100_n30_top15": [109.7, -11.7, -3.5, 12.9, 2.4, 15.0],
        "n90_top20_novolcap": [104.0, -9.9, -7.3, 38.0, -0.8, 22.0],
        "bh_csi300": [146.5, -45.3, -26.3, 95.6, -45.2, 31.6],
        "bh_chinext": [175.3, -48.3, -29.2, 176.8, -53.6, 78.7],
    }
    for tag, vals in REP3.items():
        n = load_nav(tag)["nav"].astype(float)
        for (name, s, e), rep in zip(SEGS, vals):
            st = seg(n, s, e)
            C.pp(f"S3seg:{tag}:{name}", "ret", st["ret"], rep, nd=1)
    REP3Y = {  # tag -> [2014..2026 pp]
        "V_base": [6.1, 40.3, -0.9, 8.7, -0.4, 2.4, 7.5, 0.7, -1.4, 1.0, 2.2, 6.7, -12.2],
        "b100_n30_top15": [13.3, 61.7, -1.0, 17.1, -3.0, 1.3, 3.7, 10.5, -1.4, 1.5,
                           3.4, 16.3, -4.5],
        "n90_top20_novolcap": [10.8, 65.6, -2.5, 24.5, -5.9, -3.0, 21.0, 18.7, -4.8,
                               2.7, 4.0, 18.3, -1.0],
        "bh_csi300": [62.9, 5.6, -11.3, 21.8, -25.3, 36.1, 27.2, -5.2, -21.6, -11.4,
                      14.7, 17.7, -9.8],
        "bh_chinext": [4.7, 84.0, -27.6, -10.6, -28.5, 43.5, 64.7, 12.0, -29.3, -19.3,
                       13.2, 49.4, -14.2],
    }
    years = [str(y) for y in range(2014, 2027)]
    for tag, vals in REP3Y.items():
        n = load_nav(tag)["nav"].astype(float)
        yr = yearly(n)
        for y, rep in zip(years, vals):
            C.pp(f"S3year:{tag}:{y}", "ret", yr.get(y), rep, nd=1)
    P(f"  checks so far: {len(C.rows)} fails: {len(C.fails)}")

    # ------------------------------------------------ ROUND4.md §4.2-4.5
    P("\n[J] ROUND4.md §4.2-§4.5 negative-result numbers")
    fast = load_nav("top15_fast")["nav"].astype(float)
    mf = mstats(fast)
    C.pp("S4.2:top15_fast", "total_return", mf["total_return"], 110.39)
    C.pp("S4.2:top15_fast", "max_drawdown", mf["max_drawdown"], -26.83)
    fuse = load_nav("top15_fuse_10_15")
    expo = fuse["exposure"].astype(float)
    st = seg(fuse["nav"].astype(float), *LATEST)
    C.chk("S4.3:fuse", "avg_exposure~0.10", float(expo.mean()), 0.10, 0.006)
    C.chk("S4.3:fuse", "latest_ret==0", abs(st["ret"]), 0.0, 1e-12)
    crash = load_nav("top15_crash")["nav"].astype(float)
    mc = mstats(crash)
    C.pp("S4.4:crash", "total_return", mc["total_return"], 94.58)
    C.pp("S4.4:crash", "max_drawdown", mc["max_drawdown"], -26.43)
    P(f"  checks so far: {len(C.rows)} fails: {len(C.fails)}")

    # ------------------------------------------------ ROUND4.md §4.6 stress
    P("\n[K] ROUND4.md §4.6 cost-stress table")
    st1 = pd.read_csv(OUT / "phase_stress_b100n30_top15.csv")
    base = st1[st1["cost"] == "base"].iloc[0]
    x2 = st1[st1["cost"] == "x2cost"].iloc[0]
    C.pp("S4.6:b100_n30_top15", "base_total", base["total_return"], 174.56)
    C.pp("S4.6:b100_n30_top15", "stress_total", x2["total_return"], 149.67)
    C.pp("S4.6:b100_n30_top15", "stress_dd", x2["max_drawdown"], -25.23)
    C.sh("S4.6:b100_n30_top15", "stress_sharpe", x2["sharpe"], 0.75)
    ss = pd.read_csv(OUT / "phase_stress_summary.csv")
    r90 = ss[ss["tag"] == "stress_n90_top20_novolcap"].iloc[0]
    C.pp("S4.6:n90", "stress_total", r90["total_return"], 214.22)
    C.pp("S4.6:n90", "stress_dd", r90["max_drawdown"], -30.70)
    C.sh("S4.6:n90", "stress_sharpe", r90["sharpe"], 0.71)
    C.chk("S4.6:fees", "base_fees~13572", float(base["total_fees"]), 13572.0, 0.5)
    C.chk("S4.6:fees", "stress_fees~14352", float(x2["total_fees"]), 14352.0, 0.5)
    # the conflicting stress_summary row (verified by re-run in v4_overlay.py)
    rb = ss[ss["tag"] == "stress_b100n30_top15"].iloc[0]
    P(f"  [INCONSISTENCY] out/phase_stress_summary.csv stress_b100n30_top15 row:"
      f" total={rb['total_return']:.6f} dd={rb['max_drawdown']:.6f}"
      f" sharpe={rb['sharpe']:.6f} fees={rb['total_fees']:.2f}")
    P(f"    but out/phase_stress_b100n30_top15.csv x2cost (= report value, reproduced by a")
    P(f"    fresh run in v4_overlay.py): total={x2['total_return']:.6f}"
      f" dd={x2['max_drawdown']:.6f} sharpe={x2['sharpe']:.6f} fees={x2['total_fees']:.2f}")
    P(f"    -> phase_stress_summary.csv row is stale/wrong; ROUND4.md §4.6 value is correct.")
    C.rows.append(dict(scope="S4.6:internal", item="stress_summary vs stress_b100n30 file",
                       recomputed=float(x2["total_return"]),
                       reported=float(rb["total_return"]), abs_diff=float(
                           abs(x2["total_return"] - rb["total_return"])), tol=1e-6,
                       ok=False))
    C.fails.append(C.rows[-1])

    # ------------------------------------------------ ROUND4.md §5 final candidates
    P("\n[L] ROUND4.md §5 final candidate table")
    S5 = [("b100_n30_top15", 174.56, -23.98, 0.82, 14.96),
          ("n90_top20_novolcap", 248.19, -30.02, 0.76, 22.00),
          ("n90_top15_novolcap", 285.11, -32.02, 0.80, 21.62)]
    for tag, tr_, dd_, sh_, lr_ in S5:
        n = load_nav(tag)["nav"].astype(float)
        m = mstats(n)
        lt = seg(n, *LATEST)
        sc = f"S5:{tag}"
        C.pp(sc, "total_return", m["total_return"], tr_)
        C.pp(sc, "max_drawdown", m["max_drawdown"], dd_)
        C.sh(sc, "sharpe", m["sharpe"], sh_)
        C.pp(sc, "latest_ret", lt["ret"], lr_)
    # §4.1 DD-budget statement
    mB = mstats(load_nav("n90_top20_novolcap")["nav"].astype(float))
    C.pp("S4.1:n90", "max_drawdown=-30.02", mB["max_drawdown"], -30.02)
    P(f"  checks so far: {len(C.rows)} fails: {len(C.fails)}")

    # ------------------------------------------- CANDIDATE_A_FREEZE.md numbers
    P("\n[M] CANDIDATE_A_FREEZE.md §2 evidence table")
    n = load_nav("b100_n30_top15")["nav"].astype(float)
    m = mstats(n)
    lt = seg(n, *LATEST)
    sc = "freezeA"
    C.pp(sc, "total_return", m["total_return"], 174.56)
    C.pp(sc, "max_drawdown", m["max_drawdown"], -23.98)
    C.pp(sc, "cagr", m["cagr"], 8.89)
    C.pp(sc, "ann_vol", m["ann_vol"], 11.13)
    C.sh(sc, "sharpe", m["sharpe"], 0.82)
    C.pp(sc, "avg_exposure", float(load_nav('b100_n30_top15')['exposure'].astype(float).mean()), 26.4, nd=1)
    C.chk(sc, "all_cash_days", float(
        (load_nav('b100_n30_top15')['market_value'].astype(float) <= .01).sum()), 1285.0, 0)
    C.pp(sc, "latest_ret", lt["ret"], 14.96)
    P(f"  recomputed dd peak={m['dd_peak_date']} trough={m['dd_trough_date']}")
    P(f"  freeze file says '峰 2020-03-23 前高，谷 2020-03-23' -> peak date is WRONG"
      f" (actual peak {m['dd_peak_date']}, trough {m['dd_trough_date']})")
    C.rows.append(dict(scope=sc, item="dd_peak_date (freeze says 2020-03-23)",
                       recomputed=m["dd_peak_date"], reported="2020-03-23",
                       abs_diff=None, tol=0, ok=False))
    C.fails.append(C.rows[-1])

    # ------------------------------------------------ consolidated out tables
    P("\n[N] out/ROUND4_FINAL_TABLE.csv, ROUND4_SEGMENTS.csv, ROUND4_YEARLY.csv")
    ft = pd.read_csv(OUT / "ROUND4_FINAL_TABLE.csv")
    for _, row in ft.iterrows():
        tag = row["tag"]
        n = load_nav(tag)["nav"].astype(float)
        m = mstats(n)
        lt = seg(n, *LATEST)
        df_ = load_nav(tag)
        sc = f"final:{tag}"
        C.chk(sc, "total_return", m["total_return"], row["total_return"], 1e-9)
        C.chk(sc, "max_drawdown", m["max_drawdown"], row["max_drawdown"], 1e-9)
        C.chk(sc, "sharpe", m["sharpe"], row["sharpe"], 1e-6)
        C.chk(sc, "cagr", m["cagr"], row["cagr"], 1e-9)
        C.chk(sc, "ann_vol", m["ann_vol"], row["ann_vol"], 1e-9)
        C.chk(sc, "days", float(m["days"]), float(row["days"]), 0)
        C.chk(sc, "dd_trough", 0.0 if m["dd_trough_date"] == str(row["dd_trough"]) else 1.0, 0.0, 0)
        C.chk(sc, "latest_ret", lt["ret"], row["latest_ret"], 1e-9)
        C.chk(sc, "avg_exposure",
              float(df_["exposure"].astype(float).mean()), row["avg_exposure"], 1e-9)
    reps = pd.read_csv(OUT / "ROUND4_SEGMENTS.csv")
    # ROUND4_SEGMENTS has no start/end columns; use the fixed SEGS order per tag
    for tag, g in reps.groupby("tag", sort=False):
        n = load_nav(tag)["nav"].astype(float)
        segnames = list(g["segment"])
        vals = {r["segment"]: (r["ret"], r["dd"]) for _, r in g.iterrows()}
        for name, s, e in SEGS:
            if name not in vals:
                continue
            st = seg(n, s, e)
            C.chk(f"R4SEG:{tag}", f"{name}:ret", st["ret"], vals[name][0], 1e-9)
            C.chk(f"R4SEG:{tag}", f"{name}:dd", st["dd"], vals[name][1], 1e-9)
    reps = pd.read_csv(OUT / "ROUND4_YEARLY.csv")
    for tag, g in reps.groupby("tag", sort=False):
        n = load_nav(tag)["nav"].astype(float)
        yr = yearly(n)
        for _, r in g.iterrows():
            C.chk(f"R4YEAR:{tag}", str(r["year"]), yr.get(str(r["year"])), r["ret"], 1e-9)
    P(f"  checks so far: {len(C.rows)} fails: {len(C.fails)}")

    # -------------------------------------------------------------- hashes §7
    P("\n[O] ROUND4.md §7 / CANDIDATE_A_FREEZE SHA256 list")
    hashes = {
        "variants.py": "ee19328e231f74af782fb607a31de99393630994c8bf379c6d6659ba87d7b27b",
        "overlays.py": "fb59f65cdb049b848d141edbb2996d9a191e8844d06364f48756bb4290c55348",
        "run_phaseA.py": "0705d85abdb6aea209e15ab73973cac50e53d4f7f4965ad723d59dc07fb9d73d",
        "run_phaseB.py": "6ad562c550bbREDACTED_IDae0a23e74ead9dc7df68f18b6a2163b873",
        "run_phaseC.py": "2861fd31c5f8a6d3ced4bfe0264d87b172b0b03b023d5ebdf977a1d56ec86c2b",
        "run_phaseD.py": "ecab937095ce6dc5b773ad36a7b018cba02a1419d8d80af510c4e42513f5d5b4",
        "run_phaseG.py": "42d2a3e4677a07c5925096a984863c8308e8ed64b699f541b5109e695efb9925",
    }
    for f, h in hashes.items():
        got = hashlib.sha256((R4 / f).read_bytes()).hexdigest()
        C.chk("hash", f, 0.0 if got == h else 1.0, 0.0, 0)
        P(f"  {f:16s} {'MATCH' if got == h else 'MISMATCH'}  {got[:16]}...")
    reg = WS / "qrun_1416" / "os" / "out" / "regime_v1.csv"
    rh = hashlib.sha256(reg.read_bytes()).hexdigest()
    P(f"  regime_v1.csv sha256={rh} (freeze claims 8990cf75..67d5:"
      f" {rh.startswith('8990cf75') and rh.endswith('67d5')})")
    nreg = sum(1 for _ in open(reg, encoding="utf-8")) - 1
    P(f"  regime_v1.csv rows={nreg} (report says platform RUN_TEXT 3448/3448)")

    # ------------------------------------------------- run-count consistency
    P("\n[P] RUN-COUNT / phase-count consistency")
    counts = {}
    for f, n_ in (("phaseA_summary.csv", 21), ("phaseA2_summary.csv", 13),
                  ("phaseB", 13), ("phaseC_summary.csv", 11), ("phaseD_summary.csv", 12),
                  ("phaseE_summary.csv", 10), ("phaseF_summary.csv", 5),
                  ("phaseG_summary.csv", 4), ("phaseH_summary.csv", 4)):
        p = OUT / f
        if p.exists():
            counts[f] = len(pd.read_csv(p))
            P(f"  {f:24s} rows={counts[f]} (report says {n_})")
    P(f"  strategy runs A+A2+B+C+D+E+F+G+H ="
      f" {counts.get('phaseA_summary.csv', 0) + counts.get('phaseA2_summary.csv', 0) + 13 + counts.get('phaseC_summary.csv', 0) + counts.get('phaseD_summary.csv', 0) + counts.get('phaseE_summary.csv', 0) + counts.get('phaseF_summary.csv', 0) + counts.get('phaseG_summary.csv', 0) + counts.get('phaseH_summary.csv', 0)}"
      f" (report text: '93 条本地回测', excl. 2 benchmarks and 2 stress runs)")

    # ------------------------------------------------------------- summary
    P("\n" + "=" * 100)
    P(f"REPORT-NUMBER COMPARISONS: {len(C.rows)}  FAILS: {len(C.fails)}")
    for f in C.fails:
        P(f"  FAIL [{f['scope']}] {f['item']}: recomputed={f['recomputed']!r}"
          f" reported={f['reported']!r} diff={f['abs_diff']!r} tol={f['tol']!r}")
    P("=" * 100)

    payload = dict(
        phaseB_configs_unchanged=bool(all_same),
        report_checks=C.rows,
        report_fails=C.fails,
        evidence=dict(
            phaseD_docstring=("Motivated by the 2024-09+ miss: the frozen machine stayed at"
                              " N (30%) through most of the 2024-2025 bull and did not"
                              " re-enter between month-ends."),
            phaseFGH_run_full_window=True,
            report_disclosure="ROUND4.md §0/§5/§6.2 explicitly says Phase C-H used"
                              " post-2024-09 information and is no longer OOS",
        ),
    )
    (VER / "v3_oos_report.json").write_text(json.dumps(payload, indent=1, default=str),
                                            encoding="utf-8")
    (VER / "v3_oos_report.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\nwrote", VER / "v3_oos_report.log", "and v3_oos_report.json")


if __name__ == "__main__":
    main()
