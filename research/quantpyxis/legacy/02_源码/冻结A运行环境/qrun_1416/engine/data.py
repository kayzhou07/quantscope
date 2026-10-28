"""Data access layer for the qrun_1416 A-share backtester.

Reads the frozen data contract v1 published by data-runner (read-only):

    data/meta/calendar.csv        date,is_open
    data/meta/universe.csv        code,exchange,board,name,ipo_date,out_date,status
    data/meta/codes.csv           code,id                       (optional but used for code order)
    data/meta/manifest.json       publication status / sha256    (read for data version)
    data/panel/bars_{year}.csv.gz        raw, unadjusted daily bars
    data/panel/bars_qfq_{year}.csv.gz    forward-adjusted daily bars
    data/panel/index_daily.csv.gz        index_code,date,open,high,low,close,preclose,volume,amount

The loader pivots the long bar files into (T x N) float32 matrices, one matrix per
field, and memory-maps them through an on-disk cache so repeated backtests start
in a few seconds (see engine/precompute.py).  All matrices share the same row
axis (bundle.dates, trading days) and column axis (bundle.codes).

Field names used by the engine / strategies:
    open, high, low, close, preclose, volume, amount, turn, pctchg,
    isst, tradestatus, qfq_open, qfq_close, qfq_high, qfq_low
NaN means "no bar that day" (suspended / not listed / not yet listed).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import ENGINE_VERSION

# --------------------------------------------------------------------------- #
# paths / constants
# --------------------------------------------------------------------------- #

ENGINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ENGINE_DIR.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_CACHE_DIR = REPO_ROOT / "tmp" / "engine-dev" / "cache"


class DataNotReadyError(RuntimeError):
    """Raised when the frozen data contract files required for a run are absent."""


RAW_FIELDS = [
    "open", "high", "low", "close", "preclose", "volume", "amount",
    "turn", "pctchg", "isst", "tradestatus",
]
QFQ_FIELDS = ["qfq_open", "qfq_close", "qfq_high", "qfq_low"]
ALL_FIELDS = RAW_FIELDS + QFQ_FIELDS

PCT_CHG_DATE = "2023-08-28"  # stamp-tax switch, kept here for documentation only

_CODE_RE = re.compile(r"(\d{6})")


def normalize_code(value) -> str:
    """'sh600000' / '600000.SH' / 600000 -> '600000' (6-digit string)."""
    if value is None:
        return ""
    if isinstance(value, (int, np.integer)):
        return f"{int(value):06d}"
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    m = _CODE_RE.search(s)
    if m:
        return m.group(1)
    return s


def infer_index_code(value) -> str:
    """Normalize an index code to the contract 'shXXXXXX' / 'szXXXXXX' form."""
    s = str(value).strip().lower()
    if s.startswith(("sh", "sz")):
        digits = _CODE_RE.search(s)
        return ("sh" if s.startswith("sh") else "sz") + (digits.group(1) if digits else "")
    m = _CODE_RE.search(s)
    if not m:
        return s
    digits = m.group(1)
    # stock-index conventions: 000xxx/880xxx are Shanghai, 399xxx are Shenzhen.
    return ("sz" if digits.startswith("399") else "sh") + digits


def fmt_date(value) -> str:
    """Normalize a date-like value to 'YYYY-MM-DD'."""
    if isinstance(value, str):
        s = value.strip()
        if len(s) >= 10:
            return s[:10]
        return s
    ts = pd.Timestamp(value)
    return ts.strftime("%Y-%m-%d")


def _read_csv_any(path_stem: Path, **kwargs) -> pd.DataFrame:
    """Read path_stem + '.csv.gz' if it exists, else path_stem + '.csv'."""
    gz = Path(str(path_stem) + ".csv.gz")
    plain = Path(str(path_stem) + ".csv")
    if gz.exists():
        return pd.read_csv(gz, **kwargs)
    if plain.exists():
        return pd.read_csv(plain, **kwargs)
    raise FileNotFoundError(str(gz))


def _path_exists_any(path_stem: Path) -> bool:
    return Path(str(path_stem) + ".csv.gz").exists() or Path(str(path_stem) + ".csv").exists()


def _panel_files(data_dir: Path, field_prefix: str, years):
    """Return {year: path} for bars files in the requested year range.

    Exact match on `{prefix}_{year}.csv` / `.csv.gz` so that the raw prefix `bars`
    never picks up `bars_qfq_*` files.
    """
    out = {}
    pat = re.compile(r"^" + re.escape(field_prefix) + r"_(\d{4})\.csv(?:\.gz)?$")
    for p in sorted(glob.glob(str(Path(data_dir) / "panel" / f"{field_prefix}_*.csv*"))):
        m = pat.match(Path(p).name)
        if not m:
            continue
        year = int(m.group(1))
        if year in years:
            out[year] = Path(p)
    return out


# --------------------------------------------------------------------------- #
# data status
# --------------------------------------------------------------------------- #

def data_status(data_dir=None) -> dict:
    """Inspect which parts of the contract are published. Never raises."""
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    status = {"data_dir": str(data_dir), "exists": data_dir.exists()}

    def _find(stem: Path) -> dict:
        for suffix in (".csv.gz", ".csv"):
            p = Path(str(stem) + suffix)
            if p.exists():
                return {"path": str(p), "exists": True, "size": p.stat().st_size}
        return {"path": str(stem) + ".csv.gz", "exists": False, "size": 0}

    for key, stem in {
        "calendar": data_dir / "meta" / "calendar",
        "universe": data_dir / "meta" / "universe",
        "codes": data_dir / "meta" / "codes",
        "index_daily": data_dir / "panel" / "index_daily",
        "index_qfq": data_dir / "panel" / "index_qfq",
    }.items():
        status[key] = _find(stem)
    mpath = data_dir / "meta" / "manifest.json"
    status["manifest"] = {"path": str(mpath), "exists": mpath.exists(),
                          "size": mpath.stat().st_size if mpath.exists() else 0}
    raw_years, qfq_years = [], []
    if (data_dir / "panel").exists():
        pat_raw = re.compile(r"^bars_(\d{4})\.csv(?:\.gz)?$")
        pat_qfq = re.compile(r"^bars_qfq_(\d{4})\.csv(?:\.gz)?$")
        for p in glob.glob(str(data_dir / "panel" / "*.csv*")):
            name = Path(p).name
            m = pat_raw.match(name)
            if m:
                raw_years.append(int(m.group(1)))
                continue
            m = pat_qfq.match(name)
            if m:
                qfq_years.append(int(m.group(1)))
    status["raw_years"] = sorted(set(raw_years))
    status["qfq_years"] = sorted(set(qfq_years))
    status["panel_ready"] = bool(status["raw_years"])
    status["qfq_ready"] = bool(status["qfq_years"])
    status["index_ready"] = status["index_daily"]["exists"]
    status["stock_panel_ready"] = status["panel_ready"] and status["index_ready"]
    return status


# --------------------------------------------------------------------------- #
# calendar / universe / index
# --------------------------------------------------------------------------- #

class Calendar:
    """Trading-day calendar from data/meta/calendar.csv (or panel fallback)."""

    def __init__(self, dates):
        self.dates = np.asarray([fmt_date(d) for d in dates], dtype="U10")
        self.dates = np.unique(self.dates)
        self.index = {d: i for i, d in enumerate(self.dates)}

    def __len__(self):
        return len(self.dates)

    def __contains__(self, date) -> bool:
        return fmt_date(date) in self.index

    def index_of(self, date) -> int:
        return self.index[fmt_date(date)]

    def between(self, start=None, end=None) -> np.ndarray:
        lo = 0 if start is None else int(np.searchsorted(self.dates, fmt_date(start), "left"))
        hi = len(self.dates) if end is None else int(np.searchsorted(self.dates, fmt_date(end), "right"))
        return self.dates[lo:hi]

    @classmethod
    def load(cls, data_dir: Path) -> "Calendar":
        path = Path(data_dir) / "meta" / "calendar.csv"
        if path.exists():
            df = pd.read_csv(path, dtype=str, encoding='utf-8-sig')
            cols = {c.lower(): c for c in df.columns}
            date_col = cols.get("date", df.columns[0])
            df[date_col] = df[date_col].map(fmt_date)
            if "is_open" in cols:
                mask = df[cols["is_open"]].astype(str).str.strip().isin(["1", "1.0", "True", "true"])
                dates = df.loc[mask, date_col].tolist()
            else:
                dates = df[date_col].tolist()
            if dates:
                return cls(dates)
        # fallback: derive open days from whatever bar files exist
        dates = set()
        for p in sorted(glob.glob(str(Path(data_dir) / "panel" / "bars_*.csv*"))):
            try:
                df = pd.read_csv(p, usecols=lambda c: c in ("date",), encoding="utf-8-sig")
                dates.update(df["date"].map(fmt_date).tolist())
            except Exception:
                continue
        if not dates:
            raise DataNotReadyError("no calendar available: data/meta/calendar.csv missing")
        warnings.warn("calendar.csv missing; derived trading days from bar files", RuntimeWarning)
        return cls(sorted(dates))


class UniverseStore:
    """Listing metadata (ipo/out dates, board) and derived masks.

    Convention: a stock is *listed* from ipo_date (inclusive) and delist processing
    starts on the first trading day >= out_date (if out_date present).
    """

    BOARDS = ("main", "chinext")

    def __init__(self, dates: np.ndarray, codes: np.ndarray, meta_df: pd.DataFrame | None):
        self.dates = dates
        self.codes = codes
        self.n = len(codes)
        self._code_index = {c: i for i, c in enumerate(codes)}
        self.ipo = np.array(["1900-01-01"] * self.n, dtype="U10")
        self.out = np.array([""] * self.n, dtype="U10")
        self.board = np.array(["main"] * self.n, dtype="U10")
        self.status = np.array([""] * self.n, dtype="U10")
        self._listed = None
        self.known = np.zeros(self.n, dtype=bool)
        if meta_df is not None and len(meta_df):
            cols = {c.lower(): c for c in meta_df.columns}
            c_code = cols.get("code", meta_df.columns[0])
            for _, row in meta_df.iterrows():
                code = normalize_code(row[c_code])
                j = self._code_index.get(code)
                if j is None:
                    continue
                self.known[j] = True
                ipo = row.get(cols.get("ipo_date", "_missing"), None) if "ipo_date" in cols else None
                if ipo is not None and str(ipo).strip() not in ("", "nan", "None", "NaT"):
                    try:
                        self.ipo[j] = fmt_date(ipo)
                    except Exception:
                        pass
                out = row.get(cols.get("out_date", "_missing"), None) if "out_date" in cols else None
                if out is not None and str(out).strip() not in ("", "nan", "None", "NaT"):
                    try:
                        self.out[j] = fmt_date(out)
                    except Exception:
                        pass
                board = row.get(cols.get("board", "_missing"), None) if "board" in cols else None
                if board is not None and str(board).strip().lower() in self.BOARDS:
                    self.board[j] = str(board).strip().lower()
                status = row.get(cols.get("status", "_missing"), None) if "status" in cols else None
                if status is not None:
                    self.status[j] = str(status).strip().lower()
        # infer board from code when metadata is missing/unknown
        for j, code in enumerate(codes):
            if not self.known[j] or self.board[j] not in self.BOARDS:
                self.board[j] = board_of(code)
        self.ipo_is = np.searchsorted(dates, self.ipo, "left")  # first row >= ipo date
        self.out_is = np.where(
            self.out == "", len(dates),
            np.searchsorted(dates, self.out, "left"),
        )

    def listed_mask(self) -> np.ndarray:
        if self._listed is None:
            T = len(self.dates)
            mask = np.zeros((T, self.n), dtype=bool)
            for j in range(self.n):
                a, b = int(self.ipo_is[j]), int(self.out_is[j])
                a = max(0, min(a, T))
                b = max(0, min(b, T))
                if b > a:
                    mask[a:b, j] = True
            self._listed = mask
        return self._listed

    def out_date(self, j: int) -> str:
        return str(self.out[j])


def board_of(code: str) -> str:
    """Board classification from a 6-digit code: chinext = 300/301, else main."""
    code = normalize_code(code)
    if code.startswith("300") or code.startswith("301"):
        return "chinext"
    return "main"


# --------------------------------------------------------------------------- #
# panel store
# --------------------------------------------------------------------------- #

class PanelStore:
    """(T x N) field matrices over shared dates/codes axes; may be memmap-backed."""

    def __init__(self, fields: dict, dates: np.ndarray, codes: np.ndarray, qfq_available: bool,
                 warnings_list=None):
        self.fields = fields
        self.dates = dates
        self.codes = codes
        self.qfq_available = qfq_available
        self.warnings = list(warnings_list or [])
        self.n = len(codes)
        self.T = len(dates)

    def field(self, name: str) -> np.ndarray:
        try:
            return self.fields[name]
        except KeyError:
            raise KeyError(f"unknown panel field {name!r}; available: {sorted(self.fields)}") from None

    def row(self, name: str, i: int) -> np.ndarray:
        return self.fields[name][i]

    def history(self, name: str, i: int, window: int, cols=None) -> np.ndarray:
        """Rows [i-window+1 .. i] (clipped at 0) for the requested columns."""
        lo = max(0, i - window + 1)
        mat = self.fields[name][lo:i + 1]
        if cols is None:
            return mat
        return mat[:, cols]


# --------------------------------------------------------------------------- #
# cache build / load
# --------------------------------------------------------------------------- #

def _normalized_code_series(code_series: pd.Series) -> pd.Series:
    """Vectorized code normalization (fast path for the contract's 6-digit strings)."""
    s = code_series.astype(str).str.strip()
    try:
        if bool((s.str.len() == 6).all()) and bool(s.str.isdigit().all()):
            return s
    except Exception:  # noqa: BLE001 - object dtype edge cases
        pass
    uniq = pd.unique(s)
    mapping = {u: normalize_code(u) for u in uniq}
    return s.map(mapping)


def _normalized_date_series(date_series: pd.Series) -> pd.Series:
    s = date_series.astype(str).str.strip()
    try:
        if bool((s.str.len() == 10).all()):
            return s.str[:10]
    except Exception:  # noqa: BLE001
        pass
    uniq = pd.unique(s)
    mapping = {u: fmt_date(u) for u in uniq}
    return s.map(mapping)


def _frame_row_col(df: pd.DataFrame, date_index: dict, code_index: dict) -> tuple:
    """Attach __date/__code and return (rows, cols, keep_mask) index arrays."""
    df["__date"] = _normalized_date_series(df["date"])
    df["__code"] = _normalized_code_series(df["code"])
    rows = df["__date"].map(date_index).fillna(-1).to_numpy(dtype=np.int32)
    cols = df["__code"].map(code_index).fillna(-1).to_numpy(dtype=np.int32)
    keep = (rows >= 0) & (cols >= 0)
    return rows, cols, keep


def _codes_from_files(files: dict) -> np.ndarray:
    seen = set()
    for p in files.values():
        try:
            df = pd.read_csv(p, usecols=lambda c: c == "code", dtype={"code": str},
                             encoding="utf-8-sig")
        except ValueError:
            df = pd.read_csv(p, dtype={"code": str}, encoding="utf-8-sig")
            df = df[[df.columns[0]]]
            df.columns = ["code"]
        uniq = pd.unique(_normalized_code_series(df["code"]))
        seen.update(str(c) for c in uniq)
    seen.discard("")
    seen.discard("nan")
    return np.array(sorted(seen), dtype="U8")


def _cache_signature(data_dir: Path, years, fields, load_dates) -> str:
    payload = {
        "engine": ENGINE_VERSION,
        "panel_build": 3,
        "data_dir": str(Path(data_dir).resolve()),
        "fields": list(fields),
        "years": list(years),
        "first": load_dates[0],
        "last": load_dates[-1],
        "sources": [],
    }
    for p in sorted(glob.glob(str(Path(data_dir) / "panel" / "bars_*.csv*")) +
                    glob.glob(str(Path(data_dir) / "panel" / "bars_qfq_*.csv*"))):
        try:
            st = Path(p).stat()
            payload["sources"].append([Path(p).name, st.st_size, int(st.st_mtime)])
        except OSError:
            payload["sources"].append([Path(p).name, -1, -1])
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _pivot_year(df: pd.DataFrame, field_name: str, src_col: str):
    sub = df[["__date", "__code", src_col]].dropna(subset=["__date", "__code"])
    sub = sub.drop_duplicates(subset=["__date", "__code"], keep="last")
    if sub.empty:
        return None
    piv = sub.pivot(index="__date", columns="__code", values=src_col)
    return piv


def _build_panel(data_dir: Path, years, load_dates: np.ndarray, out_dir: Path | None,
                 verbose: bool = False) -> tuple:
    """Build all field matrices; returns (fields dict, codes, qfq_available, warnings)."""
    raw_files = _panel_files(data_dir, "bars", set(years))
    qfq_files = _panel_files(data_dir, "bars_qfq", set(years))
    if not raw_files:
        raise DataNotReadyError(
            f"no stock panel files found under {Path(data_dir) / 'panel'} for years {sorted(years)}")
    codes = _codes_from_files(raw_files)
    T, N = len(load_dates), len(codes)
    if T == 0 or N == 0:
        raise DataNotReadyError("empty panel date/code axes")
    date_index = {d: k for k, d in enumerate(load_dates)}
    code_index = {c: k for k, c in enumerate(codes)}

    if out_dir is not None:
        try:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            fields = {name: np.lib.format.open_memmap(
                str(out_dir / f"{name}.npy"), mode="w+", dtype=np.float32, shape=(T, N))
                for name in ALL_FIELDS}
            for arr in fields.values():
                arr[:] = np.nan
            np.save(str(out_dir / "dates.npy"), load_dates)
            np.save(str(out_dir / "codes.npy"), codes)
        except Exception as exc:  # disk/permission problem -> fall back to RAM
            warnings.warn(f"panel cache unavailable ({exc!r}); building in RAM", RuntimeWarning)
            out_dir = None
    if out_dir is None:
        fields = {name: np.full((T, N), np.nan, dtype=np.float32) for name in ALL_FIELDS}

    warnings_list = []
    seen_raw_cols = set()
    # ---- fill raw fields year by year -------------------------------------
    for year in sorted(raw_files):
        path = raw_files[year]
        header = pd.read_csv(path, nrows=0, encoding='utf-8-sig')
        cols = {c.lower(): c for c in header.columns}
        usecols = ["code", "date"] + [c for c in cols if c in ("open", "high", "low", "close",
                                                               "preclose", "volume", "amount",
                                                               "turn", "pctchg", "isst",
                                                               "tradestatus")]
        dtype = {"code": str, "date": str}
        df = pd.read_csv(path, usecols=lambda c: c.lower() in [u.lower() for u in usecols],
                         dtype=dtype, encoding="utf-8-sig")
        ren = {c: c.lower().strip() for c in df.columns}
        df = df.rename(columns=ren)
        seen_raw_cols.update(df.columns)
        if "code" not in df.columns or "date" not in df.columns:
            raise DataNotReadyError(f"{path.name}: expected code/date columns, got {list(df.columns)}")
        rows, colsn, keep = _frame_row_col(df, date_index, code_index)
        if not keep.all():
            n_before = int(((rows < 0) & (colsn >= 0)).sum())
            n_unknown = int((colsn < 0).sum())
            if n_before:
                warnings_list.append(
                    f"{path.name}: {n_before} rows before the loaded window dropped")
            if n_unknown:
                warnings_list.append(
                    f"{path.name}: {n_unknown} rows whose code is outside the panel axes dropped")
        rr, cc = rows[keep], colsn[keep]
        for name in RAW_FIELDS:
            if name not in df.columns:
                if name in ("isst", "tradestatus"):
                    warnings_list.append(f"{path.name}: missing column {name}; using 0")
                continue
            vals = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=np.float32)[keep]
            fields[name][rr, cc] = vals
        if verbose:
            print(f"[panel] raw {path.name}: {len(df)} rows")
    # ---- fill qfq fields ---------------------------------------------------
    qfq_available = bool(qfq_files)
    if not qfq_available:
        warnings_list.append(
            "qfq panel missing: signals/valuation fall back to raw prices and dividends are NOT "
            "credited (degraded total-return mode)")
        for dst, src in (("qfq_open", "open"), ("qfq_close", "close"),
                         ("qfq_high", "high"), ("qfq_low", "low")):
            fields[dst][:] = fields[src][:]
    else:
        qfq_years = set(int(y) for y in qfq_files)
        for year in sorted(qfq_files):
            path = qfq_files[year]
            df = pd.read_csv(path, dtype={"code": str, "date": str}, encoding="utf-8-sig")
            ren = {c: c.lower().strip() for c in df.columns}
            df = df.rename(columns=ren)
            if "code" not in df.columns or "date" not in df.columns:
                raise DataNotReadyError(f"{path.name}: expected code/date columns")
            rows, colsn, keep = _frame_row_col(df, date_index, code_index)
            rr, cc = rows[keep], colsn[keep]
            for name, src in (("qfq_open", "open"), ("qfq_close", "close"),
                              ("qfq_high", "high"), ("qfq_low", "low")):
                if src in df.columns:
                    vals = pd.to_numeric(df[src], errors="coerce").to_numpy(dtype=np.float32)[keep]
                    fields[name][rr, cc] = vals
            if verbose:
                print(f"[panel] qfq {path.name}: {len(df)} rows")
        missing_q = sorted(set(years) - qfq_years)
        if missing_q:
            warnings_list.append(f"qfq panel missing years {missing_q}; raw fallback for those")
            for year in missing_q:
                pass  # those rows keep NaN qfq; engine falls back per-cell to raw

    # ---- post-processing (in-place; memmaps flush automatically) -----------
    if "tradestatus" not in seen_raw_cols:
        # data-provider convention "row exists => traded": derive the flag from bars
        warnings_list.append(
            "tradestatus column missing: derived from bar presence (bar exists => trading)")
        for t in range(T):
            fields["tradestatus"][t] = np.where(np.isfinite(fields["close"][t]), 1.0, 0.0)
    for t in range(T):
        ts = fields["tradestatus"][t]
        np.nan_to_num(ts, copy=False, nan=0.0)
    for t in range(T):
        np.nan_to_num(fields["isst"][t], copy=False, nan=0.0)
        np.nan_to_num(fields["volume"][t], copy=False, nan=0.0)
        np.nan_to_num(fields["amount"][t], copy=False, nan=0.0)
    # data-limitation disclosures used by strategies / the report
    if "isst" not in seen_raw_cols or not bool(np.any(fields["isst"] > 0.5)):
        warnings_list.append(
            "isst (ST) field has no positive values: ST names are not identifiable from this "
            "dataset, so the 'ST not buyable' hook cannot trigger (disclosed limitation)")
    if "turn" not in seen_raw_cols or not bool(np.isfinite(fields["turn"]).any()):
        warnings_list.append(
            "turn field is empty: regime turnover component is inert; liquidity ranking should "
            "use the `amount` proxy (disclosed limitation)")
    # preclose fallback: carry last valid raw close forward
    carry = np.full(N, np.nan, dtype=np.float32)
    for t in range(T):
        close_t = fields["close"][t]
        valid_c = np.isfinite(close_t)
        carry[valid_c] = close_t[valid_c]
        pre = fields["preclose"][t]
        bad = ~np.isfinite(pre)
        if bad.any():
            pre[bad] = carry[bad]
        if t == 0:
            # cannot know previous close on the first loaded row; use close as proxy
            pre[~np.isfinite(pre)] = np.where(np.isfinite(close_t[~np.isfinite(pre)]),
                                              close_t[~np.isfinite(pre)], np.nan)
    # qfq missing cells fall back to raw (e.g. partially published qfq years)
    for dst, src in (("qfq_open", "open"), ("qfq_close", "close"),
                     ("qfq_high", "high"), ("qfq_low", "low")):
        arr, raw = fields[dst], fields[src]
        bad = ~np.isfinite(arr)
        if bad.any():
            arr[bad] = raw[bad]
    return fields, codes, qfq_available, warnings_list


