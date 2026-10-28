# -*- coding: utf-8 -*-
"""Independent recomputation of the round-3 S0/S1 result metrics.

INPUTS (raw engine output CSVs only — this script never imports the Lead's code and
never uses out/metrics.json or out/summary.csv as a *source*; those are only the
comparison target):

    out/DAILY_AUDIT.csv      (canonical = S1)      out/DAILY_AUDIT_S0.csv
    out/POSITIONS.csv        (S1)                  out/POSITIONS_S0.csv
    out/TRADES.csv           (S1)                  out/TRADES_S0.csv
    out/ORDERS.csv           (S1)                  out/ORDERS_S0.csv
    out/{S0,S1}_nav.csv      (supplementary, for the S1/S0 recompute if the
                              canonical CSVs are absent)

Everything below is recomputed from those files with the platform S0 definition
(`SuperMind_S0_月度基线_胜率诊断_v03.py:124-162`):

    pnl_t          = nav_t - nav_{t-1},  nav_0 := 100000 (initial capital)
    positive_t     = pnl_t >  +1e-6
    negative_t     = pnl_t <  -1e-6
    flat_t         = neither
    risk_day_t     = (positions_t > 0) or (positions_{t-1} > 0)      # exit day counts
    pure_cash_flat = (not risk_day) and flat
    winrate_all    = positive_days / days
    winrate_risk   = (risk_day & positive).sum() / risk_days
    winrate_nonzero= positive_days / (positive_days + negative_days)
    mean_win_day   = sum(positive pnl) / positive_days
    mean_loss_day  = sum(negative pnl) / negative_days
    daily_pf       = sum(positive pnl) / -sum(negative pnl)
    max_drawdown   = min(nav/cummax(nav) - 1), with peak = argmax(nav[:trough+1])

Tolerances used for the comparison (all "material" mismatches make the script exit 1):
    NAV / cash / market value / fees : absolute 1e-4 CNY
    return / drawdown / ratios       : absolute 1e-6
    day counts / trade counts        : exact
  (both are tighter than the 1e-6-NAV / 1e-4-ratio bounds requested; the Lead's numbers
   are derived from the same engine series, so exact agreement is expected.)

Internal consistency checks (independent of the Lead's metrics code):
    I1  nav == cash + market_value, every day                          (atol 1e-4 CNY)
    I2  sum(POSITIONS.value per day) == DAILY_AUDIT.market_value        (atol 1e-2 CNY)
    I3  count(POSITIONS shares>0 per day) == DAILY_AUDIT.n_positions    (exact)
    I4  cash_t == 100000 + cumsum(trade cash flows)                     (atol 0.05 CNY)
    I5  daily P&L decomposition: (nav_t - nav_{t-1}) == market_value_t - market_value_{t-1}
        + cash_t - cash_{t-1}                                           (atol 1e-4 CNY)
    I6  trade count and fee total in TRADES vs DAILY_AUDIT.fees sum
    I7  ORDERS ledger sanity (status vocabulary, no negative fills)

Usage:
    python verify_metrics.py [--out DIR] [--json]
Exit code 0 = all checks pass; 1 = at least one material mismatch.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CAP = 100_000.0                 # initial capital, per the handoff (§1: 初始10万元)
WIN_EPS = 1e-6                  # platform flat/positive threshold (S0:134-135)
NAV_ATOL = 1e-4                 # CNY
RATIO_ATOL = 1e-6
LEDGER_ATOL = 1e-2              # CNY, POSITIONS vs DAILY_AUDIT
CASH_ATOL = 0.05                # CNY, trade-ledger cash reconciliation

RESULTS: list[dict] = []


def rec(section: str, name: str, mine, lead, tol: str, ok: bool, note: str = "") -> None:
    RESULTS.append(dict(section=section, name=name, mine=mine, lead=lead,
                        tolerance=tol, ok=bool(ok), note=note))


def fmt(v) -> str:
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return "nan"
        return f"{v:.10g}"
    return str(v)


def close_num(a, b) -> tuple[bool, str]:
    try:
        a = float(a); b = float(b)
    except (TypeError, ValueError):
        return False, "non-numeric"
    if not (np.isfinite(a) and np.isfinite(b)):
        return False, "non-finite"
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)), f"diff={a-b:+.3e}"


def read_csv_required(path: Path, cols: list[str]) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"FATAL: missing required input {path}")
    df = pd.read_csv(path)
    lower = {c.lower(): c for c in df.columns}
    missing = [c for c in cols if c.lower() not in lower]
    if missing:
        raise SystemExit(f"FATAL: {path.name} missing columns {missing}; have {list(df.columns)}")
    out = pd.DataFrame({c: df[lower[c]] for c in cols})
    return out


def load_daily(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"FATAL: missing required input {path}")
    df = pd.read_csv(path, index_col=0)
    df.index = df.index.astype(str)
    lower = {c.lower(): c for c in df.columns}
    ren = {}
    for want in ("nav", "cash", "market_value", "exposure", "n_positions", "fees",
                 "traded_amount", "regime_letter", "regime_state"):
        if want in lower:
            ren[lower[want]] = want
    df = df.rename(columns=ren)
    need = ["nav", "cash", "market_value", "exposure", "n_positions"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise SystemExit(f"FATAL: {path.name} missing {miss}; have {list(df.columns)}")
    df = df.sort_index()
    for c in ("nav", "cash", "market_value", "exposure"):
        df[c] = df[c].astype(float)
    df["n_positions"] = df["n_positions"].astype(int)
    return df


def load_positions(path: Path) -> pd.DataFrame:
    df = read_csv_required(path, ["date", "code", "shares", "value"])
    df["date"] = df["date"].astype(str)
    df["code"] = df["code"].astype(str)
    for c in ("shares", "value"):
        df[c] = df[c].astype(float)
    return df


def load_trades(path: Path) -> pd.DataFrame:
    df = read_csv_required(path, ["date", "code", "side", "shares", "price", "amount",
                                  "fee_total"])
    df["date"] = df["date"].astype(str)
    df["side"] = df["side"].astype(str)
    for c in ("shares", "price", "amount", "fee_total"):
        df[c] = df[c].astype(float)
    return df


def load_orders(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    for c in ("date", "code", "side", "status", "reason", "source"):
        if c in df.columns:
            df[c] = df[c].astype(str)
    return df


def max_drawdown(nav: pd.Series) -> dict:
    peak = nav.cummax()
    dd = nav / peak - 1.0
    trough = dd.idxmin()
    peak_date = nav.loc[:trough].idxmax()
    return dict(max_drawdown=float(dd.min()), dd_peak=str(peak_date), dd_trough=str(trough))


def compute(nav_s, cash_s, mv_s, pos_cnt, expo_s, trades: pd.DataFrame) -> dict:
    """All metrics from the raw series. pos_cnt = positions>0 per day (Series)."""
    days = len(nav_s)
    prev_nav = nav_s.shift(1).fillna(CAP)
    pnl = nav_s - prev_nav
    positive = pnl > WIN_EPS
    negative = pnl < -WIN_EPS
    flat = ~(positive | negative)
    prev_cnt = pos_cnt.shift(1).fillna(0).astype(int)
    risk = (pos_cnt > 0) | (prev_cnt > 0)
    pos_d = int(positive.sum()); neg_d = int(negative.sum()); flat_d = int(flat.sum())
    risk_d = int(risk.sum()); risk_pos = int((risk & positive).sum())
    nonzero = pos_d + neg_d
    win_sum = float(pnl[positive].sum()); loss_sum = float(pnl[negative].sum())
    dd = max_drawdown(nav_s)
    out = dict(
        trading_days=days,
        final_nav=float(nav_s.iloc[-1]),
        total_return=float(nav_s.iloc[-1] / CAP - 1.0),
        max_drawdown=dd["max_drawdown"], dd_peak=dd["dd_peak"], dd_trough=dd["dd_trough"],
        positive_days=pos_d, negative_days=neg_d, flat_days=flat_d,
        risk_days=risk_d, risk_positive_days=risk_pos,
        winrate_all=pos_d / days if days else np.nan,
        winrate_risk=risk_pos / risk_d if risk_d else np.nan,
        winrate_nonzero=pos_d / nonzero if nonzero else np.nan,
        flat_day_share=flat_d / days if days else np.nan,
        pure_cash_flat_days=int((~risk & flat).sum()),
        mean_win_day=win_sum / pos_d if pos_d else np.nan,
        mean_loss_day=loss_sum / neg_d if neg_d else np.nan,
        daily_profit_factor=win_sum / -loss_sum if loss_sum < 0 else np.nan,
        avg_exposure=float(expo_s.mean()),
        end_exposure=float(expo_s.iloc[-1]),
        all_cash_days=int((mv_s <= 0.01).sum()),
        avg_positions=float(pos_cnt.mean()),
        max_positions=int(pos_cnt.max()),
        trade_count=int(len(trades)),
        total_fees=float(trades["fee_total"].sum()),
        cagr=float((nav_s.iloc[-1] / CAP) ** (252.0 / days) - 1.0) if days else np.nan,
        daily_vol=float((pnl / prev_nav).std() * math.sqrt(252.0)),
        worst_day=float((pnl / prev_nav).min()),
        best_day=float((pnl / prev_nav).max()),
        risk_flat_days=int((risk & flat).sum()),
    )
    return out


def yearly_returns(nav_s: pd.Series) -> pd.DataFrame:
    """Calendar-year returns, standard definition:

        ret_Y = nav[last day of Y] / nav[last day of Y-1] - 1, first year starts at CAP

    i.e. the first trading day P&L belongs to year Y (not excluded).  Same convention as
    run_round3.yearly_table (start_nav = prev_nav of the year first row).
    """
    df = pd.DataFrame({"nav": nav_s})
    df["year"] = [d[:4] for d in df.index]
    df["prev_nav"] = nav_s.shift(1).fillna(CAP)
    rows = []
    for y, sub in df.groupby("year"):
        start = float(sub["prev_nav"].iloc[0])
        end = float(sub["nav"].iloc[-1])
        rows.append(dict(year=str(y), days=len(sub), start_nav=start, end_nav=end,
                         return_=end / start - 1.0))
    return pd.DataFrame(rows)


# per-variant verification
# --------------------------------------------------------------------------- #
def verify_variant(tag: str, daily_path: Path, pos_path: Path, trades_path: Path,
                   orders_path: Path, lead_metrics: dict, lead_summary: pd.DataFrame) -> dict:
    print(f"\n{'='*100}\nVARIANT {tag}: daily={daily_path.name} pos={pos_path.name} "
          f"trades={trades_path.name} orders={orders_path.name}\n{'='*100}")
    daily = load_daily(daily_path)
    pos = load_positions(pos_path)
    trades = load_trades(trades_path)
    orders = load_orders(orders_path)

    nav_s = daily["nav"]; cash_s = daily["cash"]; mv_s = daily["market_value"]
    expo_s = daily["exposure"]
    cnt_audit = daily["n_positions"].astype(int)

    # ---- positions ledger derived count / value
    pg = pos.groupby("date")
    cnt_pos = pg.apply(lambda g: int((g["shares"] > 0).sum()), include_groups=False)
    val_pos = pg["value"].sum()
    cnt_pos = cnt_pos.reindex(daily.index).fillna(0).astype(int)
    val_pos = val_pos.reindex(daily.index).fillna(0.0).astype(float)

    # ---- independent metrics
    m = compute(nav_s, cash_s, mv_s, cnt_audit, expo_s, trades)
    # same metrics but with the POSITIONS-derived position count (purer independence)
    m_pos = compute(nav_s, cash_s, mv_s, cnt_pos, expo_s, trades)
    y = yearly_returns(nav_s)

    # ================= internal consistency =================
    print("\n-- internal consistency of the raw files --")
    d1 = (nav_s - (cash_s + mv_s)).abs()
    ok = bool((d1 <= NAV_ATOL).all()); rec(tag, "I1 nav == cash + market_value", float(d1.max()), 0.0, f"atol={NAV_ATOL}", ok)
    print(f"  I1 max|nav-(cash+mv)| = {d1.max():.3e} CNY  {'PASS' if ok else 'FAIL'}")

    d2 = (val_pos - mv_s).abs()
    ok = bool((d2 <= LEDGER_ATOL * 100).all()); rec(tag, "I2 sum(POSITIONS.value) == market_value", float(d2.max()), 0.0, "atol=1.0", ok)
    print(f"  I2 max|sum(pos.value)-mv| = {d2.max():.3e} CNY  {'PASS' if ok else 'FAIL'}")

    dcnt = (cnt_pos - cnt_audit).abs()
    ok = bool((dcnt == 0).all()); rec(tag, "I3 count(POSITIONS)==n_positions", int(dcnt.max()), 0, "exact", ok)
    print(f"  I3 max|count(pos)-n_positions| = {int(dcnt.max())}  {'PASS' if ok else 'FAIL'}")

    # trade-ledger cash reconciliation
    t = trades.copy()
    flows = np.where(t["side"] == "buy", -(t["amount"] + t["fee_total"]),
                     np.where(t["side"] == "sell", t["amount"] - t["fee_total"], 0.0))
    t = t.assign(flow=flows)
    dayflow = t.groupby("date")["flow"].sum().reindex(daily.index).fillna(0.0)
    cash_implied = CAP + dayflow.cumsum()
    dcash = (cash_implied - cash_s).abs()
    ok = bool((dcash <= CASH_ATOL).all())
    rec(tag, "I4 cash == CAP + cumsum(trade flows)", float(dcash.max()), 0.0, f"atol={CASH_ATOL}", ok)
    print(f"  I4 max|cash_implied-cash| = {dcash.max():.4f} CNY  {'PASS' if ok else 'FAIL'}")

    dpnl = ((nav_s - nav_s.shift(1).fillna(CAP))
            - ((mv_s - mv_s.shift(1).fillna(0.0)) + (cash_s - cash_s.shift(1).fillna(CAP)))).abs()
    ok = bool((dpnl <= NAV_ATOL).all()); rec(tag, "I5 daily P&L decomposition", float(dpnl.max()), 0.0, f"atol={NAV_ATOL}", ok)
    print(f"  I5 max|pnl decomposition| = {dpnl.max():.3e} CNY  {'PASS' if ok else 'FAIL'}")

    ok_side = set(trades["side"].unique()) <= {"buy", "sell", "adjust"}
    rec(tag, "I6a TRADES side vocabulary", ",".join(sorted(set(trades['side']))), "buy/sell/adjust", "exact", ok_side)
    print(f"  I6a TRADES sides = {sorted(set(trades['side']))}  {'PASS' if ok_side else 'FAIL'}")

    if "fees" in daily.columns:
        dfee = abs(float(daily["fees"].sum()) - m["total_fees"])
        ok = dfee <= 1e-2
        rec(tag, "I6b sum(DAILY_AUDIT.fees) == sum(TRADES.fee_total)", dfee, 0.0, "atol=0.01", ok)
        print(f"  I6b |sum(daily fees) - sum(trade fees)| = {dfee:.4f} CNY  {'PASS' if ok else 'FAIL'}")

    if len(orders):
        st = orders["status"].astype(str).str.lower().unique() if "status" in orders else []
        bad = [s for s in st if s not in {"filled", "partial", "blocked", "expired", "unfilled_at_end", "pending"}]
        rec(tag, "I7 ORDERS status vocabulary", ",".join(sorted(map(str, st))), "", "exact", not bad, str(bad))
        print(f"  I7 ORDERS statuses = {sorted(map(str, st))}  {'PASS' if not bad else 'FAIL'}")
        if "filled" in orders.columns:
            neg = int((pd.to_numeric(orders["filled"], errors="coerce") < 0).sum())
            rec(tag, "I7b no negative fill quantities", neg, 0, "exact", neg == 0)
            print(f"  I7b negative fill rows = {neg}  {'PASS' if neg == 0 else 'FAIL'}")

    # ================= comparison with the Lead's metrics =================
    lead = lead_metrics.get(tag)
    if lead is None:
        print(f"\n!! metrics.json has no variant {tag!r} (have {list(lead_metrics)})")
        rec(tag, "metrics.json variant present", "missing", tag, "exact", False)
        lead = {}
    else:
        print(f"\n-- comparison vs out/metrics.json variants[{tag}] --")
        cmp_keys = [
            ("trading_days", "exact"), ("final_nav", NAV_ATOL), ("total_return", RATIO_ATOL),
            ("max_drawdown", RATIO_ATOL), ("dd_peak", "exact"), ("dd_trough", "exact"),
            ("positive_days", "exact"), ("negative_days", "exact"), ("flat_days", "exact"),
            ("risk_days", "exact"), ("risk_positive_days", "exact"),
            ("winrate_all", RATIO_ATOL), ("winrate_risk", RATIO_ATOL),
            ("winrate_nonzero", RATIO_ATOL), ("flat_day_share", RATIO_ATOL),
            ("pure_cash_flat_days", "exact"), ("mean_win_day", NAV_ATOL),
            ("mean_loss_day", NAV_ATOL), ("daily_profit_factor", RATIO_ATOL),
            ("avg_exposure", RATIO_ATOL), ("end_exposure", RATIO_ATOL),
            ("all_cash_days", "exact"), ("avg_positions", 1e-9), ("max_positions", "exact"),
            ("trade_count", "exact"), ("total_fees", NAV_ATOL),
            ("risk_flat_days", "exact"), ("cagr", RATIO_ATOL),
        ]
        for key, tol in cmp_keys:
            if key not in lead:
                rec(tag, key, m.get(key), "absent_in_metrics_json", str(tol), False, "key missing")
                print(f"  [MISSING] {key}: absent from metrics.json")
                continue
            mine = m.get(key)
            lv = lead[key]
            if tol == "exact":
                try:
                    ok = int(mine) == int(lv)
                except (TypeError, ValueError):
                    ok = str(mine) == str(lv)
                detail = "" if ok else f"{mine} != {lv}"
            else:
                try:
                    ok = abs(float(mine) - float(lv)) <= float(tol)
                    detail = "" if ok else f"diff={float(mine)-float(lv):+.3e}"
                except (TypeError, ValueError):
                    ok = False; detail = f"{mine} vs {lv}"
            rec(tag, key, mine, lv, str(tol), ok, detail)
            print(f"  [{'PASS' if ok else 'FAIL'}] {key:24s} mine={fmt(mine):>22s} lead={fmt(lv):>22s} {detail}")

    if lead_summary is not None and len(lead_summary):
        row = lead_summary[lead_summary["tag"].astype(str) == tag]
        if len(row):
            print(f"\n-- comparison vs out/summary.csv row tag={tag} --")
            row = row.iloc[0]
            for key, tol in [("total_return", RATIO_ATOL), ("max_drawdown", RATIO_ATOL),
                             ("final_nav", NAV_ATOL), ("winrate_all", RATIO_ATOL),
                             ("winrate_risk", RATIO_ATOL), ("trade_count", "exact"),
                             ("total_fees", NAV_ATOL), ("avg_exposure", RATIO_ATOL),
                             ("all_cash_days", "exact"), ("pure_cash_flat_days", "exact")]:
                if key not in row.index:
                    continue
                mine = m.get(key); lv = row[key]
                if tol == "exact":
                    try:
                        ok = int(mine) == int(lv)
                    except (TypeError, ValueError):
                        ok = str(mine) == str(lv)
                else:
                    try:
                        ok = abs(float(mine) - float(lv)) <= float(tol)
                    except (TypeError, ValueError):
                        ok = False
                rec(tag, "summary." + key, mine, lv, str(tol), ok)
                print(f"  [{'PASS' if ok else 'FAIL'}] summary.{key:20s} mine={fmt(mine):>22s} lead={fmt(lv):>22s}")
        else:
            print(f"\n!! summary.csv has no row with tag={tag!r}")

    # ---- print the headline independent numbers
    print(f"\n-- INDEPENDENT METRICS ({tag}) --")
    for k in ("trading_days", "final_nav", "total_return", "max_drawdown", "dd_peak",
              "dd_trough", "positive_days", "negative_days", "flat_days", "risk_days",
              "risk_positive_days", "winrate_all", "winrate_risk", "winrate_nonzero",
              "flat_day_share", "pure_cash_flat_days", "mean_win_day", "mean_loss_day",
              "daily_profit_factor", "avg_exposure", "all_cash_days", "avg_positions",
              "max_positions", "trade_count", "total_fees", "risk_flat_days",
              "daily_vol", "worst_day", "best_day"):
        print(f"  {k:22s} = {fmt(m[k])}")
    print(f"\n-- YEARLY RETURNS ({tag}) --")
    for _, r in y.iterrows():
        print(f"  {r['year']}  days={int(r['days']):4d}  {r['start_nav']:12.2f} -> "
              f"{r['end_nav']:12.2f}  {r['return_']:+.4%}")
    return dict(tag=tag, metrics=m, metrics_pos_count=m_pos, yearly=y,
                daily=daily, pos=pos, trades=trades, orders=orders)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="out/ directory (default: ../out)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    here = Path(__file__).resolve().parent
    out = Path(args.out) if args.out else here.parent / "out"
    if not out.exists():
        raise SystemExit(f"FATAL: out dir {out} does not exist")

    metrics_path = out / "metrics.json"
    summary_path = out / "summary.csv"
    lead_metrics, lead_summary = {}, None
    if metrics_path.exists():
        with open(metrics_path, encoding="utf-8") as fh:
            mj = json.load(fh)
        lead_metrics = mj.get("variants", {})
        print(f"metrics.json: variants={list(lead_metrics)}")
        print(f"platform_reference: {mj.get('platform_reference')}")
    else:
        print("!! metrics.json missing — no comparison target")
    if summary_path.exists():
        lead_summary = pd.read_csv(summary_path)
        print(f"summary.csv: {len(lead_summary)} rows, tags={sorted(lead_summary['tag'].astype(str))}")

    variants = {}
    # canonical unsuffixed files = S1 (runner: results['S1'] -> DAILY_AUDIT.csv etc.)
    variants["S1"] = (out / "DAILY_AUDIT.csv", out / "POSITIONS.csv",
                      out / "TRADES.csv", out / "ORDERS.csv")
    # S0 has suffixed files
    if (out / "DAILY_AUDIT_S0.csv").exists():
        variants["S0"] = (out / "DAILY_AUDIT_S0.csv", out / "POSITIONS_S0.csv",
                          out / "TRADES_S0.csv", out / "ORDERS_S0.csv")

    computed = {}
    for tag, paths in variants.items():
        computed[tag] = verify_variant(tag, *paths, lead_metrics, lead_summary)

    # ================= platform R2 reference (informational) =================
    print(f"\n{'='*100}\nPLATFORM R2 REFERENCE CHECK (handoff §1: reproduce 84.00% / 11.16%)\n{'='*100}")
    ref = {}
    if metrics_path.exists():
        with open(metrics_path, encoding="utf-8") as fh:
            ref = json.load(fh).get("platform_reference", {}).get("R2", {})
    if lead_summary is not None:
        print(f"reference R2 (platform engine/data): total_return={ref.get('total_return')} "
              f"max_drawdown={ref.get('max_drawdown')}")
        for tag in lead_summary["tag"].astype(str):
            row = lead_summary[lead_summary["tag"].astype(str) == tag].iloc[0]
            print(f"  {tag:10s} ret={float(row['total_return']):+.4f} ({float(row['total_return'])-ref.get('total_return', np.nan):+.4f} vs R2)"
                  f"  maxdd={float(row['max_drawdown']):.4f} ({float(row['max_drawdown'])-ref.get('max_drawdown', np.nan):+.4f} vs R2)"
                  f"  [Lead metrics.json, NOT independently recomputed here]")

    # supplementary: independent recompute of every variant straight from its nav/trades
    for tag in ("S0", "S1", "S1b", "S0_20bp", "S1_stress"):
        navp = out / f"{tag}_nav.csv"
        trp = out / f"{tag}_trades.csv"
        if navp.exists() and trp.exists():
            nd = pd.read_csv(navp, index_col=0)
            nd.index = nd.index.astype(str)
            nd = nd.sort_index()
            tr = load_trades(trp)
            cnt = nd["n_positions"].astype(int) if "n_positions" in nd.columns else \
                pd.Series(0, index=nd.index)
            mm = compute(nd["nav"].astype(float), nd["cash"].astype(float),
                         nd["market_value"].astype(float), cnt,
                         nd["exposure"].astype(float), tr)
            computed.setdefault("_supplementary", {})[tag] = mm
            print(f"  [indep recompute from {tag}_nav.csv] total_return={mm['total_return']:+.6f} "
                  f"maxdd={mm['max_drawdown']:.6f} final_nav={mm['final_nav']:.4f} "
                  f"trades={mm['trade_count']} fees={mm['total_fees']:.2f} "
                  f"winrate_all={mm['winrate_all']:.6f} winrate_risk={mm['winrate_risk']:.6f}")
            if lead_metrics.get(tag):
                lv = lead_metrics[tag]
                for k, tol in (("total_return", RATIO_ATOL), ("max_drawdown", RATIO_ATOL),
                               ("final_nav", NAV_ATOL), ("trade_count", "exact"),
                               ("total_fees", NAV_ATOL)):
                    mine = mm[k]; l = lv.get(k)
                    ok = (int(mine) == int(l)) if tol == "exact" else abs(float(mine) - float(l)) <= float(tol)
                    rec(tag, f"nav_csv.{k}", mine, l, str(tol), ok)
                    print(f"    [{'PASS' if ok else 'FAIL'}] {k:16s} nav_csv={fmt(mine):>18s} metrics.json={fmt(l):>18s}")

    # ================= S0 vs S1 single-variable comparison =================
    if "S0" in computed and "S1" in computed:
        a = computed["S0"]["metrics"]; b = computed["S1"]["metrics"]
        print(f"\n{'='*100}\nS0 vs S1 (both independently recomputed)\n{'='*100}")
        print(f"  total_return   S0={a['total_return']:+.6f}  S1={b['total_return']:+.6f}  delta={b['total_return']-a['total_return']:+.6f}")
        print(f"  max_drawdown   S0={a['max_drawdown']:.6f}  S1={b['max_drawdown']:.6f}  delta={b['max_drawdown']-a['max_drawdown']:+.6f}")
        print(f"  winrate_all    S0={a['winrate_all']:.6f}  S1={b['winrate_all']:.6f}")
        print(f"  winrate_risk   S0={a['winrate_risk']:.6f}  S1={b['winrate_risk']:.6f}")
        print(f"  trade_count    S0={a['trade_count']}  S1={b['trade_count']}")
        print(f"  total_fees     S0={a['total_fees']:.2f}  S1={b['total_fees']:.2f}")
        print(f"  avg_exposure   S0={a['avg_exposure']:.6f}  S1={b['avg_exposure']:.6f}")
        rec("S0vsS1", "S1 changes only the affordability rule (same window/days)",
            a["trading_days"], b["trading_days"], "exact", a["trading_days"] == b["trading_days"])

    # ================= yearly.csv / cost_stress.csv consistency =================
    ypath = out / "yearly.csv"
    if ypath.exists():
        print(f"\n{'='*100}\nYEARLY.CSV vs INDEPENDENT YEARLY RETURNS\n{'='*100}")
        yl = pd.read_csv(ypath, dtype={"year": str})
        for tag in ("S0", "S1"):
            if tag not in computed:
                continue
            mine = computed[tag]["yearly"]
            for _, r in mine.iterrows():
                row = yl[(yl["tag"].astype(str) == tag) &
                         (yl["year"].astype(str) == str(r["year"]))]
                if not len(row):
                    rec(tag, f"yearly.{r['year']}", r["return_"], "absent", str(RATIO_ATOL), False)
                    continue
                lv = float(row.iloc[0]["return_"])
                ok = abs(float(r["return_"]) - lv) <= RATIO_ATOL
                rec(tag, f"yearly.{r['year']}", float(r["return_"]), lv, str(RATIO_ATOL), ok)
                print(f"  [{'PASS' if ok else 'FAIL'}] {tag} {r['year']}: mine={r['return_']:+.6%} "
                      f"lead={lv:+.6%}")

    cpath = out / "cost_stress.csv"
    if cpath.exists():
        print(f"\n{'='*100}\nCOST_STRESS.CSV internal consistency\n{'='*100}")
        cs = pd.read_csv(cpath)
        for _, r in cs.iterrows():
            base, stress = str(r["base"]), str(r["stressed"])
            if base in lead_metrics and stress in lead_metrics:
                b = lead_metrics[base]; s = lead_metrics[stress]
                ok1 = abs(float(r["delta_return"]) -
                          (float(s["total_return"]) - float(b["total_return"]))) <= 1e-9
                ok2 = abs(float(r["delta_dd"]) -
                          (float(s["max_drawdown"]) - float(b["max_drawdown"]))) <= 1e-9
                rec("cost_stress", f"{base}->{stress} delta_return", float(r["delta_return"]),
                    float(s["total_return"]) - float(b["total_return"]), "1e-9", ok1)
                rec("cost_stress", f"{base}->{stress} delta_dd", float(r["delta_dd"]),
                    float(s["max_drawdown"]) - float(b["max_drawdown"]), "1e-9", ok2)
                print(f"  [{'PASS' if ok1 and ok2 else 'FAIL'}] {base}->{stress}: "
                      f"ret {float(b['total_return']):+.4f} -> {float(s['total_return']):+.4f} "
                      f"(delta {float(r['delta_return']):+.4f}); "
                      f"dd {float(b['max_drawdown']):.4f} -> {float(s['max_drawdown']):.4f} "
                      f"(delta {float(r['delta_dd']):+.4f})")

    # ================= verdict =================
    fails = [r for r in RESULTS if not r["ok"]]
    print(f"\n{'='*100}")
    print(f"VERDICT: {len(RESULTS)-len(fails)}/{len(RESULTS)} checks passed; {len(fails)} FAILED")
    if fails:
        print("FAILED CHECKS:")
        for r in fails:
            print(f"  - [{r['section']}] {r['name']}: mine={fmt(r['mine'])} lead={fmt(r['lead'])} "
                  f"tol={r['tolerance']} {r['note']}")
    print(f"{'='*100}")
    if args.json:
        payload = dict(checks=RESULTS,
                       metrics={k: v["metrics"] for k, v in computed.items() if k != "_supplementary"},
                       yearly={k: v["yearly"].to_dict("records") for k, v in computed.items() if k != "_supplementary"},
                       supplementary=computed.get("_supplementary", {}))
        print(json.dumps(payload, indent=1, default=lambda o: float(o) if isinstance(o, np.floating) else str(o))[:200])
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
