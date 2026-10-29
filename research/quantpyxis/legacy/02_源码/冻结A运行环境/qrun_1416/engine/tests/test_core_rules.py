"""Unit tests for the pure A-share rule functions (fees, limits, rounding)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.core import (  # noqa: E402
    FeeModel,
    limit_prices,
    limit_rates,
    round_cny,
    round_lot,
    stamp_tax_rate,
)


class TestRounding(unittest.TestCase):
    def test_round_cny_half_up(self):
        self.assertEqual(round_cny(1.005), 1.01)
        self.assertEqual(round_cny(1.004), 1.00)
        self.assertEqual(round_cny(2.675), 2.68)
        self.assertEqual(round_cny(0.0), 0.0)

    def test_round_lot_floors_to_100(self):
        self.assertEqual(round_lot(99), 0)
        self.assertEqual(round_lot(100), 100)
        self.assertEqual(round_lot(199.9), 100)
        self.assertEqual(round_lot(12345.6), 12300)
        self.assertEqual(round_lot(-5), 0)
        self.assertEqual(round_lot(float("nan")), 0)


class TestLimitRules(unittest.TestCase):
    def test_main_board_10pct(self):
        self.assertEqual(limit_rates("2020-01-02", "main", False), (0.10, 0.10))
        self.assertEqual(limit_prices(10.00, "2020-01-02", "main"), (11.00, 9.00))
        # 2-decimal exchange-style rounding
        self.assertEqual(limit_prices(9.87, "2020-01-02", "main"), (10.86, 8.88))

    def test_st_5pct(self):
        self.assertEqual(limit_rates("2020-01-02", "main", True), (0.05, 0.05))
        self.assertEqual(limit_prices(10.00, "2020-01-02", "main", True), (10.50, 9.50))

    def test_chinext_reform_switch(self):
        # 10% before the 2020-08-24 reform, 20% afterwards
        self.assertEqual(limit_rates("2020-08-21", "chinext", False), (0.10, 0.10))
        self.assertEqual(limit_rates("2020-08-24", "chinext", False), (0.20, 0.20))
        self.assertEqual(limit_prices(10.00, "2020-08-24", "chinext"), (12.00, 8.00))
        # pre-reform ChiNext ST used 5%
        self.assertEqual(limit_rates("2020-06-01", "chinext", True), (0.05, 0.05))
        # post-reform ChiNext ST moves 20%
        self.assertEqual(limit_rates("2021-06-01", "chinext", True), (0.20, 0.20))

    def test_stamp_tax_switch(self):
        self.assertAlmostEqual(stamp_tax_rate("2023-08-25"), 0.0010)
        self.assertAlmostEqual(stamp_tax_rate("2023-08-27"), 0.0010)
        self.assertAlmostEqual(stamp_tax_rate("2023-08-28"), 0.0005)
        self.assertAlmostEqual(stamp_tax_rate("2024-01-02"), 0.0005)


class TestFees(unittest.TestCase):
    def setUp(self):
        self.fees = FeeModel()

    def test_buy_fees_small(self):
        # 10,000 CNY: commission max(2.5, 5) = 5, transfer 0.1, no stamp
        f = self.fees.fees("buy", 10_000.0, "2023-08-25")
        self.assertAlmostEqual(f["commission"], 5.0)
        self.assertAlmostEqual(f["stamp"], 0.0)
        self.assertAlmostEqual(f["transfer"], 0.1)
        self.assertAlmostEqual(f["total"], 5.1)

    def test_sell_fees_before_stamp_switch(self):
        f = self.fees.fees("sell", 10_000.0, "2023-08-25")
        self.assertAlmostEqual(f["commission"], 5.0)
        self.assertAlmostEqual(f["stamp"], 10.0)      # 0.1%
        self.assertAlmostEqual(f["transfer"], 0.1)
        self.assertAlmostEqual(f["total"], 15.1)

    def test_sell_fees_from_stamp_switch(self):
        f = self.fees.fees("sell", 10_000.0, "2023-08-28")
        self.assertAlmostEqual(f["stamp"], 5.0)       # 0.05%
        self.assertAlmostEqual(f["total"], 10.1)

    def test_min_commission_on_tiny_trade(self):
        f = self.fees.fees("buy", 1_000.0, "2024-01-02")
        self.assertAlmostEqual(f["commission"], 5.0)
        self.assertAlmostEqual(f["transfer"], 0.01)

    def test_commission_bps_on_large_trade(self):
        f = self.fees.fees("buy", 1_000_000.0, "2024-01-02")
        self.assertAlmostEqual(f["commission"], 250.0)   # 2.5bp
        self.assertAlmostEqual(f["transfer"], 10.0)      # 0.001%
        self.assertAlmostEqual(f["total"], 260.0)
        f2 = self.fees.fees("sell", 1_000_000.0, "2024-01-02")
        self.assertAlmostEqual(f2["stamp"], 500.0)       # 0.05%
        self.assertAlmostEqual(f2["total"], 760.0)


if __name__ == "__main__":
    unittest.main()
