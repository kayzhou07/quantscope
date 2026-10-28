# -*- coding: utf-8 -*-
"""STEP 3g: FIFO trade statistics computed independently from A's OWN trade ledger.

Method (identical accounting convention to the engine, re-implemented here):
  * buy lot cost/unit = (amount + fee_total) / shares
  * sell net/unit   = (amount - fee_total) / shares, matched FIFO against open lots
  * 'adjust' rows are virtual zero-cost lots created by the qfq total-return
    approximation on ex-dates (corporate-action share multiplier; the implicit
    dividend is reinvested, so the whole sale proceeds of those units are gain)
  * a closed "sell fill" is one sell execution with matched units (engine convention:
    pnl >= 0 counts as a win); a closed "round trip" is a holding episode that goes
    from flat to flat (win if the SUM of matched sell pnl inside the episode > 0)
  * unrealized at end = final holdings market value (last ledger date) - FIFO open cost

Outputs: fifo_closed_trades.csv, fifo_roundtrips.csv, fifo_by_year.csv,
         fifo_by_stress_window.csv, fifo_summary.json
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attrib_common as C  # noqa: E402


def run_fifo(trades: pd.DataFrame):
    df = trades.reset_index(drop=True)
    lots: dict[str, deque] = {}
    sells = []            # one row per sell fill
    episodes = []         # one row per flat->flat holding episode
    open_ep = {}          # code -> episode dict
    for i, t in df.iterrows():
        code = str(t["code"])
        side = str(t["side"])
        sh = float(t["shares"]) if np.isfinite(t["shares"]) else 0.0
        if sh <= 0:
            continue
        if side == "buy":
            cost_per = (float(t["amount"]) + float(t["fee_total"])) / sh
            lots.setdefault(code, deque()).append([sh, cost_per])
            if code not in open_ep:
                open_ep[code] = dict(code=code, start_date=str(t["date"]),
                                     n_buys=0, realized=0.0, n_sells=0,
                                     units_bought=0.0)
            open_ep[code]["n_buys"] += 1
            open_ep[code]["units_bought"] += sh
            continue
        if side == "adjust":
            lots.setdefault(code, deque()).append([sh, 0.0])
            if code not in open_ep:
                open_ep[code] = dict(code=code, start_date=str(t["date"]),
                                     n_buys=0, realized=0.0, n_sells=0,
                                     units_bought=0.0)
            open_ep[code]["units_bought"] += sh
            continue
        if side != "sell":
            continue
        net_per = (float(t["amount"]) - float(t["fee_total"])) / sh
        dq = lots.get(code)
        if not dq:
            continue
        remaining = sh
        pnl = matched = 0.0
        while remaining > 1e-12 and dq:
            lot = dq[0]
            take = min(remaining, lot[0])
            pnl += take * (net_per - lot[1])
            lot[0] -= take
            remaining -= take
            matched += take
            if lot[0] <= 1e-12:
                dq.popleft()
        if matched <= 1e-12:
            continue
        sells.append(dict(date=str(t["date"]), code=code, units=matched,
                          pnl=pnl, win=bool(pnl >= 0.0),
                          reason=str(t.get("reason", "")),
                          price=float(t["price"])))
        ep = open_ep.setdefault(code, dict(code=code, start_date=str(t["date"]),
                                           n_buys=0, realized=0.0, n_sells=0,
                                           units_bought=0.0))
        ep["realized"] += pnl
        ep["n_sells"] += 1
        # flat when the FIFO book has no remaining units for this code
        left = sum(l[0] for l in dq) if dq else 0.0
        if left <= 1e-9:
            ep["end_date"] = str(t["date"])
            ep["win"] = bool(ep["realized"] > 0.0)
            episodes.append(ep)
            del open_ep[code]
    # remaining open lots
    open_units = open_cost = 0.0
    for c, dq in lots.items():
        for lot in dq:
            open_units += lot[0]
            open_cost += lot[0] * lot[1]
    ep_df = pd.DataFrame(episodes)
    open_df = pd.DataFrame([dict(code=c, units=float(sum(l[0] for l in dq)),
                                 start_date=(open_ep.get(c, {}).get("start_date", "")),
                                 unrealized_realized_so_far=(open_ep.get(c, {}).get("realized", 0.0)))
                            for c, dq in lots.items() if sum(l[0] for l in dq) > 1e-9])
    return dict(sells=pd.DataFrame(sells), episodes=ep_df,
                open_lots=open_df, open_units=open_units, open_cost=open_cost)


def stat_block(sells: pd.DataFrame, label: str) -> dict:
    if len(sells) == 0:
        return dict(window=label, n_closed_sell_fills=0)
    w = sells["win"].to_numpy()
    gains = sells.loc[sells["win"], "pnl"]
    losses = sells.loc[~sells["win"], "pnl"]
    gp, gl = float(gains.sum()), float(losses.sum())
    return dict(window=label, n_closed_sell_fills=int(len(sells)),
                realized_pnl=float(sells["pnl"].sum()),
                win_count=int(w.sum()), loss_count=int((~w).sum()),
                win_rate=float(w.mean()),
                avg_win=float(gains.mean()) if len(gains) else 0.0,
                avg_loss=float(losses.mean()) if len(losses) else 0.0,
                gross_profit=gp, gross_loss=gl,
                profit_factor=float(gp / abs(gl)) if gl < 0 else np.inf,
                matched_units=float(sells["units"].sum()))


def episode_block(ep: pd.DataFrame, label: str) -> dict:
    if len(ep) == 0:
        return dict(window=label, n_round_trips=0)
    w = ep["win"].to_numpy()
    gains = ep.loc[ep["win"], "realized"]
    losses = ep.loc[~ep["win"], "realized"]
    gp, gl = float(gains.sum()), float(losses.sum())
    return dict(window=label, n_round_trips=int(len(ep)),
                win_count=int(w.sum()), loss_count=int((~w).sum()),
                win_rate=float(w.mean()),
                avg_win=float(gains.mean()) if len(gains) else 0.0,
                avg_loss=float(losses.mean()) if len(losses) else 0.0,
                profit_factor=float(gp / abs(gl)) if gl < 0 else np.inf)


def main():
    led = C.load_ledgers()
    trades = led["trades"]
    nav = led["nav"]
    pos = led["positions"]
    res = run_fifo(trades)
    sells, eps = res["sells"], res["episodes"]
    sells.to_csv(C.OUT / "fifo_closed_trades.csv", index=False)
    eps.to_csv(C.OUT / "fifo_roundtrips.csv", index=False)

    # final holdings value from the last position ledger row
    final_date = str(nav.index[-1])
    last = pos[pos["date"] == final_date]
    final_value = float(last["value"].sum()) if len(last) else 0.0
    unrealized = final_value - res["open_cost"]

    full = stat_block(sells, "full 2014-07-01..2026-10-19")
    ep_full = episode_block(eps, "full 2014-07-01..2026-10-19")

    # per-year and per-stress-window breakdowns
    sells["year"] = sells["date"].str[:4]
    eps["year"] = eps["end_date"].astype(str).str[:4]
    by_year, by_year_ep = [], []
    for y, g in sells.groupby("year"):
        by_year.append(stat_block(g, y))
    for y, g in eps.groupby("year"):
        by_year_ep.append(episode_block(g, y))
    pd.DataFrame(by_year).to_csv(C.OUT / "fifo_by_year.csv", index=False)
    pd.DataFrame(by_year_ep).to_csv(C.OUT / "fifo_roundtrips_by_year.csv", index=False)

    by_win = [full]
    for tag, ws, we in C.STRESS_WINDOWS:
        g = sells[(sells["date"] >= ws) & (sells["date"] <= we)]
        by_win.append(stat_block(g, tag))
    pd.DataFrame(by_win).to_csv(C.OUT / "fifo_by_stress_window.csv", index=False)

    # sanity: reconciliation (realized + unrealized should equal total P&L)
    m = json.loads((C.OUT / "a_summary.json").read_text(encoding="utf-8"))
    pnl_actual = float(nav["nav"].iloc[-1] - C.CAP)
    # engine reference implementation on the same trade ledger
    from engine.metrics import fifo_pnl as engine_fifo_pnl
    eng = engine_fifo_pnl(trades)
    eng_unreal = float(final_value - eng["open_cost"])
    summary = {
        "method": "independent FIFO re-implementation from a_trades.csv (engine convention)",
        "n_trades_total": int(len(trades)),
        "n_buy_fills": int((trades["side"] == "buy").sum()),
        "n_sell_fills": int((trades["side"] == "sell").sum()),
        "n_adjust_rows": int((trades["side"] == "adjust").sum()),
        "full": full,
        "full_round_trips": ep_full,
        "unrealized_at_end": float(unrealized),
        "fifo_open_units": float(res["open_units"]),
        "fifo_open_cost": float(res["open_cost"]),
        "final_holdings_value": final_value,
        "pnl_actual": pnl_actual,
        "realized_plus_unrealized": float(full["realized_pnl"] + unrealized),
        "reconciliation_gap": float(pnl_actual - full["realized_pnl"] - unrealized),
        "engine_metrics_check": {
            "fifo_realized_pnl": eng["realized_pnl"],
            "fifo_win_rate": eng["win_rate"],
            "fifo_win_count": eng["win_count"],
            "fifo_loss_count": eng["loss_count"],
            "fifo_avg_win": eng["avg_win"],
            "fifo_avg_loss": eng["avg_loss"],
            "fifo_profit_factor": eng["profit_factor"],
            "fifo_unrealized_pnl": eng_unreal,
            "engine_open_cost": eng["open_cost"],
            "engine_open_units": eng["open_units"],
            "engine_n_adjust_events": eng["n_adjust_events"],
            "a_summary_fifo_realized_pnl": m.get("fifo_realized_pnl"),
            "a_summary_fifo_win_rate": m.get("fifo_win_rate"),
        },
        "own_vs_engine_match": {},
        "open_positions_at_end": res["open_lots"].to_dict(orient="records"),
    }
    chk = summary["engine_metrics_check"]
    summary["own_vs_engine_match"] = {
        "realized_pnl": bool(abs(full["realized_pnl"] - chk["fifo_realized_pnl"]) < 0.01),
        "win_rate": bool(abs(full["win_rate"] - chk["fifo_win_rate"]) < 1e-9),
        "win_count": bool(full["win_count"] == chk["fifo_win_count"]),
        "loss_count": bool(full["loss_count"] == chk["fifo_loss_count"]),
        "avg_win": bool(abs(full["avg_win"] - chk["fifo_avg_win"]) < 0.01),
        "avg_loss": bool(abs(full["avg_loss"] - chk["fifo_avg_loss"]) < 0.01),
        "profit_factor": bool(abs(full["profit_factor"] - chk["fifo_profit_factor"]) < 1e-6),
        "unrealized": bool(abs(unrealized - chk["fifo_unrealized_pnl"]) < 0.01),
    }
    (C.OUT / "fifo_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    print("=== 3g FIFO (A's own trades) ===")
    print(json.dumps(full, indent=1, ensure_ascii=False, default=str))
    print("round trips:", json.dumps(ep_full, indent=1, ensure_ascii=False, default=str))
    print(f"unrealized at end {unrealized:,.2f}  open cost {res['open_cost']:,.2f}  "
          f"final holdings value {final_value:,.2f}")
    print(f"reconciliation gap (actual - realized - unrealized): "
          f"{summary['reconciliation_gap']:.4f}")
    print("own vs engine:", summary["own_vs_engine_match"])
    print("\nby year:")
    print(pd.DataFrame(by_year).to_string(index=False))
    print("\nround trips by year:")
    print(pd.DataFrame(by_year_ep).to_string(index=False))
    print("\nby stress window:")
    print(pd.DataFrame(by_win).to_string(index=False))


if __name__ == "__main__":
    main()
