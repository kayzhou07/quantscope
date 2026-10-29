"""Shared helpers for the engine unittest suite (plain unittest, no pytest)."""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

FIXTURES = HERE / "fixtures"
TOY = FIXTURES / "toy"
FEES2023 = FIXTURES / "fees2023"
TMP_ROOT = HERE / "_tmp"

START = "2020-06-01"
END = "2021-12-31"


def make_tmp_dir(name: str) -> Path:
    """Create a writable temp dir under engine/tests/_tmp.

    NOTE: `tempfile.mkdtemp` directories are not writable in this DSH Windows
    sandbox (the freshly created directory gets an ACL that denies this process),
    so tests always use a plain mkdir with a unique name.
    """
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / f"{name}_{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_toy(strategy, start: str = START, end: str = END, **kwargs):
    """Run a backtest on the toy fixture with test-friendly defaults."""
    from engine.api import run_backtest
    kwargs.setdefault("warmup_days", 40)
    kwargs.setdefault("slippage_bps", 0.0)
    return run_backtest(strategy, start=start, end=end, data_dir=TOY, **kwargs)


def load_toy_bundle(start: str = START, end: str = END):
    from engine.data import DataBundle
    return DataBundle.load(TOY, start=start, end=end, warmup_days=40)


def copy_fixture(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def perturb_fixture(src: Path, dst: Path, cutoff: str = "2021-01-01",
                    factor: float = 1.7) -> Path:
    """Copy a fixture and multiply every price-like field for dates > cutoff.

    The perturbation is deliberately violent (per-name jump factors that also break
    limit/return relationships) so that any accidental lookahead into the future would
    change signals for dates <= cutoff.
    """
    copy_fixture(src, dst)
    cutoff = str(cutoff)
    price_cols = ["open", "high", "low", "close", "preclose"]
    for path in sorted((dst / "panel").glob("*.csv")):
        df = pd.read_csv(path, dtype={"code": str, "date": str})
        mask = df["date"] > cutoff
        if not mask.any():
            continue
        for col in price_cols:
            if col in df.columns:
                df.loc[mask, col] = (df.loc[mask, col].astype(float) * factor).round(2)
        if "amount" in df.columns:
            df.loc[mask, "amount"] = df.loc[mask, "amount"].astype(float) * 3.0
        if "volume" in df.columns:
            df.loc[mask, "volume"] = df.loc[mask, "volume"].astype(float) * 3.0
        df.to_csv(path, index=False)
    return dst


def events_of(result: dict, event_type: str) -> pd.DataFrame:
    ev = result["events"]
    if ev is None or len(ev) == 0 or "type" not in ev.columns:
        return pd.DataFrame()
    return ev[ev["type"] == event_type]


def trades_of(result: dict, code: str | None = None, side: str | None = None) -> pd.DataFrame:
    tr = result["trades"]
    if tr is None or len(tr) == 0:
        return pd.DataFrame()
    out = tr
    if code is not None:
        out = out[out["code"] == code]
    if side is not None:
        out = out[out["side"] == side]
    return out
