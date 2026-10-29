# -*- coding: utf-8 -*-
"""Shared helpers for round-5 attribution of frozen strategy A.

Read-only inputs: qrun_1416/ (engine+data), round4/ (frozen A code).
Writes only under round5/attrib/.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROUND5 = HERE.parent
WORKSPACE = ROUND5.parent.parent
REPO = WORKSPACE / "qrun_1416"
ROUND4 = ROUND5.parent / "round4"
OUT = HERE / "out"
CACHE = OUT / "cache"
DATA = REPO / "data"
for p in (ROUND4, REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

CAP = 100_000.0
START, END = "2014-07-01", "2026-10-19"
STATE_MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}
LETTER_CN = {"B": "牛市(B)", "N": "中性(N)", "E": "熊市(E)", "C": "崩盘(C)"}

# four pre-registered stress windows (task 3f)
STRESS_WINDOWS = [
    ("2015_crash", "2015-06-01", "2016-02-29"),
    ("2020_covid", "2020-01-02", "2020-04-30"),
    ("2025_apr", "2025-04-01", "2025-04-30"),
    ("2026_y1", "2026-01-02", "2026-10-19"),
]


# --------------------------------------------------------------------------- #
# ledgers written by rerun_a.py
# --------------------------------------------------------------------------- #
def read_csv(path, **kw):
    kw.setdefault("dtype", {"date": str})
    return pd.read_csv(path, **kw)


def load_ledgers():
    """Return dict of DataFrames from out/ (codes normalised to 0-padded strings)."""
    out = {}
    nav = pd.read_csv(OUT / "a_nav.csv", dtype={"date": str})
    nav = nav.set_index("date")
    for c in nav.columns:
        if c == "regime":
            nav[c] = nav[c].astype(object).where(nav[c].notna(), None)
        else:
            nav[c] = pd.to_numeric(nav[c], errors="coerce")
    out["nav"] = nav

    def _norm(df, codecol="code"):
        if codecol in df.columns:
            df[codecol] = df[codecol].astype(str).str.replace(r"\.0$", "", regex=True)
            df[codecol] = df[codecol].str.zfill(6)
        return df

    pos = pd.read_csv(OUT / "a_positions.csv", dtype={"date": str})
    pos["code"] = pos["code"].astype(str).str.zfill(6)
    out["positions"] = pos
    tr = pd.read_csv(OUT / "a_trades.csv", dtype={"date": str})
    tr["code"] = tr["code"].astype(str).str.zfill(6)
    out["trades"] = tr
    od = pd.read_csv(OUT / "a_orders.csv", dtype={"created_date": str, "filled_date": str})
    od["code"] = od["code"].astype(str).str.zfill(6)
    out["orders"] = od
    ev = pd.read_csv(OUT / "a_events.csv", dtype={"date": str, "signal_date": str})
    if "code" in ev.columns:
        ev["code"] = ev["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        ev.loc[ev["code"] == "00nan", "code"] = np.nan
    out["events"] = ev
    bk = pd.read_csv(OUT / "a_target_books.csv", dtype={"date": str})
    bk["code"] = bk["code"].astype(str).str.zfill(6)
    out["books"] = bk
    out["calls"] = pd.read_csv(OUT / "a_call_log.csv",
                               dtype={"date": str, "code": str})
    aud = pd.read_csv(OUT / "a_audit_days.csv", dtype={"date": str})
    out["audit_days"] = aud
    out["audit_reb"] = pd.read_csv(OUT / "a_audit_reb.csv", dtype={"date": str})
    return out


def load_regime_letters() -> pd.DataFrame:
    reg = pd.read_csv(REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    reg["letter"] = reg["state"].map(STATE_MAP)
    return reg.set_index("date")[["state", "letter"]]


# --------------------------------------------------------------------------- #
# panel cache: wide qfq / raw price matrices for the codes ever held by A
# --------------------------------------------------------------------------- #
PANEL_FIELDS = {"qfq_open": "open", "qfq_close": "close", "raw_open": "open",
                "raw_close": "close", "raw_preclose": "preclose"}


def build_panel_cache(codes=None) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    led = load_ledgers()
    if codes is None:
        codes = set(led["positions"]["code"].unique()) | set(led["books"]["code"].unique())
        codes = set(led["trades"]["code"].unique()) | codes
    codes = sorted(c for c in codes if isinstance(c, str) and len(c) == 6)
    years = range(2012, 2027)
    frames = {k: [] for k in PANEL_FIELDS}
    for y in years:
        fq = DATA / "panel" / f"bars_qfq_{y}.csv.gz"
        fr = DATA / "panel" / f"bars_{y}.csv.gz"
        if fq.exists():
            d = pd.read_csv(fq, dtype={"code": str},
                            usecols=["code", "date", "open", "close"])
            d = d[d["code"].isin(codes)]
            for k, src in (("qfq_open", "open"), ("qfq_close", "close")):
                frames[k].append(d[["code", "date", src]].rename(columns={src: k}))
        if fr.exists():
            d = pd.read_csv(fr, dtype={"code": str},
                            usecols=["code", "date", "open", "close", "preclose"])
            d = d[d["code"].isin(codes)]
            for k, src in (("raw_open", "open"), ("raw_close", "close"),
                           ("raw_preclose", "preclose")):
                frames[k].append(d[["code", "date", src]].rename(columns={src: k}))
        print(f"  loaded {y}", end="\r")
    out = {}
    for k, parts in frames.items():
        df = pd.concat(parts, ignore_index=True)
        wide = df.pivot_table(index="date", columns="code", values=k, aggfunc="last")
        wide = wide.sort_index()
        wide.index.name = "date"
        out[k] = wide.astype(np.float64)
        print(f"{k}: {wide.shape}")
    with open(CACHE / "panel_wide.pkl", "wb") as fh:
        pickle.dump({"fields": out, "codes": codes}, fh, protocol=4)
    return {"fields": out, "codes": codes}


def load_panel_cache():
    p = CACHE / "panel_wide.pkl"
    if not p.exists():
        return build_panel_cache()
    with open(p, "rb") as fh:
        return pickle.load(fh)


# --------------------------------------------------------------------------- #
# metrics helpers
# --------------------------------------------------------------------------- #
def maxdd_series(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1.0


def maxdd_stats(nav: pd.Series) -> dict:
    s = nav.astype(float).dropna()
    dd = maxdd_series(s)
    if len(s) < 2 or dd.min() >= 0:
        return {"max_drawdown": 0.0}
    tp = int(np.argmin(dd.to_numpy()))
    trough = s.index[tp]
    peak = s.index[int(np.argmax(s.iloc[:tp + 1].to_numpy()))]
    after = s.iloc[tp:]
    rec = after[after >= s.loc[peak]]
    recovery = str(rec.index[0]) if len(rec) else None
    return dict(max_drawdown=float(dd.iloc[tp]), peak_date=str(peak),
                trough_date=str(trough), recovery_date=recovery,
                underwater_days=int((dd < 0).sum()))


def drawdown_episodes(nav: pd.Series, top: int = 3) -> pd.DataFrame:
    """Non-overlapping peak->recovery underwater episodes, ranked by depth."""
    s = nav.astype(float).dropna()
    dd = maxdd_series(s)
    idx = list(s.index)
    peak = float(s.iloc[0])
    peak_i = 0
    rows = []
    i = 0
    n = len(s)
    while i < n:
        v = float(s.iloc[i])
        if v >= peak:
            peak = v
            peak_i = i
            i += 1
            continue
        # start of an underwater stretch at i-1 == peak_i
        j = i
        trough_i = i
        while j < n and float(s.iloc[j]) < peak:
            if float(s.iloc[j]) < float(s.iloc[trough_i]):
                trough_i = j
            j += 1
        rec_i = j if j < n else None
        rows.append(dict(
            peak_date=idx[peak_i], peak_nav=peak,
            trough_date=idx[trough_i], trough_nav=float(s.iloc[trough_i]),
            depth=float(s.iloc[trough_i] / peak - 1.0),
            recovery_date=idx[rec_i] if rec_i is not None else None,
            duration_calendar_days=int((pd.Timestamp(idx[rec_i]) - pd.Timestamp(idx[peak_i])).days)
            if rec_i is not None else int((pd.Timestamp(idx[-1]) - pd.Timestamp(idx[peak_i])).days) + 1,
            underwater_days=int(j - peak_i),
            still_underwater=bool(rec_i is None)))
        i = j
    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df
    return df.sort_values("depth").head(top).reset_index(drop=True)


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.astype(float).pct_change(fill_method=None)


# --------------------------------------------------------------------------- #
# exact daily P&L attribution by stock
# --------------------------------------------------------------------------- #
def compute_daily_attribution(led: dict, panel: dict) -> dict:
    """Decompose every day's NAV change exactly by stock and by timing bucket.

    Method (exact, no model):
      delta_nav(t) = sum_stock [value_end - value_start - buy_amount + sell_amount]
                     - fees(t)
      value_* come from the engine's own position ledger (shares x last raw price),
      buy/sell amount from the engine's trade ledger (fill price x shares, fees
      excluded).  This identity is checked day by day.

    Each stock contribution is then split (approximation, qfq total-return based):
      overnight       shares_start * close_prev * (qfq_open/qfq_close_prev - 1)
      intraday_held   shares_start * close_prev * (qfq_close - qfq_open)/qfq_close_prev
      intraday_traded bought*(close-open_raw) - sold*(close-open_raw)  (raw prices)
      trade_exec      bought*(open_raw-fill) + sold*(fill-open_raw)    (= -slippage)
      ca_residual     exact total minus the qfq-based split (corporate-action
                      preclose-reference cases or missing qfq bars; raw fallback used)
    """
    nav = led["nav"]
    pos = led["positions"]
    tr = led["trades"]
    f = panel["fields"]
    qopen, qclose = f["qfq_open"], f["qfq_close"]
    ropen, rclose = f["raw_open"], f["raw_close"]

    dates = list(nav.index)
    pos_by_date = {d: g for d, g in pos.groupby("date", sort=False)}
    tr_by_date = {d: g for d, g in tr.groupby("date", sort=False)}
    empty = pd.DataFrame(columns=["code", "shares", "price", "value"])

    # long rows and matrices
    rows_long = []
    codes_all = sorted(set(pos["code"].unique()) | set(tr["code"].unique()))
    codes_all = [c for c in codes_all if c in qclose.columns or c in rclose.columns]
    mat = {k: pd.DataFrame(0.0, index=dates, columns=codes_all)
           for k in ("contrib_gross", "overnight", "intraday", "trade_exec",
                     "ca_residual", "fee")}
    recon = []
    prev_rec = empty
    prev_date = None
    for t in dates:
        cur = pos_by_date.get(t, empty)
        start = {r.code: (float(r.shares), float(r.price), float(r.value))
                 for r in prev_rec.itertuples()}
        end = {r.code: (float(r.shares), float(r.price), float(r.value))
               for r in cur.itertuples()}
        tt = tr_by_date.get(t, empty)
        traded = {}
        for r in tt.itertuples():
            if r.side not in ("buy", "sell"):
                continue
            d = traded.setdefault(r.code, dict(buy_sh=0.0, buy_amt=0.0, sell_sh=0.0,
                                               sell_amt=0.0, buy_px=[], sell_px=[], fee=0.0))
            if r.side == "buy":
                d["buy_sh"] += float(r.shares)
                d["buy_amt"] += float(r.amount)
                d["buy_px"].append((float(r.shares), float(r.price)))
            else:
                d["sell_sh"] += float(r.shares)
                d["sell_amt"] += float(r.amount)
                d["sell_px"].append((float(r.shares), float(r.price)))
            d["fee"] += float(r.fee_total)
        codes = set(start) | set(end) | set(traded)
        day_sum = 0.0
        for c in codes:
            s0, c0, v0 = start.get(c, (0.0, np.nan, 0.0))
            s1, c1, v1 = end.get(c, (0.0, np.nan, 0.0))
            d = traded.get(c)
            buy_amt = d["buy_amt"] if d else 0.0
            sell_amt = d["sell_amt"] if d else 0.0
            total = v1 - v0 - buy_amt + sell_amt
            day_sum += total

            # ---- timing split -------------------------------------------
            # overnight / intraday come from the qfq total-return ratio (or raw
            # prices when a qfq bar is missing); trade_exec is the fill-vs-open
            # execution effect (== -slippage cost); the residual absorbs whatever
            # corporate-action share-multiplier effect the qfq ratio did not
            # reproduce (preclose-reference mismatch / missing qfq bars).
            overnight = intraday = trade_exec = 0.0
            # end price for trade legs: a fully-closed position has no ledger price,
            # so fall back to the panel close (or open) of that day
            c_end = c1
            if not np.isfinite(c_end):
                for _src in (rclose, ropen):
                    if t in _src.index and c in _src.columns:
                        _v = float(_src.at[t, c])
                        if np.isfinite(_v) and _v > 0:
                            c_end = _v
                            break
            if s0 > 0 and np.isfinite(c0):
                try:
                    q0 = float(qclose.at[prev_date, c]) if prev_date in qclose.index else np.nan
                    qo1 = float(qopen.at[t, c]) if t in qopen.index else np.nan
                    q1 = float(qclose.at[t, c]) if t in qclose.index else np.nan
                except KeyError:
                    q0 = qo1 = q1 = np.nan
                o1 = float(ropen.at[t, c]) if (t in ropen.index and c in ropen.columns) else np.nan
                if all(np.isfinite(x) and x > 0 for x in (q0, qo1, q1)):
                    overnight = s0 * c0 * (qo1 / q0 - 1.0)
                    intraday_held = s0 * c0 * (q1 - qo1) / q0
                elif np.isfinite(o1):
                    overnight = s0 * (o1 - c0)
                    intraday_held = s0 * (c1 - o1) if np.isfinite(c1) else 0.0
                else:
                    overnight = 0.0
                    intraday_held = s0 * (c1 - c0) if np.isfinite(c1) else 0.0
                intraday += intraday_held
            if d:
                o1 = float(ropen.at[t, c]) if (t in ropen.index and c in ropen.columns) else np.nan
                for sh, px in d["buy_px"]:
                    if np.isfinite(o1) and np.isfinite(c_end):
                        trade_exec += sh * (o1 - px)
                        intraday += sh * (c_end - o1)
                    elif np.isfinite(c_end):
                        intraday += sh * (c_end - px)
                for sh, px in d["sell_px"]:
                    if np.isfinite(o1) and np.isfinite(c_end):
                        trade_exec += sh * (px - o1)
                        intraday += sh * (o1 - c_end)
                    elif np.isfinite(c_end):
                        intraday += sh * (px - c_end)
            # residual makes the timing split add up exactly to the contribution
            ca_resid = total - overnight - intraday - trade_exec
            fee = d["fee"] if d else 0.0
            mat["contrib_gross"].at[t, c] += total
            mat["overnight"].at[t, c] += overnight
            mat["intraday"].at[t, c] += intraday
            mat["trade_exec"].at[t, c] += trade_exec
            mat["ca_residual"].at[t, c] += ca_resid
            mat["fee"].at[t, c] += fee
            rows_long.append(dict(date=t, code=c, shares_start=s0, shares_end=s1,
                                  value_start=v0, value_end=v1, buy_amount=buy_amt,
                                  sell_amount=sell_amt, contrib_gross=total,
                                  overnight=overnight, intraday=intraday,
                                  trade_exec=trade_exec, ca_residual=ca_resid, fee=fee))
        prev_nav = float(nav["nav"].iloc[dates.index(t) - 1]) if dates.index(t) > 0 else CAP
        cur_nav = float(nav.at[t, "nav"])
        fees_t = float(nav.at[t, "fees"])
        recon.append(dict(date=t, dnav=cur_nav - prev_nav, gross=day_sum,
                          fees=fees_t, gap=cur_nav - prev_nav - (day_sum - fees_t)))
        prev_rec = cur
        prev_date = t
    return {"long": pd.DataFrame(rows_long), "mat": mat,
            "recon": pd.DataFrame(recon), "codes": codes_all}
