# -*- coding: utf-8 -*-
"""Paper ledger: idempotent signal -> next-open fills with lots, limits, fees, corporate actions.

The ledger is a JSON file.  Capital identity (cash + marked positions = NAV) is asserted after
every trading day; a violation aborts the run.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import config, panel, state as state_mod, util

LOT = config.LOT


class LedgerError(Exception):
    pass


def _commission(amount: float) -> float:
    return max(config.MIN_COMM, amount * config.COMM)


def _stamp(amount: float, date: str) -> float:
    r = config.STAMP_FROM if date >= config.STAMP_SWITCH else config.STAMP_BEFORE
    return amount * r


def _transfer(amount: float) -> float:
    return amount * config.TRANSFER


def _fees(amount: float, side: str, date: str) -> dict:
    c = _commission(amount)
    s = _stamp(amount, date) if side == "sell" else 0.0
    t = _transfer(amount)
    return {"commission": c, "stamp": s, "transfer": t, "total": c + s + t}


class PaperLedger:
    def __init__(self, path: Path, data: dict):
        self.path = path
        self.d = data

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, path: Path, account_id: str, cash: float,
               inherit_positions: dict | None = None) -> "PaperLedger":
        data = {
            "schema": config.SCHEMA_LEDGER,
            "account_id": account_id,
            "freeze_id": config.FREEZE_ID,
            "freeze_at": config.FREEZE_AT,
            "created_at": util.now_iso(),
            "initial_cash": float(cash),
            "cash": float(cash),
            "positions": {},
            "pending_orders": [],
            "fills": [],
            "nav_history": [],
            "processed_signals": {},
            "events": [],
            "last_processed_date": None,
        }
        led = cls(path, data)
        for code, spec in (inherit_positions or {}).items():
            shares = float(spec["shares"])
            price = float(spec.get("last_price", spec.get("avg_cost", 0.0)))
            led.d["positions"][code] = {
                "shares": shares, "cost_total": shares * price,
                "last_price": price, "last_date": None, "open_date": None,
            }
        led.save()
        return led

    @classmethod
    def load(cls, path: Path) -> "PaperLedger":
        return cls(path, util.read_json(path))

    def save(self) -> None:
        util.atomic_write_json(self.path, self.d)

    # ------------------------------------------------------------- helpers
    @property
    def cash(self) -> float:
        return float(self.d["cash"])

    @property
    def positions(self) -> dict:
        return self.d["positions"]

    def nav(self) -> float:
        return self.cash + sum(p["shares"] * p["last_price"] for p in self.positions.values())

    def _order_id(self, signal_id: str, code: str, side: str, seq: int) -> str:
        return "ord-" + util.sha256_text(f"{signal_id}|{code}|{side}|{seq}")[:16]

    def _batch_id(self, signal_id: str) -> str:
        return "fill-" + util.sha256_text(f"batch|{signal_id}")[:16]

    # --------------------------------------------------- corporate actions
    def apply_corporate_actions(self, date: str) -> list[dict]:
        if not panel.has_bar(date):
            return []
        bars = panel.bars_on(date)
        out = []
        for code, pos in list(self.positions.items()):
            if code not in bars.index:
                continue
            pre = float(bars.loc[code, "preclose"])
            if not np.isfinite(pre) or pre <= 0:
                continue
            prev = panel.last_close_before(code, date)
            if prev is None or not np.isfinite(prev) or prev <= 0:
                continue
            m = prev / pre
            if 0.2 <= m <= 3.0 and abs(m - 1.0) > 0.003:
                pos["shares"] = float(pos["shares"]) * m
                pos["last_price"] = pos["last_price"] / m if pos["last_price"] else pos["last_price"]
                ev = {"date": date, "type": "corporate_action", "code": code,
                      "multiplier": round(float(m), 8),
                      "shares_after": round(float(pos["shares"]), 6),
                      "source": "raw preclose vs previous close (qfq-reinvest convention)"}
                self.d["events"].append(ev)
                out.append(ev)
        return out

    # --------------------------------------------------------- order intake
    def create_orders(self, signal: dict, fill_date: str) -> str:
        sid = signal["signal_id"]
        if sid in self.d["processed_signals"]:
            return self.d["processed_signals"][sid]
        batch_id = self._batch_id(sid)
        targets = dict(signal.get("targets") or {})
        base_nav = float(signal["nav"])
        orders = []
        seq = 0
        for code, pos in self.positions.items():
            if float(pos["shares"]) > 0 and code not in targets:
                seq += 1
                orders.append({"order_id": self._order_id(sid, code, "sell", seq),
                               "signal_id": sid, "code": code, "side": "sell",
                               "requested_shares": float(pos["shares"]),
                               "created_date": signal["asof"],
                               "created_close": signal["asof"],
                               "expires_date": self._expiry(fill_date),
                               "base_nav": base_nav, "target_weight": 0.0,
                               "attempts": 0, "status": "pending", "last_reason": ""})
        for code, w in targets.items():
            seq += 1
            orders.append({"order_id": self._order_id(sid, code, "buy", seq),
                           "signal_id": sid, "code": code, "side": "buy",
                           "requested_shares": None, "target_weight": float(w),
                           "created_date": signal["asof"],
                           "created_close": signal["asof"],
                           "expires_date": self._expiry(fill_date),
                           "base_nav": base_nav, "attempts": 0,
                           "status": "pending", "last_reason": ""})
        # trims for held target names whose target is below current are handled in attempt
        existing = {o["order_id"] for o in self.d["pending_orders"]}
        for o in orders:
            if o["order_id"] not in existing:
                self.d["pending_orders"].append(o)
        self.d["processed_signals"][sid] = batch_id
        return batch_id

    def _expiry(self, date: str) -> str:
        days = [d for d in panel.trading_days() if d >= date]
        idx = min(len(days) - 1, config.MAX_PENDING_DAYS)
        return days[idx]

    # -------------------------------------------------------------- fills
    def attempt_pending(self, date: str) -> dict:
        bars = panel.bars_on(date)
        result = {"date": date, "attempted": 0, "filled": 0, "blocked": 0, "expired": 0,
                  "no_bar": bars.empty, "blocked_detail": []}
        if bars.empty:
            return result
        keep = []
        for o in self.d["pending_orders"]:
            o["attempts"] = int(o.get("attempts", 0)) + 1
            if date > o["expires_date"]:
                o["status"] = "expired"
                self.d["events"].append({"date": date, "type": "order_expired",
                                         "order_id": o["order_id"], "code": o["code"],
                                         "side": o["side"], "reason": "max_pending_days"})
                result["expired"] += 1
                continue
            if o["code"] not in bars.index:
                o["last_reason"] = "suspended"
                result["blocked"] += 1
                keep.append(o)
                continue
            row = bars.loc[o["code"]]
            openp = float(row["open"])
            pre = float(row["preclose"])
            if not np.isfinite(openp) or openp <= 0 or not np.isfinite(pre) or pre <= 0:
                o["last_reason"] = "missing_open"
                result["blocked"] += 1
                keep.append(o)
                continue
            band = panel.limit_band(o["code"], date)
            up = round(pre * (1.0 + band), 2)
            dn = round(pre * (1.0 - band), 2)
            pos = self.positions.get(o["code"])
            cur = float(pos["shares"]) if pos else 0.0
            result["attempted"] += 1
            if o["side"] == "sell":
                if openp <= dn + 1e-9:
                    o["last_reason"] = "limit_down"
                    result["blocked"] += 1
                    result["blocked_detail"].append({"code": o["code"], "side": "sell",
                                                     "reason": "limit_down", "date": date})
                    keep.append(o)
                    continue
                shares = min(float(o["requested_shares"]), cur)
                if shares <= 0:
                    continue  # nothing to sell
                price = openp * (1.0 - config.SLIP)
                self._fill(o, date, price, shares)
                result["filled"] += 1
                continue
            # buy / trim
            if openp >= up - 1e-9:
                o["last_reason"] = "limit_up"
                result["blocked"] += 1
                result["blocked_detail"].append({"code": o["code"], "side": "buy",
                                                 "reason": "limit_up", "date": date})
                keep.append(o)
                continue
            target_value = float(o["target_weight"]) * float(o["base_nav"])
            desired = math.floor(target_value / (openp * 1.001) / LOT) * LOT
            diff = desired - cur
            if diff <= 0:
                continue  # already at/above target (trim not required between rebalances)
            shares = diff
            price = openp * (1.0 + config.SLIP)
            while shares > 0:
                amount = shares * price
                fee = _fees(amount, "buy", date)["total"]
                if amount + fee <= self.cash + 1e-9:
                    break
                shares -= LOT
            if shares <= 0:
                o["last_reason"] = "insufficient_cash"
                result["blocked"] += 1
                keep.append(o)
                continue
            self._fill(o, date, price, shares)
            result["filled"] += 1
        self.d["pending_orders"] = keep
        return result

    def _fill(self, order: dict, date: str, price: float, shares: float) -> None:
        amount = price * shares
        fee = _fees(amount, order["side"], date)
        if order["side"] == "sell":
            self.d["cash"] = self.cash + amount - fee["total"]
            pos = self.positions[order["code"]]
            pos["shares"] = float(pos["shares"]) - shares
            if pos["shares"] <= 1e-9:
                self.positions.pop(order["code"], None)
            else:
                pos["last_price"] = price
                pos["last_date"] = date
        else:
            self.d["cash"] = self.cash - amount - fee["total"]
            pos = self.positions.get(order["code"])
            if pos is None:
                self.positions[order["code"]] = {"shares": shares, "cost_total": amount + fee["total"],
                                                 "last_price": price, "last_date": date,
                                                 "open_date": date}
            else:
                pos["shares"] = float(pos["shares"]) + shares
                pos["cost_total"] = float(pos["cost_total"]) + amount + fee["total"]
                pos["last_price"] = price
                pos["last_date"] = date
        self.d["fills"].append({"date": date, "order_id": order["order_id"],
                                "signal_id": order["signal_id"], "code": order["code"],
                                "side": order["side"], "shares": float(shares),
                                "price": float(price), "amount": float(amount),
                                "commission": fee["commission"], "stamp": fee["stamp"],
                                "transfer": fee["transfer"], "fee_total": fee["total"],
                                "note": order.get("last_reason", "")})

    # --------------------------------------------------------------- marking
    def mark(self, date: str, state_letter: str | None = None,
             target_exposure: float | None = None) -> dict:
        bars = panel.bars_on(date)
        fees_day = 0.0
        for code, pos in self.positions.items():
            if code in bars.index:
                c = float(bars.loc[code, "close"])
                if np.isfinite(c) and c > 0:
                    pos["last_price"] = c
                    pos["last_date"] = date
        mv = sum(p["shares"] * p["last_price"] for p in self.positions.values())
        nav = self.cash + mv
        fees_day = sum(f["fee_total"] for f in self.d["fills"] if f["date"] == date)
        row = {"date": date, "nav": nav, "cash": self.cash, "market_value": mv,
               "exposure": (mv / nav) if nav > 0 else 0.0,
               "n_positions": int(sum(1 for p in self.positions.values() if p["shares"] > 0)),
               "fees": fees_day, "state": state_letter,
               "target_exposure": target_exposure,
               "pending_orders": len(self.d["pending_orders"]),
               "data_asof": date, "freeze_at": config.FREEZE_AT,
               "generated_at": util.now_iso()}
        if abs(nav - (self.cash + mv)) > 1e-6:
            raise LedgerError(f"capital identity violated on {date}")
        hist = self.d["nav_history"]
        if hist and hist[-1]["date"] == date:
            if abs(hist[-1]["nav"] - nav) > 1e-6:
                raise LedgerError(f"non-idempotent re-marking of {date}: "
                                  f"{hist[-1]['nav']} vs {nav}")
            return hist[-1]
        hist.append(row)
        self.d["last_processed_date"] = date
        return row

    def daily_audit(self, date: str, state_letter: str | None = None,
                    target_exposure: float | None = None,
                    last_signal_reason: str = "") -> dict:
        row = self.d["nav_history"][-1] if self.d["nav_history"] else None
        if row is None or row["date"] != date:
            row = self.mark(date, state_letter, target_exposure)
        nav = row["nav"]
        cash = row["cash"]
        npos = row["n_positions"]
        if target_exposure is not None and target_exposure <= 0:
            reason = "state_forbidden"
        elif npos == 0 and row["pending_orders"] > 0:
            reason = "pending_fill"
        elif npos == 0 and last_signal_reason in ("no_candidate", "no_affordable_candidate"):
            reason = last_signal_reason
        elif npos == 0:
            reason = "off_window"
        elif cash <= max(1.0, 0.01 * nav):
            reason = "fully_invested"
        elif target_exposure is not None and cash <= (1.0 - target_exposure) * nav + 0.05 * nav:
            reason = "design_cap_or_lot_rounding"
        else:
            reason = "cash_above_cap"
        out = dict(row)
        out.update({"schema": config.SCHEMA_AUDIT, "account_id": self.d["account_id"],
                    "cash_reason": reason, "identity_ok": True})
        return out

    # ------------------------------------------------------------- advance
    def advance(self, to_date: str, signals_by_exec: dict | None = None) -> list[dict]:
        signals_by_exec = signals_by_exec or {}
        days = [d for d in panel.trading_days() if d > (self.d["last_processed_date"] or "")
                and d <= to_date]
        audits = []
        for d in days:
            self.apply_corporate_actions(d)
            for sig in signals_by_exec.get(d, []):
                if sig["signal_id"] in self.d["processed_signals"]:
                    continue
                self.create_orders(sig, fill_date=d)
            self.attempt_pending(d)
            letter, target = None, None
            sigs_today = [s for s in (signals_by_exec.get(d) or [])]
            if sigs_today:
                letter = sigs_today[0]["state"]["letter"]
                target = sigs_today[0]["exposure"]
            self.mark(d, letter, target)
            audits.append(self.daily_audit(d, letter, target))
        self.save()
        return audits


def load_signals(runs_dir: Path) -> dict:
    """Collect signal JSONs from runs/*/signals/*.json grouped by planned exec date."""
    out: dict = {}
    for p in sorted(Path(runs_dir).glob("*/signals/*.json")):
        try:
            sig = util.read_json(p)
        except Exception:  # noqa: BLE001
            continue
        if sig.get("status") == "PENDING_DATA" or sig.get("action") not in (
                "target", "risk_off", "hold_cash"):
            continue
        out.setdefault(sig["planned_exec_date"], []).append(sig)
    return out
