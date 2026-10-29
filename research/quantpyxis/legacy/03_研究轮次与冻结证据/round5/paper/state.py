# -*- coding: utf-8 -*-
"""State continuation: recompute the frozen regime generator at an as-of date.

The published regime_v1.csv is a historical SAMPLE, not a continuation algorithm.  For any
forward date we recompute with the frozen generator (qrun_1416/os/regime_v1.py).

Implementation note: the generator is causal (row t uses only data <= t), so one full
recomputation to the data cutoff, truncated at asof, is bit-identical to recomputing with
end=asof.  `direct_recompute` performs the slow independent end=asof run for equivalence
evidence.  Missing data raises DataUnavailable (never fall back to N / stale state).
"""
from __future__ import annotations

import functools
import importlib.util

import pandas as pd

from . import config, panel

MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}


def _load_generator():
    p = config.REPO / "os" / "regime_v1.py"
    spec = importlib.util.spec_from_file_location("regime_v1_frozen", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@functools.lru_cache(maxsize=1)
def _full() -> pd.DataFrame:
    gen = _load_generator()
    cutoff = panel.last_available_date()
    try:
        df = gen.compute_regime(data_dir=str(config.DATA), start="2012-08-01", end=cutoff)
    except Exception as exc:  # noqa: BLE001
        raise panel.DataUnavailable(f"regime recompute failed: {exc}") from exc
    return df


def direct_recompute(asof: str) -> pd.DataFrame:
    """Slow independent recomputation with end=asof (used only for equivalence evidence)."""
    gen = _load_generator()
    return gen.compute_regime(data_dir=str(config.DATA), start="2012-08-01", end=asof)


@functools.lru_cache(maxsize=64)
def recompute(asof: str) -> dict:
    last = panel.last_available_date()
    if asof > last:
        raise panel.DataUnavailable(
            f"asof {asof} is after the last available bar {last}; state cannot be computed")
    df = _full()
    df = df[df["date"].astype(str) <= asof].reset_index(drop=True)
    if len(df) == 0 or str(df["date"].iloc[-1]) != asof:
        raise panel.DataUnavailable(f"regime has no row for {asof}")
    flags = df[["crash_flag", "bear_flag", "bull_flag"]].astype(int).to_numpy()
    states = df["state"].tolist()

    def cand(i: int) -> str:
        c, b, u = flags[i]
        return "CRASH" if c else "BEAR" if b else "BULL" if u else "NEUTRAL"

    cur = states[-1]
    run_len = 1
    for i in range(len(states) - 2, -1, -1):
        if states[i] == cur:
            run_len += 1
        else:
            break
    pending, pend_n = None, 0
    if cand(len(states) - 1) != cur:
        base = "BEAR" if cur == "CRASH" else cur
        pending = f"{base}->{cand(len(states) - 1)}"
        pend_n = 1
        j = len(states) - 2
        while j >= 0 and cand(j) == cand(len(states) - 1):
            pend_n += 1
            j -= 1
    return {
        "asof": asof,
        "state": cur,
        "letter": MAP[cur],
        "state_prev": states[-2] if len(states) > 1 else "NEUTRAL",
        "state_run_days": run_len,
        "candidate_today": cand(len(states) - 1),
        "crash_flag": int(flags[-1][0]), "bear_flag": int(flags[-1][1]),
        "bull_flag": int(flags[-1][2]),
        "pending_direction": pending,
        "pending_confirm_days": int(pend_n) if pending else 0,
        "confirm_required_days": 5,
        "last_available_bar": last,
    }


@functools.lru_cache(maxsize=1)
def published() -> pd.DataFrame:
    df = pd.read_csv(config.REPO / "os" / "out" / "regime_v1.csv", dtype={"date": str})
    df["letter"] = df["state"].map(MAP)
    return df


def equivalence_check(cutoff: str) -> dict:
    """Slow check: direct end=cutoff recomputation == truncated full recomputation."""
    direct = direct_recompute(cutoff)[["date", "state"]].copy()
    direct["date"] = direct["date"].astype(str)
    direct["direct_letter"] = direct["state"].map(MAP)
    trunc = _full()[["date", "state"]].copy()
    trunc["date"] = trunc["date"].astype(str)
    trunc = trunc[trunc["date"] <= cutoff]
    trunc["trunc_letter"] = trunc["state"].map(MAP)
    m = direct.merge(trunc, on="date", how="inner")
    mism = m[m["direct_letter"] != m["trunc_letter"]]
    return {"cutoff": cutoff, "compared_rows": int(len(m)),
            "mismatch_rows": int(len(mism)),
            "pass": len(mism) == 0 and len(m) > 0,
            "mismatch_first": mism.head(5)[["date", "direct_letter", "trunc_letter"]]
            .to_dict("records") if len(mism) else []}


def check(cutoffs: list[str], do_equivalence: bool = True) -> dict:
    out = {"cutoffs": [], "all_pass": True}
    pub = published()
    trunc_full = _full()[["date", "state"]].copy()
    trunc_full["date"] = trunc_full["date"].astype(str)
    trunc_full["recomputed_letter"] = trunc_full["state"].map(MAP)
    for c in cutoffs:
        rec = {"cutoff": c}
        try:
            info = recompute(c)
            rec.update({k: info[k] for k in
                        ("state", "pending_direction", "pending_confirm_days",
                         "state_run_days", "last_available_bar")})
            p = pub[pub["date"] <= c]
            if len(p) == 0:
                rec["pass"] = False
                rec["error"] = "no published rows before cutoff"
            else:
                merged = trunc_full[trunc_full["date"] <= c].merge(
                    p[["date", "letter"]], on="date", how="inner")
                mism = merged[merged["recomputed_letter"] != merged["letter"]]
                rec["compared_rows"] = int(len(merged))
                rec["mismatch_rows"] = int(len(mism))
                rec["mismatch_first"] = (mism.head(5)[["date", "recomputed_letter", "letter"]]
                                         .to_dict("records") if len(mism) else [])
                rec["published_prefix_match"] = len(mism) == 0 and len(merged) > 0
                rec["pass"] = rec["published_prefix_match"]
        except panel.DataUnavailable as exc:
            rec["pass"] = False
            rec["error"] = f"DATA_UNAVAILABLE: {exc}"
        out["cutoffs"].append(rec)
        out["all_pass"] = out["all_pass"] and rec.get("pass", False)
    if do_equivalence and cutoffs:
        eq = equivalence_check(cutoffs[-1])
        out["direct_vs_truncated"] = eq
        out["all_pass"] = out["all_pass"] and eq["pass"]
    return out
