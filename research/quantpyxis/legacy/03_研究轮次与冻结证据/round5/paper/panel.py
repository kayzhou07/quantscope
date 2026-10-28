# -*- coding: utf-8 -*-
"""Read-only panel access for the frozen strategy, with explicit no-future guards."""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
import pandas as pd

from . import config


class DataUnavailable(Exception):
    """Raised when the requested as-of date has no complete data (never guess)."""


class FutureDataError(Exception):
    """Raised on any attempt to read a row after the as-of date."""


@functools.lru_cache(maxsize=4)
def _calendar() -> pd.DataFrame:
    df = pd.read_csv(config.META / "calendar.csv", dtype={"date": str})
    return df


def trading_days() -> list[str]:
    cal = _calendar()
    if "is_open" in cal.columns:
        return sorted(cal.loc[cal["is_open"] == 1, "date"].astype(str).tolist())
    return sorted(cal["date"].astype(str).tolist())


@functools.lru_cache(maxsize=8)
def _raw_year(year: int) -> pd.DataFrame:
    p = config.PANEL / f"bars_{year}.csv.gz"
    if not p.exists():
        raise DataUnavailable(f"missing raw panel file {p.name}")
    return pd.read_csv(p, dtype={"code": str, "date": str},
                       usecols=["code", "date", "open", "high", "low", "close",
                                "preclose", "volume", "amount"])


@functools.lru_cache(maxsize=8)
def _qfq_year(year: int) -> pd.DataFrame:
    p = config.PANEL / f"bars_qfq_{year}.csv.gz"
    if not p.exists():
        raise DataUnavailable(f"missing qfq panel file {p.name}")
    return pd.read_csv(p, dtype={"code": str, "date": str},
                       usecols=["code", "date", "open", "close", "high", "low"])


@functools.lru_cache(maxsize=1)
def universe() -> pd.DataFrame:
    u = pd.read_csv(config.META / "universe.csv", dtype={"code": str})
    return u


def last_available_date() -> str:
    """Last date that has any raw bar (scan backwards over trading days)."""
    days = trading_days()
    year = int(days[-1][:4])
    df = _raw_year(year)
    have = set(df["date"].unique())
    for d in reversed(days):
        if d[:4] != str(year):
            year = int(d[:4])
            df = _raw_year(year)
            have = set(df["date"].unique())
        if d in have:
            return d
    raise DataUnavailable("panel has no bars at all")


def has_bar(date: str) -> bool:
    if date > last_available_date():
        return False
    return len(_raw_year(int(date[:4])).query("date == @date")) > 0


def assert_not_future(date: str, asof: str) -> None:
    if date > asof:
        raise FutureDataError(f"attempt to read {date} with as_of={asof}")


def bars_on(date: str) -> pd.DataFrame:
    """Raw bars of one trading day, indexed by code. Empty frame if the day is missing."""
    if not has_bar(date):
        return pd.DataFrame(columns=["code", "open", "high", "low", "close",
                                     "preclose", "volume", "amount"]).set_index("code")
    df = _raw_year(int(date[:4]))
    sub = df[df["date"] == date].set_index("code")
    return sub


def qfq_window(asof: str, n: int = 260) -> tuple[list[str], pd.DataFrame]:
    """(dates, wide qfq-close DataFrame) of the last n trading days <= asof."""
    assert_not_future(asof, asof)
    days = [d for d in trading_days() if d <= asof]
    if len(days) < n:
        raise DataUnavailable(f"need {n} trading days <= {asof}, have {len(days)}")
    days = days[-n:]
    years = sorted({int(d[:4]) for d in days})
    frames = [_qfq_year(y)[["code", "date", "close"]] for y in years]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["date"].isin(set(days))]
    wide = df.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    wide = wide.reindex(days)
    return days, wide


def amount_window(asof: str, n: int = 60) -> pd.DataFrame:
    days = [d for d in trading_days() if d <= asof][-n:]
    years = sorted({int(d[:4]) for d in days})
    frames = [_raw_year(y)[["code", "date", "amount"]] for y in years]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["date"].isin(set(days))]
    return df.pivot_table(index="date", columns="code", values="amount", aggfunc="last").reindex(days)


def last_close_before(code: str, date: str, max_back: int = 30) -> float | None:
    days = [d for d in trading_days() if d < date]
    got_bar = False
    for d in reversed(days[-max_back:]):
        try:
            bars = bars_on(d)
        except DataUnavailable:
            continue
        if code in bars.index:
            got_bar = True
            v = bars.loc[code, "close"]
            if np.isfinite(v) and v > 0:
                return float(v)
        elif got_bar:
            # crossed a suspension block; keep scanning a little further
            continue
    return None


def limit_band(code: str, date: str) -> float:
    if code.startswith("300") or code.startswith("301"):
        return 0.20 if date >= "2020-08-24" else 0.10
    return 0.10


def universe_at(date: str) -> list[str]:
    u = universe().copy()
    u = u[u["ipo_date"].notna() & (u["ipo_date"] <= date)]
    out = u["out_date"].isna() | (u["out_date"] > date)
    u = u[out]
    ok = u["code"].str.startswith(("600", "601", "603", "605", "000", "001",
                                   "002", "003", "300", "301"))
    return sorted(u.loc[ok, "code"].tolist())
