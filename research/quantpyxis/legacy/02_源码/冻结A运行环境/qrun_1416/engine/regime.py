"""Market-regime state machine (no lookahead).

The rule set is frozen in engine/RULES.md and must not be tuned on backtest results.

Every indicator for date T uses data up to and including the close of T-1 only
(`lag=1`); the exported label for T is therefore actionable at T's open.  The
default output is engine/out/regime_labels.csv and `run_backtest(...,
regime_labels=...)` joins it onto the NAV dates.

Public API
----------
build_regime_labels(data_dir=None, start=None, end=None, index_code="sh000300",
                    out_csv=None, lag=1, include_breadth=None, cache=True) -> DataFrame
load_regime_labels(path_or_df) -> dict[date, label]
export_regime_labels(...) -> Path

CLI:  python -m engine.regime [--data-dir data] [--start ...] [--end ...] [--out path]
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import ENGINE_VERSION
from .data import (
    DEFAULT_DATA_DIR,
    REPO_ROOT,
    DataBundle,
    DataNotReadyError,
    data_status,
    fmt_date,
    infer_index_code,
)

TRADING_DAYS = 252

# frozen thresholds (engine/RULES.md)
FULL_BULL_SCORE = 3
FULL_BEAR_SCORE = -3
PARTIAL_BULL_SCORE = 2
PARTIAL_BEAR_SCORE = -2
BREADTH_MA = 60
VOL_PCT_LOW = 40.0
VOL_PCT_HIGH = 85.0
DD_GOOD = -0.10
DD_BAD = -0.25
SLOPE_THRESHOLD = 0.02
BREADTH_GOOD = 0.60
BREADTH_BAD = 0.40
NHNL_GOOD = 1.5
NHNL_BAD = 0.667
TURNOVER_Z = 1.5
CRASH_RET20 = -0.12

OUT_DEFAULT = REPO_ROOT / "engine" / "out" / "regime_labels.csv"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _rolling_mean_np(mat: np.ndarray, n: int) -> np.ndarray:
    """NaN-aware rolling mean along axis 0 (returns NaN when fewer than n/2 valid)."""
    T, N = mat.shape
    out = np.full((T, N), np.nan, dtype=np.float32)
    if T < n:
        return out
    csum = np.zeros((T + 1, N), dtype=np.float64)
    cnt = np.zeros((T + 1, N), dtype=np.float64)
    np.cumsum(np.nan_to_num(mat, nan=0.0), axis=0, out=csum[1:])
    np.cumsum(np.isfinite(mat).astype(np.float64), axis=0, out=cnt[1:])
    win_sum = csum[n:] - csum[:-n]
    win_cnt = cnt[n:] - cnt[:-n]
    valid = win_cnt >= max(1, int(n * 0.5))
    out[n - 1:] = np.where(valid, win_sum / np.maximum(win_cnt, 1.0), np.nan)
    return out


def _rowwise_nanmean(mat: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        cnt = np.isfinite(mat).sum(axis=1)
        total = np.nansum(mat, axis=1)
        return np.where(cnt > 0, total / np.maximum(cnt, 1), np.nan)


def _zscore_rolling(series: pd.Series, window: int = 250) -> pd.Series:
    mean = series.rolling(window, min_periods=window // 2).mean()
    std = series.rolling(window, min_periods=window // 2).std(ddof=1)
    out = (series - mean) / std.replace(0.0, np.nan)
    return out


# --------------------------------------------------------------------------- #
# index indicators
# --------------------------------------------------------------------------- #

def index_indicators(index_close: pd.Series) -> pd.DataFrame:
    """Daily index-derived indicators indexed by the date the data is known at.

    All columns are computed from the close of the same row only.  build_regime_labels
    applies the 1-day lag when it turns these into tradable labels.
    """
    close = index_close.astype(float).dropna()
    close = close[~close.index.duplicated(keep="last")].sort_index()
    df = pd.DataFrame({"close": close})
    ret1 = close.pct_change(fill_method=None)
    for n in (20, 60, 120, 250):
        df[f"ma{n}"] = close.rolling(n, min_periods=n).mean()
    df["ma20_slope_20"] = df["ma20"] / df["ma20"].shift(20) - 1.0
    df["ma60_slope_20"] = df["ma60"] / df["ma60"].shift(20) - 1.0
    df["dd250"] = close / close.rolling(250, min_periods=250).max() - 1.0
    df["ret20"] = close / close.shift(20) - 1.0
    df["ret60"] = close / close.shift(60) - 1.0
    df["vol20"] = ret1.rolling(20, min_periods=15).std(ddof=1) * np.sqrt(TRADING_DAYS)
    df["vol_pct"] = _rolling_pct_rank(df["vol20"], 250)

    # component scores (frozen thresholds). NaN where the lookback is insufficient so
    # that the label is 'unknown' rather than a silent neutral.
    trend = np.full(len(df), np.nan)
    valid_t = df["ma120"].notna() & df["ma60"].notna()
    up = valid_t & (close > df["ma120"]) & (df["ma20"] > df["ma60"])
    down = valid_t & (close < df["ma120"]) & (df["ma20"] < df["ma60"])
    trend[valid_t] = 0.0
    trend[up] = 1.0
    trend[down] = -1.0
    df["trend"] = trend
    slope = np.full(len(df), np.nan)
    valid_s = df["ma60_slope_20"].notna()
    slope[valid_s] = 0.0
    slope[valid_s & (df["ma60_slope_20"] > SLOPE_THRESHOLD)] = 1.0
    slope[valid_s & (df["ma60_slope_20"] < -SLOPE_THRESHOLD)] = -1.0
    df["slope"] = slope
    dd = np.full(len(df), np.nan)
    valid_d = df["dd250"].notna()
    dd[valid_d] = 0.0
    dd[valid_d & (df["dd250"] > DD_GOOD)] = 1.0
    dd[valid_d & (df["dd250"] < DD_BAD)] = -1.0
    df["dd250_score"] = dd
    vol = np.full(len(df), np.nan)
    valid_v = df["vol_pct"].notna()
    vol[valid_v] = 0.0
    vol[valid_v & (df["vol_pct"] < VOL_PCT_LOW)] = 1.0
    vol[valid_v & (df["vol_pct"] > VOL_PCT_HIGH)] = -1.0
    df["vol_regime"] = vol
    return df


def _rolling_pct_rank(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank (0-100) of the latest value inside a trailing window."""
    arr = series.to_numpy(dtype=float)
    n = len(arr)
    out = np.full(n, np.nan, dtype=float)
    for i in range(window - 1, n):
        v = arr[i]
        win = arr[i - window + 1: i + 1]
        win = win[np.isfinite(win)]
        if not np.isfinite(v) or len(win) < window // 2:
            continue
        out[i] = 100.0 * float((win <= v).mean())
    return pd.Series(out, index=series.index)


