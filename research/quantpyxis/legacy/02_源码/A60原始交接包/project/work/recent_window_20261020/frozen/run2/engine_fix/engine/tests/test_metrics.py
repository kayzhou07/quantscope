"""Metric-correctness tests on crafted NAV / trade series."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine import metrics as M  # noqa: E402


def crafted_nav():
    return pd.Series(
        [100.0, 110.0, 99.0, 121.0, 120.0],
        index=["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"],
    )


class TestReturnMetrics(unittest.TestCase):
    def test_total_return(self):
        self.assertAlmostEqual(M.total_return(crafted_nav()), 0.20, places=12)

    def test_cagr_uses_252_day_year(self):
        nav = pd.Series([100.0] * 251 + [110.0],
                        index=[str(pd.Timestamp("2020-01-01") + pd.Timedelta(days=i))
                               for i in range(252)])
        expected = (1.10) ** (252 / 252) - 1.0
        self.assertAlmostEqual(M.cagr(nav), expected, places=6)

    def test_annualized_vol_and_sharpe(self):
        nav = crafted_nav()
        ret = nav.pct_change(fill_method=None).dropna()
        vol = float(ret.std(ddof=1) * np.sqrt(252))
        sharpe = float(ret.mean() / ret.std(ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(M.annualized_vol(ret), vol, places=12)
        self.assertAlmostEqual(M.sharpe(ret), sharpe, places=12)

    def test_zero_vol_sharpe_is_zero(self):
        nav = pd.Series([100.0, 100.0, 100.0],
                        index=["2020-01-02", "2020-01-03", "2020-01-06"])
        self.assertEqual(M.annualized_vol(nav.pct_change(fill_method=None).dropna()), 0.0)
        self.assertEqual(M.sharpe(nav.pct_change(fill_method=None).dropna()), 0.0)


class TestDrawdown(unittest.TestCase):
    def test_max_drawdown_peak_trough(self):
        dd = M.max_drawdown(crafted_nav())
        self.assertAlmostEqual(dd["max_drawdown"], 99.0 / 110.0 - 1.0, places=12)
        self.assertEqual(dd["peak_date"], "2020-01-03")
        self.assertEqual(dd["trough_date"], "2020-01-06")
        self.assertIsNotNone(dd["duration_days"])

    def test_drawdown_series(self):
        dd = M.drawdown_series(crafted_nav())
        self.assertAlmostEqual(float(dd.loc["2020-01-06"]), 99.0 / 110.0 - 1.0, places=12)
        self.assertEqual(float(dd.loc["2020-01-03"]), 0.0)

    def test_drawdown_no_drawdown(self):
        nav = pd.Series([100.0, 101.0, 102.0],
                        index=["2020-01-02", "2020-01-03", "2020-01-06"])
        dd = M.max_drawdown(nav)
        self.assertEqual(dd["max_drawdown"], 0.0)
        self.assertIsNone(dd["peak_date"])


class TestMonthly(unittest.TestCase):
    def test_monthly_returns(self):
        nav = pd.Series(
            [100.0, 110.0, 120.0, 90.0],
            index=["2020-01-02", "2020-01-31", "2020-02-03", "2020-02-28"],
        )
        monthly = M.monthly_returns(nav, capital=100.0)
        self.assertEqual(len(monthly), 2)
        # January month-end NAV is 110 (the 120 print is on 2020-02-03)
        self.assertAlmostEqual(monthly.iloc[0]["ret"], 0.10, places=12)
        self.assertAlmostEqual(monthly.iloc[1]["ret"], 90.0 / 110.0 - 1.0, places=12)
        mat = M.monthly_matrix(monthly)
        self.assertAlmostEqual(mat.loc[2020, "Jan"], 0.10, places=12)

    def test_monthly_empty(self):
        self.assertEqual(len(M.monthly_returns(pd.Series(dtype=float))), 0)


class TestTailAndExposure(unittest.TestCase):
    def test_tail_stats(self):
        tail = M.tail_stats(crafted_nav())
        self.assertAlmostEqual(tail["worst_day"], 99.0 / 110.0 - 1.0, places=12)
        self.assertEqual(tail["worst_day_date"], "2020-01-06")
        self.assertAlmostEqual(tail["best_day"], 121.0 / 99.0 - 1.0, places=12)
        self.assertLessEqual(tail["var95_daily"], 0.0)

    def test_exposure_stats(self):
        nav_df = pd.DataFrame({
            "nav": [100.0, 100.0, 100.0, 100.0],
            "exposure": [0.0, 0.5, 1.0, 0.0],
            "n_positions": [0, 1, 2, 0],
        }, index=["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
        ex = M.exposure_stats(nav_df)
        self.assertAlmostEqual(ex["avg_exposure"], 0.375, places=12)
        self.assertAlmostEqual(ex["end_exposure"], 0.0, places=12)
        self.assertEqual(ex["all_cash_days"], 2)
        self.assertAlmostEqual(ex["avg_positions"], 0.75, places=12)


class TestFifo(unittest.TestCase):
    def test_fifo_simple_round_trip(self):
        trades = pd.DataFrame([
            {"date": "2020-01-02", "code": "600000", "side": "buy", "shares": 100,
             "units": 0.0, "amount": 1000.0, "fee_total": 5.0},
            {"date": "2020-01-06", "code": "600000", "side": "sell", "shares": 100,
             "units": 0.0, "amount": 1100.0, "fee_total": 6.0},
        ])
        fifo = M.fifo_pnl(trades)
        # (1100 - 6) - (1000 + 5)
        self.assertAlmostEqual(fifo["realized_pnl"], 89.0, places=9)
        self.assertEqual(fifo["win_count"], 1)
        self.assertEqual(fifo["loss_count"], 0)
        self.assertEqual(fifo["open_units"], 0.0)
        self.assertAlmostEqual(fifo["open_cost"], 0.0, places=9)

    def test_fifo_adjust_lot_is_zero_cost(self):
        trades = pd.DataFrame([
            {"date": "2020-01-02", "code": "600000", "side": "buy", "shares": 100,
             "units": 0.0, "amount": 1000.0, "fee_total": 5.0},
            {"date": "2020-06-01", "code": "600000", "side": "adjust", "shares": 100,
             "units": 0.0, "amount": 0.0, "fee_total": 0.0},
            {"date": "2020-09-01", "code": "600000", "side": "sell", "shares": 200,
             "units": 0.0, "amount": 1800.0, "fee_total": 5.0},
        ])
        fifo = M.fifo_pnl(trades)
        # net proceeds 1795 - cost 1005 = 790
        self.assertAlmostEqual(fifo["realized_pnl"], 790.0, places=9)
        self.assertEqual(fifo["n_adjust_events"], 1)

    def test_fifo_open_lot(self):
        trades = pd.DataFrame([
            {"date": "2020-01-02", "code": "600000", "side": "buy", "shares": 100,
             "units": 0.0, "amount": 1000.0, "fee_total": 5.0},
        ])
        fifo = M.fifo_pnl(trades)
        self.assertEqual(fifo["realized_pnl"], 0.0)
        self.assertAlmostEqual(fifo["open_units"], 100.0)
        self.assertAlmostEqual(fifo["open_cost"], 1005.0)


class TestRegimeSegmentsAndBenchmark(unittest.TestCase):
    def test_regime_segments(self):
        nav = pd.Series(
            [100.0, 101.0, 100.0, 102.0],
            index=["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"],
        )
        nav_df = pd.DataFrame({"nav": nav, "ret": nav.pct_change(fill_method=None).fillna(0.0),
                               "exposure": [1.0, 1.0, 1.0, 1.0]})
        nav_df["regime"] = ["bull", "bull", "bear", "bear"]
        seg = M.regime_segments(nav_df)
        self.assertEqual(len(seg), 2)
        bull = seg[seg["regime"] == "bull"].iloc[0]
        self.assertEqual(int(bull["days"]), 2)
        self.assertAlmostEqual(float(bull["segment_return"]), 101.0 / 100.0 - 1.0, places=9)
        bear = seg[seg["regime"] == "bear"].iloc[0]
        self.assertAlmostEqual(float(bear["segment_return"]), 102.0 / 101.0 - 1.0, places=9)

    def test_benchmark_compare_identical(self):
        nav = crafted_nav()
        bench = {"sh000300": nav.copy()}
        cmp = M.benchmark_compare(nav, bench)
        self.assertEqual(len(cmp), 1)
        row = cmp.iloc[0]
        self.assertAlmostEqual(float(row["beta"]), 1.0, places=9)
        self.assertAlmostEqual(float(row["corr"]), 1.0, places=9)
        self.assertAlmostEqual(float(row["excess_total_return"]), 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