def _load_cache_fields(root: Path):
    fields = {}
    for name in ALL_FIELDS:
        p = root / f"{name}.npy"
        if not p.exists():
            return None
        fields[name] = np.load(str(p), mmap_mode="r", allow_pickle=False)
    return fields


def _cache_source_files(data_dir: Path):
    return (glob.glob(str(Path(data_dir) / "panel" / "bars_*.csv*"))
            + glob.glob(str(Path(data_dir) / "panel" / "bars_qfq_*.csv*")))


def _open_panel_cache(cache_dir: Path, sig: str, load_dates: np.ndarray):
    """Return (fields, codes, qfq_available, warnings) from the exact-signature cache."""
    root = Path(cache_dir) / f"panel_{sig}"
    meta_path = root / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("engine") != ENGINE_VERSION:
            return None
        dates = np.load(str(root / "dates.npy"), allow_pickle=False)
        if len(dates) != len(load_dates):
            return None
        if len(dates) and not np.array_equal(
                np.asarray(dates, dtype="U10"), np.asarray(load_dates, dtype="U10")):
            return None
        codes = np.load(str(root / "codes.npy"), allow_pickle=False)
        fields = _load_cache_fields(root)
        if fields is None:
            return None
        return fields, codes, bool(meta.get("qfq_available", True)), list(meta.get("warnings", []))
    except Exception:
        return None