# --------------------------------------------------------------------------- #
# cross-sectional (breadth / turnover / equal weight) indicators
# --------------------------------------------------------------------------- #

def breadth_indicators(bundle: DataBundle, index_close: pd.Series) -> pd.DataFrame:
    """Breadth, new-high/low and turnover-z series aligned to bundle.dates.

    Uses qfq close for MA comparisons (total-return consistent) and raw `turn` for
    participation.  Each row uses the close of that row only (lag applied later).
    """
    panel = bundle.panel
    T = len(bundle.dates)
    N = len(panel.codes)
    out = pd.DataFrame(index=pd.Index([str(d) for d in bundle.dates], name="date"))
    if N == 0:
        return out
    qfq = panel.fields["qfq_close"]
    raw_close = panel.fields["close"]
    turn = panel.fields["turn"]
    ts = panel.fields["tradestatus"]

    above20 = np.zeros(T); above60 = np.zeros(T); above250 = np.zeros(T)
    cnt20 = np.zeros(T); cnt60 = np.zeros(T); cnt250 = np.zeros(T)
    nh = np.zeros(T); nl = np.zeros(T); n_valid_extreme = np.zeros(T)
    up = np.zeros(T); cnt_up = np.zeros(T)
    ew_sum = np.zeros(T); ew_cnt = np.zeros(T)

    chunk = 256
    for c0 in range(0, N, chunk):
        c1 = min(N, c0 + chunk)
        sub = pd.DataFrame(qfq[:, c0:c1], index=out.index)
        tradable = pd.DataFrame(ts[:, c0:c1], index=out.index) >= 0.5
        live = sub.notna() & tradable
        for n, above, cnt in ((20, above20, cnt20), (60, above60, cnt60),
                              (250, above250, cnt250)):
            ma = sub.rolling(n, min_periods=n).mean()
            valid = live & ma.notna()
            above += ((sub > ma) & valid).sum(axis=1).to_numpy()
            cnt += valid.sum(axis=1).to_numpy()
        hh = sub.rolling(250, min_periods=250).max()
        ll = sub.rolling(250, min_periods=250).min()
        valid_e = live & hh.notna() & ll.notna()
        nh += ((sub >= hh) & valid_e).sum(axis=1).to_numpy()
        nl += ((sub <= ll) & valid_e).sum(axis=1).to_numpy()
        n_valid_extreme += valid_e.sum(axis=1).to_numpy()
        ret = sub.pct_change(fill_method=None)
        up += ((ret > 0) & ret.notna() & tradable).sum(axis=1).to_numpy()
        cnt_up += (ret.notna() & tradable).sum(axis=1).to_numpy()
        ew_sum += ret.where(tradable).sum(axis=1).fillna(0.0).to_numpy()
        ew_cnt += (ret.notna() & tradable).sum(axis=1).to_numpy()

    with np.errstate(invalid="ignore", divide="ignore"):
        out["pct_above_ma20"] = np.where(cnt20 > 0, above20 / np.maximum(cnt20, 1), np.nan)
        out["pct_above_ma60"] = np.where(cnt60 > 0, above60 / np.maximum(cnt60, 1), np.nan)
        out["pct_above_ma250"] = np.where(cnt250 > 0, above250 / np.maximum(cnt250, 1), np.nan)
        out["pct_up"] = np.where(cnt_up > 0, up / np.maximum(cnt_up, 1), np.nan)
        out["nh_count"] = nh
        out["nl_count"] = nl
        out["nh_nl_ratio"] = (nh + 1.0) / (nl + 1.0)
        out["n_stocks"] = cnt20
    with np.errstate(invalid="ignore", divide="ignore"):
        ew_ret = np.where(ew_cnt > 0, ew_sum / np.maximum(ew_cnt, 1), 0.0)
    out["equal_weight_index"] = np.cumprod(1.0 + ew_ret) * 1000.0
    out.loc[out["n_stocks"] <= 0, "equal_weight_index"] = np.nan

    # market turnover = equal-weighted mean of raw turn over names that traded
    turn_mean = np.full(T, np.nan)
    for c0 in range(0, N, chunk):
        c1 = min(N, c0 + chunk)
        tsub = turn[:, c0:c1]
        live = (ts[:, c0:c1] >= 0.5) & np.isfinite(tsub)
        tsum = np.nansum(np.where(live, tsub, 0.0), axis=1)
        tcnt = live.sum(axis=1)
        good = tcnt > 0
        these = np.where(good, tsum / np.maximum(tcnt, 1), np.nan)
        if c0 == 0:
            turn_mean = these
        else:
            both = np.isfinite(turn_mean) & np.isfinite(these)
            turn_mean[both] = (turn_mean[both] + these[both]) / 2.0
            turn_mean[~np.isfinite(turn_mean)] = these[~np.isfinite(turn_mean)]
    out["market_turnover"] = turn_mean
    out["turnover_z"] = _zscore_rolling(pd.Series(turn_mean, index=out.index), 250).to_numpy()

    # frozen breadth / nhnl / turnover scores
    b = np.zeros(T)
    b[out["pct_above_ma60"].to_numpy() > BREADTH_GOOD] = 1.0
    b[out["pct_above_ma60"].to_numpy() < BREADTH_BAD] = -1.0
    out["breadth"] = b
    nhnl = np.zeros(T)
    r = out["nh_nl_ratio"].to_numpy()
    nhnl[r > NHNL_GOOD] = 1.0
    nhnl[r < NHNL_BAD] = -1.0
    out["nhnl"] = nhnl
    tscore = np.zeros(T)
    z = out["turnover_z"].to_numpy()
    ret20 = index_close.reindex(out.index).pct_change(20, fill_method=None).to_numpy()
    tscore[(z > TURNOVER_Z) & (ret20 > 0)] = 1.0
    tscore[(z < -TURNOVER_Z) & (ret20 < 0)] = -1.0
    out["turnover"] = tscore
    return out


