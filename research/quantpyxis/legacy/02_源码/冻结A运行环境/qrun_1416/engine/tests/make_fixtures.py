"""Generate the synthetic data-contract fixtures used by engine/tests/.

Run:  python engine/tests/make_fixtures.py
Writes plain CSV files (the loader accepts .csv as well as .csv.gz, same column
layout as the frozen data contract) under engine/tests/fixtures/toy and
.../fees2023.

The toy market is deterministic and contains hand-crafted events:
  * 600001 Alpha        normal control stock
  * 600002 Beta         cash dividend 0.50 on 2020-11-16 (reference preclose 9.50)
  * 600003 Gamma        limit-up day 2020-09-15 (open == limit-up price)
  * 600004 Delta        limit-down day 2020-10-20 (open == limit-down price)
  * 600005 Suspender    missing bars 2020-08-03..2020-08-14 (suspension carry)
  * 600006 Suspender2   tradestatus=0 rows 2020-09-01..2020-09-10
  * 600007 Ststock      isst=1 all days (not buyable by default)
  * 600008 Ipo          first bar 2021-01-04 (IPO-day trade blocked by default)
  * 600009 Delisted     bars end 2021-02-26, out_date 2021-03-01 (forced liquidation)
  * 600010 Divsplit     10-for-10 share split 2021-05-10 (ref preclose 10.00)
  * 300001 Chinext      +10% limit-up day 2020-07-15 (pre-reform), +15% day 2021-03-10
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
COLS = ["code", "date", "open", "high", "low", "close", "preclose", "volume",
        "amount", "turn", "pctchg", "isst", "tradestatus", "adj"]


def weekdays(start: str, end: str) -> list:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, end)]


class Spec:
    """Deterministic stock path spec with explicit event overrides."""

    def __init__(self, code, dates, p0, phase=0.0, amp=0.004, drift=0.00015,
                 isst=0, start_date=None, end_date=None, skip_dates=()):
        self.code = code
        self.dates = dates
        self.p0 = p0
        self.phase = phase
        self.amp = amp
        self.drift = drift
        self.isst = isst
        self.start_date = start_date
        self.end_date = end_date
        self.skip_dates = set(skip_dates)
        self.events: dict[str, dict] = {}

    def event(self, date: str, **kw):
        self.events[date] = kw

    def build(self):
        rows, total_returns = [], {}
        close_prev = self.p0
        t = 0
        for date in self.dates:
            if self.start_date and date < self.start_date:
                continue
            if self.end_date and date > self.end_date:
                continue
            if date in self.skip_dates:
                continue
            ev = self.events.get(date)
            if ev is not None:
                if ev.get("suspend"):
                    open_ = high = low = close = round(close_prev, 2)
                    preclose = close_prev
                    volume = 0
                    tradestatus = 0
                    tr = 1.0
                else:
                    open_ = float(ev["open"])
                    close = float(ev["close"])
                    high = float(ev.get("high", max(open_, close)))
                    low = float(ev.get("low", min(open_, close)))
                    preclose = float(ev.get("preclose", close_prev))
                    volume = float(ev.get("volume", 100000))
                    tradestatus = int(ev.get("tradestatus", 1))
                    if "total_return" in ev:
                        tr = float(ev["total_return"])
                    else:
                        tr = (close + float(ev.get("dividend", 0.0))) / close_prev
            else:
                open_ = round(close_prev * (1.0 + self.amp * np.sin(t * 0.13 + self.phase)
                                            + self.drift), 2)
                close = round(open_ * (1.0 + 0.8 * self.amp * np.cos(t * 0.11 + self.phase)), 2)
                high = round(max(open_, close) * 1.002, 2)
                low = round(min(open_, close) * 0.998, 2)
                preclose = close_prev
                volume = 100000 + (t * 37) % 5000
                tradestatus = 1
                tr = close / close_prev
            rows.append({
                "code": self.code, "date": date, "open": open_, "high": high, "low": low,
                "close": close, "preclose": preclose, "volume": volume,
                "amount": round(volume * close, 2), "turn": round(0.01 + 0.0001 * t, 6),
                "pctchg": round((close / preclose - 1.0) * 100, 4) if preclose else 0.0,
                "isst": int(self.isst), "tradestatus": tradestatus, "adj": 1.0,
            })
            total_returns[date] = tr
            if tradestatus == 1:
                close_prev = close
            t += 1
        return rows, total_returns


def with_qfq(rows, total_returns):
    """Build the forward-adjusted sibling file from the total-return factors."""
    out = []
    qfq_prev = None
    for r in rows:
        tr = total_returns.get(r["date"], 1.0)
        if qfq_prev is None:
            qfq_close = float(r["close"])
            qfq_prev = qfq_close
        else:
            qfq_close = qfq_prev * tr
        qfq_open = qfq_close * (r["open"] / r["close"]) if r["close"] > 0 else qfq_close
        out.append({
            "code": r["code"], "date": r["date"],
            "open": round(qfq_open, 6),
            "high": round(qfq_close * r["high"] / r["close"], 6) if r["close"] else qfq_close,
            "low": round(qfq_close * r["low"] / r["close"], 6) if r["close"] else qfq_close,
            "close": round(qfq_close, 6),
            "preclose": round(qfq_prev, 6) if qfq_prev else round(qfq_close, 6),
            "volume": r["volume"], "amount": r["amount"], "turn": r["turn"],
            "pctchg": r["pctchg"], "isst": r["isst"],
            "tradestatus": r["tradestatus"], "adj": 1.0,
        })
        qfq_prev = qfq_close
    return out


def _write_meta(out_dir: Path, universe, raw, idx_rows, cal_start, cal_end):
    out_dir = Path(out_dir)
    (out_dir / "meta").mkdir(parents=True, exist_ok=True)
    (out_dir / "panel").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(universe, columns=["code", "exchange", "board", "name", "ipo_date",
                                    "out_date", "status"]).to_csv(
        out_dir / "meta" / "universe.csv", index=False)
    codes = sorted(raw["code"].unique())
    pd.DataFrame({"code": codes, "id": range(len(codes))}).to_csv(
        out_dir / "meta" / "codes.csv", index=False)
    pd.DataFrame(idx_rows).to_csv(out_dir / "panel" / "index_daily.csv", index=False)
    cal = pd.bdate_range(cal_start, cal_end)
    pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in cal], "is_open": 1}).to_csv(
        out_dir / "meta" / "calendar.csv", index=False)


def _year_slice(df: pd.DataFrame, year: int) -> pd.DataFrame:
    return df[df["date"].str[:4] == str(year)]


def build_toy(out_dir: Path):
    dates = weekdays("2020-06-01", "2021-12-31")
    specs = []

    def flat(code, p0):
        """Constant-price stock so event days are numerically clean for tests."""
        return Spec(code, dates, p0, phase=0.0, amp=0.0, drift=0.0)

    # Alpha: plain moving control stock
    a = Spec("600001", dates, 10.00, phase=0.0)
    specs.append(("main", a, "2010-01-01", ""))

    # Beta: constant 10.00, cash dividend 0.50 ex-date 2020-11-16 (ref preclose 9.50)
    b = flat("600002", 10.00)
    b.event("2020-11-16", open=9.45, close=9.60, high=9.70, low=9.40,
            preclose=9.50, total_return=(9.60 + 0.50) / 10.00, volume=120000)
    specs.append(("main", b, "2010-01-01", ""))

    # Gamma: constant 10.00, limit-up day 2020-09-15
    g = flat("600003", 10.00)
    g.event("2020-09-15", open=11.00, close=11.00, high=11.00, low=11.00,
            preclose=10.00, total_return=1.10, volume=1000)
    specs.append(("main", g, "2010-01-01", ""))

    # Delta: constant 10.00, limit-down day 2020-10-20
    d = flat("600004", 10.00)
    d.event("2020-10-20", open=9.00, close=9.00, high=9.00, low=9.00,
            preclose=10.00, total_return=0.90, volume=1000)
    specs.append(("main", d, "2010-01-01", ""))

    # Suspender: missing bars 2020-08-03..2020-08-14
    skip = set(dates[dates.index("2020-08-03"): dates.index("2020-08-14") + 1])
    s1 = Spec("600005", dates, 12.00, phase=0.3, skip_dates=skip)
    specs.append(("main", s1, "2010-01-01", ""))

    # Suspender2: tradestatus=0 rows 2020-09-01..2020-09-10
    s2 = Spec("600006", dates, 8.00, phase=0.7)
    for dt in dates[dates.index("2020-09-01"): dates.index("2020-09-10") + 1]:
        s2.event(dt, suspend=True)
    specs.append(("main", s2, "2010-01-01", ""))

    # ST stock
    st = Spec("600007", dates, 6.00, phase=1.4, isst=1)
    specs.append(("main", st, "2010-01-01", ""))

    # IPO stock: first bar 2021-01-04
    ipo = Spec("600008", dates, 15.00, phase=2.2, start_date="2021-01-04")
    ipo.event("2021-01-04", open=18.00, close=19.00, high=20.00, low=17.00,
              preclose=15.00, total_return=19.00 / 15.00, volume=500000)
    specs.append(("main", ipo, "2021-01-04", ""))

    # Delisted: bars end 2021-02-26, out_date 2021-03-01
    dl = Spec("600009", dates, 9.00, phase=0.9, end_date="2021-02-26")
    specs.append(("main", dl, "2010-01-01", "2021-03-01"))

    # Constant 20.00 then 10-for-10 split on 2021-05-10 (ref preclose 10.00)
    ds = flat("600010", 20.00)
    ds.event("2021-05-10", open=10.05, close=10.20, high=10.30, low=9.90,
             preclose=10.00, total_return=1.02, volume=400000)
    specs.append(("main", ds, "2010-01-01", ""))

    # ChiNext: constant 10.00; pre-reform 10% limit-up 2020-07-15, post-reform +15% 2021-03-10
    cn = flat("300001", 10.00)
    cn.event("2020-07-15", open=11.00, close=11.00, high=11.00, low=11.00,
             preclose=10.00, total_return=1.10, volume=1000)
    cn.event("2021-03-10", open=11.50, close=11.60, high=11.70, low=11.40,
             preclose=10.00, total_return=1.16, volume=200000)
    specs.append(("chinext", cn, "2010-01-01", ""))

    raw_rows, qfq_rows, universe = [], [], []
    for board, spec, ipo_date, out_date in specs:
        rows, trs = spec.build()
        raw_rows.extend(rows)
        qfq_rows.extend(with_qfq(rows, trs))
        universe.append({"code": spec.code,
                         "exchange": "sh" if spec.code.startswith("6") else "sz",
                         "board": board, "name": spec.code, "ipo_date": ipo_date,
                         "out_date": out_date, "status": "listed"})

    # index series
    idx_rows = []
    for code, p0, ph in (("sh000300", 3800.0, 0.0), ("sz399006", 2400.0, 0.4)):
        close_prev = p0
        for t, date in enumerate(dates):
            close = round(close_prev * (1 + 0.0025 * np.sin(t * 0.05 + ph)), 2)
            open_ = round(close_prev * (1 + 0.001 * np.cos(t * 0.07 + ph)), 2)
            idx_rows.append({"index_code": code, "date": date, "open": open_,
                             "high": round(max(open_, close) * 1.003, 2),
                             "low": round(min(open_, close) * 0.997, 2),
                             "close": close, "preclose": close_prev,
                             "volume": 1e8, "amount": 1e11})
            close_prev = close

    raw = pd.DataFrame(raw_rows, columns=COLS)
    qfq = pd.DataFrame(qfq_rows, columns=COLS)
    raw = raw.sort_values(["code", "date"]).reset_index(drop=True)
    qfq = qfq.sort_values(["code", "date"]).reset_index(drop=True)
    out = Path(out_dir)
    (out / "panel").mkdir(parents=True, exist_ok=True)
    for year in (2020, 2021):
        _year_slice(raw, year).to_csv(out / "panel" / f"bars_{year}.csv", index=False)
        _year_slice(qfq, year).to_csv(out / "panel" / f"bars_qfq_{year}.csv", index=False)
    _write_meta(out, universe, raw, idx_rows, "2020-06-01", "2021-12-31")
    manifest = {"contract": "v1", "fixture": True,
                "generated_by": "engine/tests/make_fixtures.py",
                "calendar": "meta/calendar.csv", "universe": "meta/universe.csv",
                "files": [f"panel/bars_{y}.csv" for y in (2020, 2021)] +
                         [f"panel/bars_qfq_{y}.csv" for y in (2020, 2021)] +
                         ["panel/index_daily.csv", "meta/calendar.csv",
                          "meta/universe.csv", "meta/codes.csv"]}
    (out / "meta" / "manifest.json").write_text(json.dumps(manifest, indent=1),
                                                encoding="utf-8")


def build_fees2023(out_dir: Path):
    dates = weekdays("2023-06-01", "2023-09-15")
    alpha = Spec("600001", dates, 10.00, phase=0.0)
    rows, trs = alpha.build()
    qfq_rows = with_qfq(rows, trs)
    out = Path(out_dir)
    (out / "panel").mkdir(parents=True, exist_ok=True)
    raw = pd.DataFrame(rows, columns=COLS).sort_values(["code", "date"]).reset_index(drop=True)
    qfq = pd.DataFrame(qfq_rows, columns=COLS).sort_values(["code", "date"]).reset_index(drop=True)
    raw.to_csv(out / "panel" / "bars_2023.csv", index=False)
    qfq.to_csv(out / "panel" / "bars_qfq_2023.csv", index=False)
    universe = [{"code": "600001", "exchange": "sh", "board": "main", "name": "600001",
                 "ipo_date": "2010-01-01", "out_date": "", "status": "listed"}]
    idx_rows = []
    close_prev = 3800.0
    for t, date in enumerate(dates):
        close = round(close_prev * (1 + 0.001 * np.sin(t * 0.05)), 2)
        idx_rows.append({"index_code": "sh000300", "date": date,
                         "open": close_prev, "high": max(close_prev, close),
                         "low": min(close_prev, close), "close": close,
                         "preclose": close_prev, "volume": 1e8, "amount": 1e11})
        close_prev = close
    _write_meta(out, universe, raw, idx_rows, "2023-06-01", "2023-09-15")


def main():
    toy = FIXTURES / "toy"
    fees = FIXTURES / "fees2023"
    build_toy(toy)
    build_fees2023(fees)
    print(f"fixtures written to {FIXTURES}")
    for d in (toy, fees):
        files = [p for p in d.rglob("*") if p.is_file()]
        size = sum(p.stat().st_size for p in files)
        print(f"  {d.name}: {len(files)} files, {size/1024:.1f} KB")


if __name__ == "__main__":
    main()