def _open_superset_panel_cache(cache_dir: Path, data_dir: Path, load_dates: np.ndarray,
                               years) -> tuple | None:
    """Reuse an existing cache whose row axis is a superset of the requested dates.

    This makes a single precompute serve every `warmup_days` variant (e.g. a cache
    built with warmup 340 also serves warmup 320 runs) without rebuilding ~1 GB.
    Requirements: same engine version, same published year set, requested dates are a
    contiguous slice of the cached axis, and every published source file is older than
    the cache (otherwise the cache is stale and is rebuilt).
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return None
    target = [str(d) for d in load_dates]
    target_years = tuple(sorted(int(y) for y in years))
    candidates = sorted(cache_dir.glob("panel_*"),
                        key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for root in candidates:
        meta_path = root / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("engine") != ENGINE_VERSION:
                continue
            dates = np.load(str(root / "dates.npy"), allow_pickle=False)
            dstr = [str(x) for x in dates]
            if len(dstr) < len(target):
                continue
            cached_years = tuple(sorted({int(x[:4]) for x in dstr}))
            if cached_years != target_years:
                continue
            if dstr[0] > target[0] or dstr[-1] < target[-1]:
                continue
            cached_arr = np.asarray(dates, dtype="U10")
            lo = int(np.searchsorted(cached_arr, target[0], "left"))
            hi = lo + len(target)
            if hi > len(cached_arr):
                continue
            if [str(x) for x in cached_arr[lo:hi]] != target:
                continue
            created = meta.get("created")
            created_ts = pd.Timestamp(created).timestamp() if created else None
            fresh = True
            if created_ts is not None:
                for p in _cache_source_files(data_dir):
                    try:
                        if Path(p).stat().st_mtime > created_ts + 1.0:
                            fresh = False
                            break
                    except OSError:
                        fresh = False
                        break
            if not fresh:
                continue
            fields = _load_cache_fields(root)
            if fields is None:
                continue
            sliced = {name: arr[lo:hi] for name, arr in fields.items()}
            codes = np.load(str(root / "codes.npy"), allow_pickle=False)
            warns = list(meta.get("warnings", [])) + [
                f"reused superset panel cache {root.name} (cached rows {dstr[0]}..{dstr[-1]}; "
                f"sliced to requested {target[0]}..{target[-1]}); no rebuild needed"]
            return sliced, codes, bool(meta.get("qfq_available", True)), warns
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
# bundle
# --------------------------------------------------------------------------- #

class DataBundle:
    """Everything a backtest needs: dates, codes, panel matrices, universe, indices."""

    def __init__(self, data_dir, calendar: Calendar, panel: PanelStore, universe: UniverseStore,
                 index_dict: dict, index_meta: dict, load_start: str, load_end: str,
                 warnings_list=None, data_version=None, benchmark_codes=None):
        self.data_dir = Path(data_dir)
        self.calendar = calendar
        self.panel = panel
        self.universe = universe
        self.index_dict = index_dict          # {full_index_code: DataFrame indexed by date}
        self.index_meta = index_meta
        self.dates = panel.dates
        self.codes = panel.codes
        self.code_index = {c: i for i, c in enumerate(self.codes)}
        self.load_start = load_start
        self.load_end = load_end
        self.warnings = list(warnings_list or [])
        self.data_version = dict(data_version or {})
        self.benchmark_codes = list(benchmark_codes or [])
        self._index_close = {}
        self._index_open = {}
        self._date_index = {str(d): k for k, d in enumerate(self.dates)}

    # ---- axis helpers ------------------------------------------------------
    def __len__(self):
        return len(self.dates)

    def i_of(self, date) -> int:
        d = fmt_date(date)
        try:
            return self._date_index[d]
        except KeyError:
            raise KeyError(
                f"{d} is not a loaded trading day ({self.dates[0]}..{self.dates[-1]})") from None

    def date_at(self, i: int) -> str:
        return str(self.dates[i])

    def n_stocks(self) -> int:
        return len(self.codes)

    # ---- panel access ------------------------------------------------------
    def field(self, name: str) -> np.ndarray:
        return self.panel.field(name)

    def matrix(self, name: str) -> np.ndarray:
        return self.panel.field(name)

    def history(self, name: str, i: int, window: int, cols=None) -> np.ndarray:
        return self.panel.history(name, i, window, cols)

    def latest(self, name: str, i: int, cols=None) -> np.ndarray:
        arr = self.panel.fields[name][i]
        return arr if cols is None else arr[cols]

    def col_index(self, codes) -> np.ndarray:
        if codes is None:
            return np.arange(len(self.codes))
        if isinstance(codes, (str, np.str_)):
            codes = [codes]
        return np.array([self.code_index[c] for c in codes], dtype=int)

    # ---- index access ------------------------------------------------------
    def has_index(self, code: str) -> bool:
        return infer_index_code(code) in self.index_dict

    def index_frame(self, code: str) -> pd.DataFrame:
        return self.index_dict[infer_index_code(code)]

    def index_close_series(self, code: str) -> pd.Series:
        code = infer_index_code(code)
        if code not in self._index_close:
            df = self.index_dict[code]
            self._index_close[code] = df["close"].astype(float)
        return self._index_close[code]

    def index_open_series(self, code: str) -> pd.Series:
        code = infer_index_code(code)
        if code not in self._index_open:
            df = self.index_dict[code]
            self._index_open[code] = df["open"].astype(float)
        return self._index_open[code]

    def listed_mask(self) -> np.ndarray:
        return self.universe.listed_mask()

    # ---- loader ------------------------------------------------------------
    @classmethod
    def load(cls, data_dir=None, start: str | None = None, end: str | None = None,
             warmup_days: int = 320, cache_dir=None, verbose: bool = False,
             require_panel: bool = True) -> "DataBundle":
        data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        if not data_dir.exists():
            raise DataNotReadyError(f"data dir does not exist: {data_dir}")
        calendar = Calendar.load(data_dir)
        index_dict, index_meta = _load_indices(data_dir)
        bench = [c for c in ("sh000300", "sh000001", "sz399006", "sz399001",
                             "sh000905", "sh000852", "sh000985")
                 if c in index_dict]

        if start is None:
            start = str(calendar.dates[0])
        if end is None:
            end = str(calendar.dates[-1])
        start, end = fmt_date(start), fmt_date(end)
        start_i = int(np.searchsorted(calendar.dates, start, "left"))
        end_i = int(np.searchsorted(calendar.dates, end, "right")) - 1
        if start_i >= len(calendar.dates) or end_i < start_i:
            raise DataNotReadyError(f"date range {start}..{end} has no trading days in calendar")
        load_i = max(0, start_i - int(warmup_days))
        load_dates = calendar.dates[load_i:end_i + 1]

        years = set(range(int(load_dates[0][:4]), int(load_dates[-1][:4]) + 1))
        raw_files = _panel_files(data_dir, "bars", years)
        if not raw_files:
            if require_panel:
                raise DataNotReadyError(
                    "stock panel not published yet (no data/panel/bars_YYYY.csv.gz for "
                    f"years {sorted(years)}). Use require_panel=False for index-only runs.")
            fields = {name: np.full((len(load_dates), 0), np.nan, dtype=np.float32)
                      for name in ALL_FIELDS}
            panel = PanelStore(fields, load_dates, np.array([], dtype="U8"), False,
                               ["stock panel not loaded (index-only mode)"])
        else:
            # if the warmup window reaches before the first published year, clamp it
            avail_years = sorted(raw_files)
            if int(load_dates[0][:4]) < avail_years[0]:
                first_avail = f"{avail_years[0]}-01-01"
                j = int(np.searchsorted(calendar.dates, first_avail, "left"))
                load_i = max(load_i, j)
                load_dates = calendar.dates[load_i:end_i + 1]
                years = set(range(int(load_dates[0][:4]), int(load_dates[-1][:4]) + 1))
                raw_files = _panel_files(data_dir, "bars", years)
            missing_years = sorted(years - set(raw_files))
            if missing_years and require_panel:
                raise DataNotReadyError(
                    f"panel files missing for years {missing_years} (published: {sorted(raw_files)})")
            cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
            sig = _cache_signature(data_dir, sorted(raw_files), ALL_FIELDS, load_dates)
            cached = _open_panel_cache(cache_dir, sig, load_dates) if cache_dir else None
            cache_note = ""
            if cached is not None:
                fields, codes, qfq_available, warns = cached
                if verbose:
                    print(f"[panel] cache hit {cache_dir / ('panel_' + sig)}: "
                          f"{len(load_dates)}x{len(codes)}")
            else:
                # a precompute for a different warmup window can be reused when its row
                # axis is a superset of the requested dates (avoids ~1 GB rebuilds)
                cached = (None if cache_dir is None else
                          _open_superset_panel_cache(cache_dir, data_dir, load_dates, years))
                if cached is not None:
                    fields, codes, qfq_available, warns = cached
                    cache_note = warns[-1] if warns else ""
                    if verbose:
                        print(f"[panel] superset cache hit: {len(load_dates)}x{len(codes)}")
                else:
                    if verbose:
                        print(f"[panel] building matrices {len(load_dates)}x? "
                              f"(first run, cached after)")
                    out_dir = None
                    if cache_dir is not None:
                        out_dir = Path(cache_dir) / f"panel_{sig}"
                    fields, codes, qfq_available, warns = _build_panel(
                        data_dir, sorted(raw_files), load_dates, out_dir, verbose=verbose)
                    if out_dir is not None:
                        meta = {"engine": ENGINE_VERSION, "signature": sig, "fields": ALL_FIELDS,
                                "qfq_available": qfq_available, "warnings": warns,
                                "n_dates": len(load_dates), "n_codes": len(codes),
                                "years": sorted(int(y) for y in years),
                                "data_dir": str(Path(data_dir).resolve()),
                                "created": pd.Timestamp.now().isoformat()}
                        tmp = Path(str(out_dir) + ".meta.tmp")
                        tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
                        tmp.replace(Path(out_dir) / "meta.json")
            panel = PanelStore(fields, load_dates, codes, qfq_available, warns)

        codes = panel.codes
        universe_df = None
        upath = data_dir / "meta" / "universe.csv"
        if upath.exists():
            try:
                universe_df = pd.read_csv(upath, dtype=str, encoding='utf-8-sig')
            except Exception as exc:
                warnings.warn(f"universe.csv unreadable ({exc!r}); treating panel codes as listed")
        universe = UniverseStore(panel.dates, codes, universe_df)

        manifest = {}
        manifest_sha256 = None
        mpath = data_dir / "meta" / "manifest.json"
        if mpath.exists():
            try:
                mbytes = mpath.read_bytes()
                manifest_sha256 = hashlib.sha256(mbytes).hexdigest()
                manifest = json.loads(mbytes.decode("utf-8"))
            except Exception:
                manifest = {}
        manifest = manifest if isinstance(manifest, dict) else {}
        data_version = {
            "data_dir": str(data_dir),
            "manifest_keys": sorted(manifest.keys())[:50],
            # provenance only (does not affect calculation): the published
            # data/meta/manifest.json (v1_final) hash so every result can be traced to a
            # dataset snapshot even if the files are later republished.
            "manifest_sha256": manifest_sha256,
            "manifest_version": manifest.get("version"),
            "manifest_generated_at": manifest.get("generated_at"),
            "manifest_dataset": manifest.get("dataset"),
            "manifest_files": len(manifest.get("files", [])) if isinstance(
                manifest.get("files"), list) else None,
            "raw_years": sorted(int(str(p).split("_")[-1][:4]) for p in glob.glob(
                str(data_dir / "panel" / "bars_*.csv*")) if re.search(r"bars_(\d{4})", str(p))),
            "qfq_available": panel.qfq_available,
            "n_dates": len(panel.dates),
            "n_codes": len(panel.codes),
            "load_range": [str(load_dates[0]), str(load_dates[-1])],
        }
        # final run window inside the loaded rows (warmup rows are before _start_i)
        start_i2 = int(np.searchsorted(panel.dates, start, "left"))
        end_i2 = int(np.searchsorted(panel.dates, end, "right")) - 1
        if start_i2 >= len(panel.dates) or end_i2 < start_i2:
            raise DataNotReadyError(
                f"requested range {start}..{end} not covered by published data "
                f"({panel.dates[0]}..{panel.dates[-1]})")
        warnings_list = list(panel.warnings)
        if start_i2 < int(warmup_days):
            warnings_list.append(
                f"only {start_i2} warmup rows available before {start} (requested {warmup_days}); "
                "strategies with long lookbacks may see shorter history")
        bundle = cls(data_dir, calendar, panel, universe, index_dict, index_meta,
                     str(panel.dates[0]), str(panel.dates[-1]), warnings_list, data_version, bench)
        bundle._start = start
        bundle._end = end
        bundle._start_i = start_i2
        bundle._end_i = end_i2
        return bundle


def _load_indices(data_dir: Path):
    """Load data/panel/index_daily.csv.gz -> dict of per-index DataFrames."""
    path = Path(data_dir) / "panel" / "index_daily.csv.gz"
    if not path.exists():
        plain = Path(data_dir) / "panel" / "index_daily.csv"
        if not plain.exists():
            return {}, {}
        path = plain
    df = pd.read_csv(path, dtype={"index_code": str, "date": str}, encoding="utf-8-sig")
    ren = {c: c.lower().strip() for c in df.columns}
    df = df.rename(columns=ren)
    if "index_code" not in df.columns or "date" not in df.columns:
        return {}, {}
    df["index_code"] = df["index_code"].map(infer_index_code)
    df["date"] = df["date"].map(fmt_date)
    out, meta = {}, {}
    for code, sub in df.groupby("index_code", sort=True):
        sub = sub.drop_duplicates("date", keep="last").set_index("date").sort_index()
        cols = {}
        for name in ("open", "high", "low", "close", "preclose", "volume", "amount"):
            if name in sub.columns:
                cols[name] = pd.to_numeric(sub[name], errors="coerce").astype(float)
        if "close" not in cols:
            continue
        out[code] = pd.DataFrame(cols)
        meta[code] = {"n": int(len(sub)), "first": str(sub.index[0]), "last": str(sub.index[-1])}
    return out, meta