# --------------------------------------------------------------------------- #
# classification + label builder
# --------------------------------------------------------------------------- #

def classify_frame(ind: pd.DataFrame, variant: str):
    """Frozen classification from indicator columns (engine/RULES.md)."""
    idx = ind.index
    base = ind[["trend", "slope", "dd250_score", "vol_regime"]].sum(axis=1, skipna=False)
    if variant == "full":
        extra = ind[["breadth", "nhnl", "turnover"]].fillna(0.0).sum(axis=1)
        score = base + extra
        bull_t, bear_t = FULL_BULL_SCORE, FULL_BEAR_SCORE
    else:
        score = base
        bull_t, bear_t = PARTIAL_BULL_SCORE, PARTIAL_BEAR_SCORE
    reg = pd.Series("neutral", index=idx, dtype=object)
    reg[score >= bull_t] = "bull"
    reg[score <= bear_t] = "bear"
    reg[ind["ret20"] <= CRASH_RET20] = "bear"
    reg[score.isna()] = "unknown"
    return reg, score


def build_regime_labels(data_dir=None, start: str | None = "2014-01-01",
                        end: str | None = None, index_code: str = "sh000300",
                        out_csv=None, lag: int = 1, include_breadth: bool | None = None,
                        warmup_days: int = 420, verbose: bool = False) -> pd.DataFrame:
    """Build the daily regime label table.

    Parameters
    ----------
    data_dir : path, default <repo>/data
    start, end : 'YYYY-MM-DD' window for the exported labels (indicators need warmup).
    index_code : 'sh000300' (default) or another published index.
    out_csv : when given (or when called from the CLI with no path) the table is written.
    lag : 1 (frozen default) = label for T uses data <= T-1 close.
    include_breadth : None = auto (use the panel when published), True/False force.
    """
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    status = data_status(data_dir)
    if not status["index_ready"]:
        raise DataNotReadyError(
            f"index panel not published ({data_dir / 'panel' / 'index_daily.csv.gz'} missing); "
            "regime labels need at least the index daily file")
    panel_ready = bool(status["panel_ready"])
    if include_breadth is None:
        include_breadth = panel_ready
    if include_breadth and not panel_ready:
        warnings.warn("stock panel not published: falling back to the index-only rule variant",
                      RuntimeWarning)
        include_breadth = False
    bundle = DataBundle.load(data_dir=data_dir, start=start, end=end,
                             warmup_days=warmup_days, require_panel=include_breadth,
                             verbose=verbose)
    idx_series = None
    for candidate in (index_code, "sh000300", "sh000001"):
        want = infer_index_code(candidate)
        if want in bundle.index_dict:
            idx_series = bundle.index_close_series(want)
            index_code = want
            break
    if idx_series is None:
        raise DataNotReadyError(f"none of the requested indices are published: {sorted(bundle.index_dict)}")
    idx_close = idx_series.reindex(pd.Index([str(d) for d in bundle.dates])).ffill()
    ind = index_indicators(idx_close)
    ind = ind.reindex(pd.Index([str(d) for d in bundle.dates], name="date"))
    ind.index.name = "date"
    if verbose:
        print(f"[regime] index {index_code}: {len(ind)} rows, "
              f"{ind.dropna(subset=['vol_pct']).shape[0]} enough history")
    variant = "index_only"
    if include_breadth:
        br = breadth_indicators(bundle, idx_close)
        ind = ind.join(br, how="left")
        variant = "full"
    regime, score = classify_frame(ind, variant)
    out = ind.copy()
    out["regime_raw"] = regime
    out["score_raw"] = score
    out["variant"] = variant
    # apply lag: label for date T uses indicators through T-1
    out["regime"] = regime.shift(lag)
    out["score"] = score.shift(lag)
    out["label_date"] = out.index
    out["regime"] = out["regime"].fillna("unknown")
    out["score"] = out["score"].astype(float)
    out = out.reset_index()
    out["date"] = out["date"].map(fmt_date)
    out["label_date"] = out["label_date"].map(fmt_date)
    cols = ["date", "regime", "variant", "score", "label_date",
            "trend", "slope", "dd250_score", "vol_regime", "breadth", "nhnl", "turnover",
            "close", "ma20", "ma60", "ma120", "ma250", "ma20_slope_20", "ma60_slope_20",
            "dd250", "ret20", "ret60", "vol20", "vol_pct",
            "pct_above_ma20", "pct_above_ma60", "pct_above_ma250", "pct_up",
            "nh_count", "nl_count", "nh_nl_ratio", "market_turnover", "turnover_z",
            "equal_weight_index", "n_stocks"]
    cols = [c for c in cols if c in out.columns]
    out = out[cols]
    # trim the warmup rows to the requested window
    if start is not None:
        out = out[out["date"] >= fmt_date(start)]
    if end is not None:
        out = out[out["date"] <= fmt_date(end)]
    out = out.reset_index(drop=True)
    if out_csv is not None:
        out_csv = Path(out_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_csv, index=False)
        if verbose:
            print(f"[regime] wrote {len(out)} rows to {out_csv}")
    return out


