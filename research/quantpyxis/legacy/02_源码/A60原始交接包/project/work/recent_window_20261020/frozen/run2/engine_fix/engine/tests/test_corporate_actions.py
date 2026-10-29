"""Corporate-action tests: qfq total-return valuation, dividends, splits."""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_utils import load_toy_bundle, run_toy, trades_of  # noqa: E402

from engine.api import BuyHold  # noqa: E402


def position_value_series(result, code):
    pos = result["positions"]
    pos = pos[(pos["code"] == code) & (pos["kind"] == "stock")].copy()
    return pos.set_index("date")["value"].astype(float).sort_index()


def qfq_ratio_series(bundle, code, dates):
    j = bundle.code_index[code]
    qfq = bundle.field("qfq_close")[:, j]
    idx = [bundle.i_of(d) for d in dates]
    return qfq[idx]


class TestDividendTotalReturn(unittest.TestCase):
    def test_dividend_event_and_implicit_reinvestment(self):
        r = run_toy(BuyHold("600002"))
        ev = r["events"]
        corp = ev[ev["type"] == "corporate_action"]
        self.assertEqual(len(corp), 1)
        row = corp.iloc[0]
        self.assertEqual(str(row["date"])[:10], "2020-11-16")
        self.assertGreater(float(row["multiplier"]), 1.02)
        self.assertIn(str(row["source"]), ("preclose_reference", "qfq_ratio"))
        # ~9,900 shares * 0.50 dividend -> ~5,000 CNY implicitly reinvested
        self.assertGreater(r["total_implicit_dividends"], 4500.0)
        self.assertLess(r["total_implicit_dividends"], 5600.0)
        # adjustment rows are visible to the FIFO/trade accounting
        adj = trades_of(r, code="600002")
        adj = adj[adj["side"] == "adjust"]
        self.assertEqual(len(adj), 1)

    def test_nav_total_return_matches_qfq_across_dividend(self):
        r = run_toy(BuyHold("600002"))
        pos = position_value_series(r, "600002")
        dates = list(pos.index)
        bundle = load_toy_bundle()
        qfq = qfq_ratio_series(bundle, "600002", dates)
        base_v, base_q = pos.iloc[0], qfq[0]
        rel = np.abs((pos.to_numpy() / base_v) / (qfq / base_q) - 1.0)
        self.assertLess(float(rel.max()), 0.005,
                        "qfq-based total-return valuation should track the qfq series "
                        "within the documented approximation (<0.5%)")

    def test_dividend_creates_no_price_gap_in_valuation(self):
        r = run_toy(BuyHold("600002"))
        nav = r["nav"]["nav"]
        bundle = load_toy_bundle()
        i_ex = bundle.i_of("2020-11-16")
        prev = bundle.date_at(i_ex - 1)
        ratio = float(nav.loc["2020-11-16"] / nav.loc[prev])
        # total return at the ex-date is +1.0% (market move) with the dividend reinvested
        self.assertGreater(ratio, 1.002)
        self.assertLess(ratio, 1.02)

    def test_dividend_mode_none_shows_price_only_return(self):
        r = run_toy(BuyHold("600002"), dividend_mode="none")
        ev = r["events"]
        if len(ev):
            self.assertEqual(len(ev[ev["type"] == "corporate_action"]), 0)
        pos = position_value_series(r, "600002")
        ratio = float(pos.loc["2020-11-17"] / pos.loc["2020-11-13"])
        self.assertLess(ratio, 0.98)   # raw price dropped 10.00 -> 9.60
        self.assertEqual(r["total_implicit_dividends"], 0.0)


class TestSplitAdjustment(unittest.TestCase):
    def test_ten_for_ten_split_multiplies_shares(self):
        r = run_toy(BuyHold("600010"))
        corp = r["events"][r["events"]["type"] == "corporate_action"]
        self.assertEqual(len(corp), 1)
        self.assertAlmostEqual(float(corp.iloc[0]["multiplier"]), 2.0, places=3)
        shares = r["holdings_final"]["600010"]["shares"]
        self.assertAlmostEqual(float(shares), 9800.0, places=2)   # 4,900 * 2
        # a split is not a dividend: implicit dividend accounting must stay ~0
        self.assertLess(r["total_implicit_dividends"], 1.0)
        self.assertGreater(r["total_corporate_action_value"], 40000.0)

    def test_no_spurious_corporate_actions_on_normal_stock(self):
        r = run_toy(BuyHold("600001"))
        ev = r["events"]
        self.assertTrue(len(ev[ev["type"] == "corporate_action"]) == 0)


class TestQfqConsistency(unittest.TestCase):
    def test_normal_stock_valuation_tracks_qfq(self):
        r = run_toy(BuyHold("600001"))
        pos = position_value_series(r, "600001")
        bundle = load_toy_bundle()
        qfq = qfq_ratio_series(bundle, "600001", list(pos.index))
        rel = np.abs((pos.to_numpy() / pos.iloc[0]) / (qfq / qfq[0]) - 1.0)
        self.assertLess(float(rel.max()), 1e-3)

    def test_split_valuation_continuity(self):
        r = run_toy(BuyHold("600010"))
        pos = position_value_series(r, "600010")
        before = float(pos.loc["2021-05-07"])
        after = float(pos.loc["2021-05-10"])
        self.assertAlmostEqual(after / before, 1.02, places=3)


if __name__ == "__main__":
    unittest.main()
