# -*- coding: utf-8 -*-
"""Bull/Bear market regime v1 (Lead-owned, frozen by os/RULES_V1.md).

Computes a daily state machine from data that is fully known at each close:

    CRASH   -> emergency risk-off (subset of BEAR; immediate switch)
    BEAR    -> capital-preservation operating system (0% equity by default)
    NEUTRAL -> transition regime (40% target equity)
    BULL    -> bull operating system (90% target equity)

The label attached to date T is the state evaluated on T's close (i.e. after all
of T's data is known).  Portfolio actions taken at T's close are executed at
T+1's open, which is exactly the timing frozen in os/RULES_V1.md.  This differs
from engine.regime's "actionable at T open" convention on purpose; the Lead's
runner passes these labels to the engine only for reporting/segmentation.

Outputs:
    os/out/regime_v1.csv
        date,state,state_prev,crash_flag,bear_flag,bull_flag,
        dd60,dd250,vol20,volpct,ma60_slope,ma250_slope,br60,br250,
        px_csi300,ma20,ma60,ma120,ma250
    os/out/regime_summary.json

Standalone:
    python os/regime_v1.py [--start 2012-08-01] [--end 2026-10-19] [--data-dir ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.data import DataBundle, DataNotReadyError  # noqa: E402

OUT_DIR = REPO / "os" / "out"

# ---- frozen parameters (os/RULES_V1.md) -----------------------------------
CONFIRM_DAYS = 5          # state changes need 5 consecutive confirming days
DD60_CRASH = -0.18
VOLPCT_CRASH = 0.95
DD60_CRASH_VOL = -0.10
DD250_CRASH = -0.25
DD250_BEAR = -0.20
BR250_BEAR = 0.30
DD60_BEAR = -0.10
BR60_BULL = 0.50
VOL_WINDOW = 20
VOLPCT_WINDOW = 750
SLOPE_FAST = 20
SLOPE_SLOW = 60


def rolling_mean(mat: np.ndarray, window: int) -> np.ndarray:
    """NaN-aware rolling mean along axis 0, float32 matrix (T,N)."""
    valid = np.isfinite(mat)
    vals = np.where(valid, mat, 0.0)
    cs = np.cumsum(vals, axis=0, dtype=np.float64)
    cc = np.cumsum(valid.astype(np.float64), axis=0)
    out = np.full(mat.shape, np.nan, dtype=np.float64)
    out[window - 1:] = (cs[window - 1:] - np.vstack([np.zeros((1, mat.shape[1])), cs[:-window]])) / \
        np.maximum(cc[window - 1:] - np.vstack([np.zeros((1, mat.shape[1])), cc[:-window]]), 1e-9)
    # require full window of valid observations
    nvalid = cc[window - 1:] - np.vstack([np.zeros((1, mat.shape[1])), cc[:-window]])
    out[window - 1:] = np.where(nvalid >= window - 1e-9, out[window - 1:], np.nan)
    return out.astype(np.float32)


def compute_breadth(bundle: DataBundle) -> pd.DataFrame:
    """Daily breadth: % of eligible (listed, non-ST, enough history) stocks above MA60/MA250."""
    panel = bundle.panel
    close = np.asarray(panel.fields["qfq_close"])
    isst = np.asarray(panel.fields["isst"])
    dates = [str(d) for d in bundle.dates]
    T, N = close.shape
    valid = np.isfinite(close)
    print(f"[regime] breadth: panel {T} x {N}", flush=True)

    out = {"date": dates}
    for win, name in ((60, "br60"), (250, "br250")):
        ma = rolling_mean(close, win)
        above = valid & np.isfinite(ma) & (close > ma)
        elig = valid & (isst < 0.5)
        nobs = rolling_mean(valid.astype(np.float32), win)  # fraction of window with a bar
        elig = elig & (nobs >= 1.0 - 1e-9)
        n_elig = elig.sum(axis=1)
        n_above = (above & elig).sum(axis=1)
        br = np.where(n_elig > 0, n_above / np.maximum(n_elig, 1), np.nan)
        out[name] = br
        print(f"[regime] {name}: mean={np.nanmean(br):.3f} min={np.nanmin(br):.3f} max={np.nanmax(br):.3f}", flush=True)
        del ma, above, elig, nobs
    return pd.DataFrame(out).set_index("date")


def compute_regime(data_dir=None, start="2012-08-01", end="2026-10-19") -> pd.DataFrame:
    # Load the same (start, end, warmup) triple as engine.precompute so the engine's
    # memory-mapped panel cache under tmp/engine-dev/cache is reused (no 45s rebuild).
    load_start = "2014-01-01" if start <= "2014-01-01" else start
    bundle = DataBundle.load(data_dir=data_dir, start=load_start, end=end,
                             warmup_days=340, require_panel=True)
    dates = pd.Index([str(d) for d in bundle.dates], name="date")
    idx = bundle.index_close_series("sh000300").reindex(dates)
    if idx.isna().any():
        print(f"[regime] WARNING: CSI300 close has {int(idx.isna().sum())} missing dates; ffill", flush=True)
        idx = idx.ffill()
    px = idx.to_numpy(dtype=float)
    T = len(px)

    def sma(w):
        return pd.Series(px, index=dates).rolling(w, min_periods=w).mean().to_numpy()

    ma20, ma60, ma120, ma250 = sma(20), sma(60), sma(120), sma(250)
    # 60d / 250d drawdown (peak of close over the window, inclusive of today)
    dd60 = px / pd.Series(px, index=dates).rolling(60, min_periods=20).max().to_numpy() - 1.0
    dd250 = px / pd.Series(px, index=dates).rolling(250, min_periods=120).max().to_numpy() - 1.0
    # realized vol (20d, annualised) and its 750d percentile rank
    ret = pd.Series(pd.Series(px, index=dates).pct_change())
    vol20 = ret.rolling(VOL_WINDOW, min_periods=10).std().to_numpy() * np.sqrt(252.0)
    volpct = pd.Series(vol20, index=dates).rolling(VOLPCT_WINDOW, min_periods=250).rank(pct=True).to_numpy()
    # slopes
    ma60s = pd.Series(ma60, index=dates)
    ma250s = pd.Series(ma250, index=dates)
    slope60 = (ma60s / ma60s.shift(SLOPE_FAST) - 1.0).to_numpy()
    slope250 = (ma250s / ma250s.shift(SLOPE_SLOW) - 1.0).to_numpy()

    breadth = compute_breadth(bundle).reindex(dates)
    br60 = breadth["br60"].to_numpy(dtype=float)
    br250 = breadth["br250"].to_numpy(dtype=float)

    # ---- frozen state machine -------------------------------------------
    crash = ((dd60 <= DD60_CRASH) |
             ((volpct >= VOLPCT_CRASH) & (dd60 <= DD60_CRASH_VOL)) |
             ((px < ma250) & (dd250 <= DD250_CRASH)))
    bear = (((px < ma250) & (slope250 < 0)) |
            ((dd250 <= DD250_BEAR) & (br250 < BR250_BEAR)) |
            ((px < ma120) & (px < ma250) & (dd60 <= DD60_BEAR)))
    bull = ((px > ma250) & (slope250 > 0) & (px > ma120) & (br60 >= BR60_BULL))

    flags = pd.DataFrame({"crash": crash, "bear": bear, "bull": bull}, index=dates).fillna(False)
    states = []
    cur = "NEUTRAL"
    pend = None
    pend_n = 0
    for t in range(T):
        c, b, u = bool(flags["crash"].iloc[t]), bool(flags["bear"].iloc[t]), bool(flags["bull"].iloc[t])
        if c:
            cand = "CRASH"
        elif b:
            cand = "BEAR"
        elif u:
            cand = "BULL"
        else:
            cand = "NEUTRAL"
        if cand == "CRASH":
            cur = "CRASH"
            pend, pend_n = None, 0
        elif cand == cur:
            pend, pend_n = None, 0
        else:
            # a CRASH episode only ends through the normal confirmation path
            base = "BEAR" if cur == "CRASH" else cur
            key = (base, cand)
            if pend == key:
                pend_n += 1
            else:
                pend, pend_n = key, 1
            if pend_n >= CONFIRM_DAYS:
                cur = cand
                pend, pend_n = None, 0
        states.append(cur)
    df = pd.DataFrame({
        "state": states,
        "crash_flag": flags["crash"].astype(int).to_numpy(),
        "bear_flag": flags["bear"].astype(int).to_numpy(),
        "bull_flag": flags["bull"].astype(int).to_numpy(),
        "dd60": dd60, "dd250": dd250, "vol20": vol20, "volpct": volpct,
        "ma60_slope": slope60, "ma250_slope": slope250,
        "br60": br60, "br250": br250,
        "px_csi300": px, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma250": ma250,
    }, index=dates)
    df["state_prev"] = df["state"].shift(1).fillna("NEUTRAL")
    return df.reset_index()


def summarize(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["year"] = df["date"].str[:4]
    out = {"rows": int(len(df)), "start": df["date"].iloc[0], "end": df["date"].iloc[-1]}
    out["state_counts"] = {str(k): int(v) for k, v in df["state"].value_counts().items()}
    ct = pd.crosstab(df["year"], df["state"])
    out["by_year"] = {str(y): {str(k): int(v) for k, v in row.items() if v > 0}
                      for y, row in ct.iterrows()}
    flag_cols = [c for c in ("crash_flag", "bear_flag", "bull_flag") if c in df.columns]
    if flag_cols:
        out["flag_days_by_year"] = {str(y): {c: int(v) for c, v in row.items()}
                                    for y, row in df.groupby("year")[flag_cols].sum().iterrows()}
    # state runs
    runs = []
    cur, start = None, None
    for d, s in zip(df["date"], df["state"]):
        if s != cur:
            if cur is not None:
                runs.append({"state": cur, "start": start, "end": prev})
            cur, start = s, d
        prev = d
    runs.append({"state": cur, "start": start, "end": df["date"].iloc[-1]})
    out["runs"] = runs
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2012-08-01")
    ap.add_argument("--end", default="2026-10-19")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out", default=str(OUT_DIR / "regime_v1.csv"))
    args = ap.parse_args(argv)
    try:
        df = compute_regime(data_dir=args.data_dir, start=args.start, end=args.end)
    except DataNotReadyError as exc:
        print(f"[regime] DATA NOT READY: {exc}", flush=True)
        return 2
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False, float_format="%.6f")
    summary = summarize(df)
    (OUT_DIR / "regime_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
    print(f"[regime] wrote {args.out} ({len(df)} rows)", flush=True)
    print(json.dumps(summary["state_counts"], ensure_ascii=False), flush=True)
    print("runs:", flush=True)
    for r in summary["runs"]:
        print(f"  {r['state']:8s} {r['start']} .. {r['end']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