def load_regime_labels(path_or_obj) -> dict:
    """Load a labels CSV / DataFrame / Series / dict into {date: label}."""
    if isinstance(path_or_obj, dict):
        return {fmt_date(k): str(v) for k, v in path_or_obj.items() if v is not None}
    if isinstance(path_or_obj, pd.Series):
        return {fmt_date(k): str(v) for k, v in path_or_obj.items() if pd.notna(v)}
    if isinstance(path_or_obj, pd.DataFrame):
        df = path_or_obj
        cols = {c.lower(): c for c in df.columns}
        if "date" not in cols or "regime" not in cols:
            raise ValueError("labels table needs 'date' and 'regime' columns")
        return {fmt_date(d): str(r) for d, r in zip(df[cols["date"]], df[cols["regime"]])
                if pd.notna(r)}
    path = Path(path_or_obj)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return load_regime_labels(pd.read_csv(path, dtype={"date": str}))


def export_regime_labels(data_dir=None, start: str | None = "2014-01-01",
                         end: str | None = None, index_code: str = "sh000300",
                         out: str | Path | None = None, **kwargs) -> Path:
    out = Path(out) if out is not None else OUT_DEFAULT
    labels = build_regime_labels(data_dir=data_dir, start=start, end=end,
                                 index_code=index_code, out_csv=out, **kwargs)
    return out


def yearly_counts(labels: pd.DataFrame) -> pd.DataFrame:
    """Per-year regime counts (for validation / reporting)."""
    if labels is None or len(labels) == 0:
        return pd.DataFrame()
    df = labels.copy()
    df["year"] = df["date"].str[:4]
    counts = df.pivot_table(index="year", columns="regime", values="date",
                            aggfunc="count", fill_value=0)
    for col in ("bull", "bear", "neutral", "unknown"):
        if col not in counts.columns:
            counts[col] = 0
    counts = counts[["bull", "bear", "neutral", "unknown"]]
    counts["total"] = counts.sum(axis=1)
    for col in ("bull", "bear", "neutral"):
        counts[f"{col}_pct"] = (counts[col] / counts["total"].replace(0, np.nan)).round(4)
    return counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description="Build no-lookahead market regime labels")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--start", default="2014-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--index-code", default="sh000300")
    parser.add_argument("--out", default=str(OUT_DEFAULT))
    parser.add_argument("--include-breadth", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    inc = {"auto": None, "yes": True, "no": False}[args.include_breadth]
    labels = build_regime_labels(data_dir=args.data_dir, start=args.start, end=args.end,
                                 index_code=args.index_code, out_csv=args.out,
                                 include_breadth=inc, verbose=args.verbose)
    counts = yearly_counts(labels)
    print(f"labels: {len(labels)} rows, {labels['date'].min()} .. {labels['date'].max()}, "
          f"variant={labels['variant'].iloc[0] if len(labels) else 'n/a'}")
    print(counts.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
